"""Deadline, trace, process and filesystem boundaries for generation."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Any, Callable

from .safety import sanitize


class DeadlineExceeded(RuntimeError):
    pass


class GenerationError(RuntimeError):
    pass


class Deadline:
    def __init__(self, seconds: float = 1800, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.start = clock()
        self.limit = self.start + seconds

    def remaining(self) -> float:
        return max(0.0, self.limit - self.clock())

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.start)

    def require(self, seconds: float = 0, reserve: float = 0) -> None:
        if self.remaining() <= seconds + reserve:
            raise DeadlineExceeded("The builder time budget is exhausted")

    def timeout(self, cap: float = 120, reserve: float = 0) -> float:
        self.require(reserve=reserve)
        return min(cap, self.remaining() - reserve)


class Trace:
    def __init__(self, path: Path, secrets: tuple[str, ...] = ()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = secrets

    def emit(self, event: str, **fields: Any) -> None:
        value = {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event, **fields}
        # Redact before persistence, including subprocess stderr and model events.
        value = sanitize(value, secrets=self.secrets)
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")


class FileLock:
    def __init__(self, path: Path, deadline: Deadline | None = None, reserve: float = 0):
        self.path = Path(path)
        self.deadline = deadline or Deadline()
        self.reserve = reserve
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+")
        try:
            while True:
                self.deadline.require(reserve=self.reserve)
                try:
                    fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except BlockingIOError:
                    time.sleep(self.deadline.timeout(0.1, reserve=self.reserve))
        except BaseException:
            self.file.close()
            raise

    def __exit__(self, *_):
        if self.file:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


# Strict structured outputs prohibit arbitrary object maps. The outer wire object
# therefore has two required JSON strings; local validators inspect their contents.
GENERATION_SCHEMA = {"type": "object", "additionalProperties": False,
                     "required": ["policy_json", "workflow_json"],
                     "properties": {"policy_json": {"type": "string", "description": "Serialized provenance policy JSON object"},
                                    "workflow_json": {"type": "string", "description": "Serialized complete Freya workflow JSON object"}}}


def plan_generation_schema(contract_sha256: str) -> dict:
    if not isinstance(contract_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", contract_sha256):
        raise GenerationError("Plan generation requires a validated policy contract fingerprint")
    return {"type": "object", "additionalProperties": False,
            "required": ["plan_json", "contract_sha256"],
            "properties": {"plan_json": {"type": "string", "description": "Serialized restricted dialogue/action plan JSON object"},
                           "contract_sha256": {"type": "string", "enum": [contract_sha256]}}}


def _strict_json(text: str) -> Any:
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("Duplicate JSON property")
            value[key] = child
        return value
    def invalid(_):
        raise ValueError("Nonfinite JSON value")
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


REQUIRED_FLAGS = ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema", "--strict-config")
DISABLED_FEATURES = ("shell_tool", "apps", "plugins", "remote_plugin", "hooks", "multi_agent", "memories",
                     "view_image", "browser_use", "computer_use", "skill_search", "skill_mcp_dependency_install",
                     "multi_agent_v2", "image_generation", "goals", "sleep_tool", "code_mode_host", "tool_suggest")


def _expected_cli_warning(message: Any) -> str | None:
    if not isinstance(message, str):
        return None
    if re.fullmatch(r"Under-development features enabled: skip_host_skill_discovery\. "
                    r"Under-development features are incomplete and may behave unpredictably\. "
                    r"To suppress this warning, set `suppress_unstable_features_warning = true` in [^\n]+config\.toml\.", message):
        return "skip_host_skill_discovery_is_experimental"
    if re.fullmatch(r"Exceeded skills context budget\. All skill descriptions were removed and \d+ additional skills "
                    r"were not included in the model-visible skills list\.", message):
        return "skill_descriptions_removed_by_context_budget"
    return None


def run_bounded(argv: list[str], *, input: str | None = None, text: bool = True, capture_output: bool = True,
                timeout: float, check: bool = False, on_progress: Callable | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Bound the process group, retaining drained output and optional buffered progress."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
                               start_new_session=True, **kwargs)
    started = time.monotonic()
    remaining, payload = timeout, input
    while True:
        interval = min(30, remaining) if on_progress else remaining
        try:
            stdout, stderr = process.communicate(input=payload, timeout=interval)
            break
        except subprocess.TimeoutExpired as exc:
            payload = None  # communicate has already submitted/closed stdin.
            elapsed = time.monotonic() - started
            if on_progress and interval < remaining and elapsed < timeout:
                try:
                    on_progress(elapsed, exc.output, exc.stderr)
                except BaseException:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.communicate(timeout=2)
                    raise
                remaining = timeout - elapsed
                continue
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            exc.process_group_terminated = True
            try:
                # communicate after a timeout returns the complete buffered streams,
                # including output emitted before the timeout. Do not discard them.
                stdout, stderr = process.communicate(timeout=2)
                exc.output, exc.stderr = stdout, stderr
            except subprocess.TimeoutExpired as drained:
                exc.output = drained.output if drained.output is not None else exc.output
                exc.stderr = drained.stderr if drained.stderr is not None else exc.stderr
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            exc.timeout = timeout
            raise exc
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, argv, output=stdout, stderr=stderr)
    return result


def generation_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Retain runtime/login locations, never bank or API secrets or shell state."""
    source = os.environ if environ is None else environ
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TERM", "SHELL", "CODEX_HOME"}
    return {k: v for k, v in source.items() if k in allowed}


class CodexGenerator:
    def __init__(self, output_dir: Path, trace: Trace, deadline: Deadline, executable: str = "codex",
                 model: str | None = "gpt-6.1-sol", run: Callable | None = None, reasoning_effort: str = "high"):
        self.output_dir, self.trace, self.deadline = Path(output_dir).resolve(), trace, deadline
        self.executable, self.model, self.run = executable, model, run or run_bounded
        self.reasoning_effort = reasoning_effort
        self._checked = False
        self._isolation_checked = False

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

    def _progress(self, elapsed: float, stdout: Any, stderr: Any) -> None:
        stdout, stderr = self._text(stdout), self._text(stderr)
        last_event = "none"
        allowed = {"thread.started", "turn.started", "item.started", "item.updated", "item.completed",
                   "turn.completed", "turn.failed", "error"}
        for line in reversed(stdout.splitlines()[-16:]):
            try:
                value = json.loads(line)
                event_type = value.get("type") if isinstance(value, dict) else None
                last_event = event_type if isinstance(event_type, str) and event_type in allowed else "unknown"
                break
            except json.JSONDecodeError:
                continue
        # Progress metadata is data-free; raw process text is persisted only after redaction.
        self.trace.emit("generation_progress", elapsed_seconds=round(elapsed, 2),
                        stdout_characters=len(stdout), stderr_characters=len(stderr), last_event=last_event,
                        remaining_seconds=round(self.deadline.remaining(), 2))

    def _record_output(self, stdout: Any, stderr: Any, *, partial: bool = False) -> None:
        stdout, stderr = self._text(stdout), self._text(stderr)
        # Sanitize BEFORE clipping: a clipped credential must not evade detection.
        clean_stdout = sanitize(stdout, secrets=self.trace.secrets)
        clean_stderr = sanitize(stderr, secrets=self.trace.secrets)
        limit = 256 * 1024
        truncated = len(clean_stdout) > limit
        if truncated:
            clean_stdout = clean_stdout[:limit // 2] + "\n[OUTPUT TRUNCATED]\n" + clean_stdout[-limit // 2:]
        self.trace.emit("generation_output_summary", partial=partial, stdout_characters=len(stdout),
                        stderr_characters=len(stderr), stdout_truncated=truncated,
                        stdout_retention_limit=limit, stderr_retention_limit=12000)
        self.trace.emit("generation_stderr", text=clean_stderr[-12000:], partial=partial)
        retained = 0
        for line in clean_stdout.splitlines():
            if retained >= limit:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                excerpt = line[:min(2000, limit - retained)]
                self.trace.emit("generation_output", text=excerpt, partial=partial)
                retained += len(excerpt)
                continue
            # Keep event shape and data-free state while bounding long agent messages.
            def bounded(value, depth=0):
                if isinstance(value, str):
                    return value if len(value) <= 4000 else value[:2000] + "[TRUNCATED]" + value[-2000:]
                if depth >= 6:
                    return "[NESTING TRUNCATED]"
                if isinstance(value, dict):
                    return {k: bounded(v, depth + 1) for k, v in list(value.items())[:64]}
                if isinstance(value, list):
                    return [bounded(v, depth + 1) for v in value[:32]]
                return value
            event = bounded(event)
            serialized = json.dumps(event, ensure_ascii=False)
            if len(serialized) <= limit - retained:
                self.trace.emit("generation_event", data=event, partial=partial)
                retained += len(serialized)
            else:
                self.trace.emit("generation_output", text=serialized[:min(2000, limit - retained)], partial=partial)
                retained = limit

    def check_capabilities(self) -> None:
        if self._checked:
            return
        env = generation_environment()
        try:
            help_result = self.run([self.executable, "exec", "--help"], capture_output=True, text=True,
                                   check=True, timeout=self.deadline.timeout(20, reserve=300), env=env)
            feature_result = self.run([self.executable, "features", "list"], capture_output=True, text=True,
                                      check=True, timeout=self.deadline.timeout(20, reserve=300), env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GenerationError("Codex CLI capability inspection failed") from exc
        missing = [flag for flag in REQUIRED_FLAGS if flag not in help_result.stdout]
        features = {line.split()[0] for line in feature_result.stdout.splitlines() if line.strip()}
        missing += [name for name in (*DISABLED_FEATURES, "skip_host_skill_discovery") if name not in features]
        if missing:
            raise GenerationError("Installed Codex lacks required isolation controls: " + ", ".join(missing))
        self._checked = True

    def argv(self, directory: Path, schema: Path) -> list[str]:
        # TOML table syntax is passed as one argv value; never interpolate shell commands.
        quoted = json.dumps(str(directory.resolve()))
        filesystem = '{":root"="deny",":minimal"="read",' + quoted + '="read"}'
        args = [self.executable, "exec", "-", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--strict-config", "--skip-git-repo-check", "--cd", str(directory), "--output-schema", str(schema), "--json"]
        settings = ["approval_policy=\"never\"", "project_doc_max_bytes=0", "web_search=\"disabled\"",
                    "agents.enabled=false", "apps._default.enabled=false",
                    "skills.max_context_tokens=1", "default_permissions=\"freya_generation\"",
                    "permissions.freya_generation.filesystem=" + filesystem,
                    "permissions.freya_generation.network.enabled=false", "features.skip_host_skill_discovery=true",
                    "history.persistence=\"none\"", "model_reasoning_effort=" + json.dumps(self.reasoning_effort)]
        for setting in settings:
            args.extend(["-c", setting])
        for feature in DISABLED_FEATURES:
            args.extend(["--disable", feature])
        if self.model:
            args.extend(["--model", self.model])
        return args

    def check_isolation(self) -> None:
        if self._isolation_checked:
            return
        # Inspect the same CLI surface using synthetic inputs before sending a
        # banking brief. Detection happens before any substantive model call.
        from .preflight import isolation_probe
        allocated = self.deadline.timeout(120, reserve=300)
        started = time.monotonic()
        self.trace.emit("model_call", model=self.model or "codex-default", reasoning_effort=self.reasoning_effort,
                        purpose="Audit generation tools/context with synthetic isolation canaries",
                        allocated_seconds=round(allocated, 2), remaining_seconds=round(self.deadline.remaining(), 2))
        report = isolation_probe(self.output_dir / "isolation-audit", executable=self.executable,
                                 model=self.model, run=self.run,
                                 reasoning_effort=self.reasoning_effort,
                                 timeout=allocated)
        self.trace.emit("generation_isolation_audit", **report, elapsed_seconds=round(time.monotonic() - started, 2),
                        remaining_seconds=round(self.deadline.remaining(), 2))
        if report.get("status") != "pass":
            raise GenerationError("Codex generation isolation audit failed; no banking brief was sent; see sanitized trace")
        self._isolation_checked = True

    def generate(self, prompt: str, purpose: str = "Compile supplied banking brief and tool contract", *, max_seconds: float = 900) -> dict:
        return self._generate(prompt, purpose, max_seconds=max_seconds)

    def generate_plan(self, prompt: str, *, contract_sha256: str, max_seconds: float = 900) -> dict:
        return self._generate(prompt, "Generate restricted action plan from frozen banking policy contract",
                              max_seconds=max_seconds, plan_contract_sha256=contract_sha256)

    def _generate(self, prompt: str, purpose: str, *, max_seconds: float,
                  plan_contract_sha256: str | None = None) -> dict:
        schema_value = plan_generation_schema(plan_contract_sha256) if plan_contract_sha256 else GENERATION_SCHEMA
        if type(max_seconds) not in {int, float} or not 0 < max_seconds <= 900:
            raise GenerationError("Generation stage cap must be positive and at most 900 seconds")
        self.check_capabilities()
        self.check_isolation()
        self.deadline.require(seconds=1, reserve=300)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        allocated = self.deadline.timeout(max_seconds, reserve=300)
        started = time.monotonic()
        self.trace.emit("model_call", model=self.model or "codex-default; actual emitted model recorded when available",
                        purpose=purpose, reasoning_effort=self.reasoning_effort,
                        allocated_seconds=round(allocated, 2), prompt_characters=len(prompt))
        # Workspaces remain inside the project artifacts directory; only schema is on disk.
        with tempfile.TemporaryDirectory(prefix="generation-", dir=self.output_dir) as dirname:
            directory = Path(dirname)
            schema = directory / "response.schema.json"
            schema.write_text(json.dumps(schema_value), encoding="utf-8")
            try:
                result = self.run(self.argv(directory, schema), input=prompt, text=True, capture_output=True,
                                  timeout=allocated, env=generation_environment(), cwd=directory, on_progress=self._progress)
            except subprocess.TimeoutExpired as exc:
                self._record_output(exc.output, exc.stderr, partial=True)
                self.trace.emit("generation_timeout", elapsed_seconds=round(time.monotonic() - started, 2),
                                allocated_seconds=round(allocated, 2),
                                process_group_terminated=getattr(exc, "process_group_terminated", None),
                                remaining_seconds=round(self.deadline.remaining(), 2))
                raise GenerationError("Codex generation exceeded its allocated time; bounded sanitized partial output is in the trace") from None
            except OSError as exc:
                raise GenerationError("Codex generation process could not start") from exc
        self._record_output(result.stdout, result.stderr)
        self.trace.emit("generation_process_completed", elapsed_seconds=round(time.monotonic() - started, 2), returncode=result.returncode)
        messages: list[str] = []
        forbidden = {"command_execution", "mcp_tool_call", "web_search", "file_change", "tool_call", "image_generation", "computer_use"}
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item", {})
            if not isinstance(item, dict):
                raise GenerationError("Codex emitted a malformed event; see sanitized trace")
            if event.get("type") in ("turn.failed", "error"):
                raise GenerationError("Codex reported a failed generation turn; see sanitized trace")
            if item.get("type") == "error":
                warning = _expected_cli_warning(item.get("message"))
                if warning is None:
                    raise GenerationError("Codex reported an unrecognized error diagnostic; see sanitized trace")
                self.trace.emit("generation_cli_warning", kind=warning)
                continue
            if (item.get("type") and item["type"] not in {"agent_message", "reasoning"}) or event.get("type") in forbidden:
                raise GenerationError("Generation attempted a prohibited tool action")
            if event.get("model"):
                self.trace.emit("model_actual", model=event["model"])
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
        if result.returncode:
            raise GenerationError("Codex generation failed; see sanitized trace")
        for message in reversed(messages):
            text = message.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                value = _strict_json(text)
                if plan_contract_sha256:
                    if not isinstance(value, dict) or set(value) != {"plan_json", "contract_sha256"}:
                        continue
                    if value["contract_sha256"] != plan_contract_sha256 or not isinstance(value["plan_json"], str):
                        raise GenerationError("Plan output does not match the frozen policy contract")
                    plan = _strict_json(value["plan_json"])
                    if isinstance(plan, dict):
                        return {"plan": plan, "contract_sha256": plan_contract_sha256}
                    continue
                if isinstance(value, dict) and isinstance(value.get("policy_json"), str) and isinstance(value.get("workflow_json"), str):
                    value = {"policy": _strict_json(value["policy_json"]), "workflow": _strict_json(value["workflow_json"])}
                if isinstance(value, dict) and isinstance(value.get("policy"), dict) and isinstance(value.get("workflow"), dict):
                    return value
            except (json.JSONDecodeError, ValueError):
                pass
        raise GenerationError("Codex returned no object matching the generation stage contract")
