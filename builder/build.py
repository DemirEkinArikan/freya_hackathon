"""One-command, bounded and auditable banking workflow generation."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

if __package__ in (None, ""):
    # Python puts this script's directory first for direct execution. Leaving it
    # there makes imports of stdlib ``platform`` resolve to builder/platform.py.
    # Load our package from its parent and remove only these duplicate roots.
    _builder_directory = Path(__file__).resolve().parent
    _project_directory = _builder_directory.parent
    sys.path[:] = [str(_project_directory), *(
        entry for entry in sys.path
        if Path(entry or os.curdir).resolve() not in {_builder_directory, _project_directory}
    )]
    __package__ = "builder"

from .platform import FreyaClient, PlatformError
from .contract import compile_policy_contract, PolicyContractError
from .runtime import CodexGenerator, Deadline, DeadlineExceeded, GenerationFormatError, Trace
from .safety import assert_no_secrets, canonical_json, digest, read_json, sanitize
from .toolbindings import assert_live_contract, bind_controller_auth, derive_tools, operations, resolve_refs
from .validation import validate_workflow
from .action_bindings import compile_action_bindings
from .policy import compile_execution_policy, PolicyError, MissingExecutionRules
from .guards import compile_guarded_workflow, required_execution_kinds, GuardCompileError
from .stored_workflow import assert_equivalent_stored_workflow
from .execution_requirements import compile_execution_requirements, validate_plan_execution
from .guards import execution_action_catalogue
from .policy import execution_rule_guidance
from .source_provenance import capture_source_provenance, verify_source_unchanged, bind_run_provenance
from .repair_diagnostics import repair_test_summary as _repair_test_summary
from .repair_diagnostics import test_infrastructure_failed as _test_infrastructure_failed

PROJECT = Path(__file__).resolve().parents[1]
KIT = PROJECT / "build-the-builder"
EXPORT_RESERVE = 300
MAX_REPAIRS = 2
POLICY_INITIAL_CAP = 900
POLICY_REPAIR_CAP = 450
WORKFLOW_CAP = 900
# Kept from policy repairs for the first plan call. In a replay of 11 Oct call
# times, a larger reserve refused policy repairs that would have succeeded and
# a smaller one changed timing success by at most 0.07.
FIRST_WORKFLOW_RESERVE = 450
# A repair rewrites its whole document: about 61 s plus 0.0044 s per character of
# the previous attempt's compact JSON (47 validation repairs completed on 11 Oct
# took 0.62-1.19 times this). A repair starts only when its allocation covers 90%
# of the estimate; otherwise the build stops with the real failure instead of
# timing out. A format repair has no previous document, so only the 61 s base is
# checked; the two on 11 Oct took 261 s and 443 s.
REPAIR_BASE_SECONDS = 61
REPAIR_SECONDS_PER_CHARACTER = 0.0044
REPAIR_ADMISSION_FRACTION = 0.9
PROMPT_ENFORCED_GUIDANCE = ("No sealed controller check enforces these source rules. The compiler adds them to system_prompt "
                            "and to nodes that route into the actions they concern. Also state each rule in the "
                            "task_message of every other node where it applies, and plan the dialogue so that the "
                            "agent follows it before it proposes any affected action.")
FORMAT_REPAIR_GUIDANCE = ("Return only the exact required outer JSON object. Serialize each inner object "
                         "as a valid JSON string; escape quotes, backslashes and control characters once "
                         "for each JSON layer. Do not return commentary, duplicate keys or nonfinite numbers.")


class FrozenPolicyContractError(ValueError):
    """The fixed policy lacks executable semantics; plan repair is unjustified."""


class HostedTestInfrastructureError(RuntimeError):
    """Evaluation could not establish behavior; changing the candidate is unjustified."""


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(description=__doc__)
    out.add_argument("--brief", type=Path, action="append", required=True, help="Explicit banking source; repeat for amendments")
    out.add_argument("--tool-api", type=Path, required=True, help="This brief's OpenAPI JSON")
    out.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    out.add_argument("--simulation-lease", type=Path,
                     help="Shared controller-only simulation lock path; defaults to output-dir/.simulation.lock")
    out.add_argument("--slot", help="Persistent workflow slot; defaults to first brief filename")
    out.add_argument("--adopt-workflow", help="Explicit existing workflow ID to bind to this slot after workspace ownership verification")
    out.add_argument("--offline", action="store_true", help="Validate an explicit fixture, with no model or network")
    out.add_argument("--generate-only", action="store_true", help="Generate and validate locally; never initialize a Freya client")
    out.add_argument("--extract-policy-only", action="store_true", help="Extract and compile policy/API contract only; no workflow or Freya client")
    out.add_argument("--validate-only", action="store_true", help="Only validate --workflow-file locally")
    out.add_argument("--workflow-file", type=Path, help="Offline candidate: workflow, platform export, or {policy,workflow}")
    out.add_argument("--policy-file", type=Path, help="Provenance policy for offline validation, or explicitly resume generation from a policy matching all brief/API inputs")
    out.add_argument("--test-cases", type=Path, help="Optional evaluator-only cases; never included in model context")
    out.add_argument("--test-brief", choices=("A", "B"), help="Evaluator-only selection when supplied cases mix practice briefs")
    out.add_argument("--model", default=os.environ.get("FREYA_BUILDER_MODEL") or os.environ.get("BUILDER_MODEL") or "gpt-6.1-sol")
    out.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="high")
    out.add_argument("--codex", default="codex", help="Existing Codex CLI executable")
    out.add_argument("--declarations", choices=("strict", "relaxed"), default="strict",
                     help="relaxed is for last-resort attempts: protected_unenforceable and unresolved source_ambiguous "
                          "declarations become prompt-enforced dialogue rules instead of rejecting the policy")
    return out


def _within_project(path: Path, *, must_exist: bool = True) -> Path:
    candidate = path if path.is_absolute() else PROJECT / path
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(PROJECT) or any(item.is_symlink() for item in (candidate, *candidate.parents)):
        raise ValueError("Inputs and artifacts must stay in this standalone project; symlinks are refused")
    return resolved


def _model_input_path(path: Path) -> Path:
    """Refuse known evaluation and private setup assets before reading model input."""
    resolved = _within_project(path)
    relative = resolved.relative_to(PROJECT)
    parts = tuple(part.casefold() for part in relative.parts)
    private_names = {"meeting.md", "meeting_summary.md", "kickoff_email.txt"}
    if (any(part in {"benchmarks", "tests", "meeting", "kickoff"} for part in parts)
            or any(part == ".env" or part.startswith(".env.") for part in parts)
            or resolved.name.casefold() in private_names
            or resolved.name.casefold() == "public-test-set.json"):
        raise ValueError("Protected evaluator and private setup files cannot be generator briefs or tool API inputs")
    if not resolved.is_file():
        raise ValueError("Generator input must be a regular project file")
    return resolved


def _load_inputs(args) -> tuple[list[dict], dict]:
    # Check every input before opening any, so a protected API cannot be smuggled
    # alongside otherwise approved briefs. --test-cases has a separate evaluator path.
    brief_paths = [_model_input_path(path) for path in args.brief]
    api_path = _model_input_path(args.tool_api)
    sources = []
    for index, resolved in enumerate(brief_paths, 1):
        text = resolved.read_text(encoding="utf-8")
        if not text.strip() or len(text.encode()) > 2 * 1024 * 1024:
            raise ValueError("Brief input is empty or exceeds the input limit")
        assert_no_secrets(text, forbidden_values=_known_secrets())
        sources.append({"id": f"brief-{index}", "file": resolved.name, "sha256": digest(text), "text": text})
    openapi = read_json(api_path)
    assert_no_secrets(openapi, forbidden_values=_known_secrets())
    operations(openapi)  # Fail on unresolved or unsupported contracts before generation.
    # Preserve composition evidence for the strict contract compiler. Tool
    # declarations resolve local references separately; flattening allOf here
    # would hide incompatible field constraints before they can be rejected.
    return sources, openapi


def make_prompt(sources: list[dict], openapi: dict, repair: dict | None = None) -> str:
    template = (PROJECT / "builder/prompts/compile.txt").read_text(encoding="utf-8")
    references = []
    for name in ("workflow-format.md", "tools-and-code.md"):
        references.append({"name": name, "text": (KIT / "docs" / name).read_text(encoding="utf-8")})
    payload = {"sources": sources, "openapi": openapi, "derived_tools": derive_tools(openapi), "runtime_references": references}
    if repair:
        payload["repair"] = repair
    assert_no_secrets(payload, forbidden_values=_known_secrets())
    return template + "\n\nAUTHORIZED INPUTS:\n" + canonical_json(payload)


def make_policy_prompt(sources: list[dict], openapi: dict, repair: dict | None = None) -> str:
    template = (PROJECT / "builder/prompts/compile.txt").read_text(encoding="utf-8")
    policy_instructions = template.split("Adapt to the supplied banking objective", 1)[1].split(
        "Generate a complete Freya workflow", 1)[0]
    header = (PROJECT / "builder/prompts/extract.txt").read_text(encoding="utf-8")
    payload = {"sources": sources, "openapi": openapi,
               "execution_interface": {"actions": execution_action_catalogue(),
                                       "rule_shapes": execution_rule_guidance()}}
    if repair:
        payload["repair"] = repair
    assert_no_secrets(payload, forbidden_values=_known_secrets())
    return header + "\nAdapt to the supplied banking objective" + policy_instructions + "\n\nAUTHORIZED INPUTS:\n" + canonical_json(payload)


def make_workflow_prompt(contract: dict, openapi: dict, repair: dict | None = None, *,
                         execution_requirements: dict | None = None) -> str:
    """Use resolved values only; historical quotations are not operative rules."""
    template = (PROJECT / "builder/prompts/workflow.txt").read_text(encoding="utf-8")
    reference = (PROJECT / "builder/prompts/workflow-reference.txt").read_text(encoding="utf-8")
    payload = {"policy_contract_sha256": contract["sha256"], "goal": contract["goal"],
               "active_rules": {kind: copy.deepcopy(rule["value"]) for kind, rule in contract["active_rules"].items()},
               "permitted_operations": contract["permitted_operations"], "sources": contract["sources"],
               "runtime_reference": reference}
    if execution_requirements is not None:
        payload["execution_requirements"] = copy.deepcopy(execution_requirements)
    if contract.get("prompt_enforced_limitations"):
        payload["prompt_enforced_rules"] = {"guidance": PROMPT_ENFORCED_GUIDANCE, "rules": [
            {"requirement": entry["requirement"], "source_quotes": [source["quote"] for source in entry["sources"]]}
            for entry in contract["prompt_enforced_limitations"]]}
    if repair:
        payload["repair"] = repair
    assert_no_secrets(payload, forbidden_values=_known_secrets())
    return template + "\n\nAUTHORIZED INPUTS:\n" + canonical_json(payload)


def _admit_repair(trace, deadline, stage, attempt, previous, failure, *, cap, reserve):
    """Start a repair only if it can finish before the reserve; otherwise keep the real failure."""
    size = len(json.dumps(previous, ensure_ascii=False, separators=(",", ":"))) if previous is not None else 0
    estimate = REPAIR_BASE_SECONDS + REPAIR_SECONDS_PER_CHARACTER * size
    available = min(cap, deadline.remaining() - reserve)
    admitted = available >= REPAIR_ADMISSION_FRACTION * estimate
    trace.emit("repair_admission", stage=stage, attempt=attempt, admitted=admitted,
               estimated_seconds=round(estimate, 1), available_seconds=round(available, 2),
               reserve_seconds=reserve, remaining_seconds=round(deadline.remaining(), 2))
    if not admitted:
        raise DeadlineExceeded(f"No time for a repair ({max(available, 0):.0f} s available, "
                               f"about {estimate:.0f} s needed): {failure}")


def _stage_completed(trace, deadline, durations, stage, started, **fields):
    """Add one stage interval, measured on the build deadline clock, to the trace and totals."""
    elapsed = round(deadline.elapsed() - started, 3)
    durations[stage] = round(durations.get(stage, 0) + elapsed, 3)
    trace.emit("stage_completed", stage=stage, elapsed_seconds=elapsed,
               remaining_seconds=round(deadline.remaining(), 2), **fields)


def _extract_policy(generator, sources, openapi, run_dir, trace, deadline, secrets, *, workflow_pending=True,
                    declarations="strict"):
    repair = failure = None
    reserve = EXPORT_RESERVE + (FIRST_WORKFLOW_RESERVE if workflow_pending else 0)
    for attempt in range(MAX_REPAIRS + 1):
        cap = POLICY_INITIAL_CAP if attempt == 0 else POLICY_REPAIR_CAP
        if repair:
            _admit_repair(trace, deadline, "policy", attempt + 1, repair.get("previous_policy"), failure,
                          cap=cap, reserve=reserve)
        deadline.require(seconds=1, reserve=reserve)
        allocated = deadline.timeout(cap, reserve=reserve)
        trace.emit("policy_generation_budget", attempt=attempt + 1, cap_seconds=cap,
                   allocated_seconds=round(allocated, 2), reserve_seconds=reserve,
                   first_workflow_reserve_seconds=FIRST_WORKFLOW_RESERVE if workflow_pending else 0,
                   remaining_seconds=round(deadline.remaining(), 2))
        started = deadline.elapsed()
        try:
            extracted = generator.generate(make_policy_prompt(sources, openapi, repair),
                                           purpose="Extract banking policy and source/API bindings",
                                           max_seconds=allocated, reserve_seconds=reserve)
        except GenerationFormatError as exc:
            _write_json(run_dir / f"policy.format-rejected-{attempt + 1}.json", {"format_error": exc.code})
            trace.emit("policy_format_validation", passed=False, format_error=exc.code, attempt=attempt + 1,
                       elapsed_seconds=round(deadline.elapsed() - started, 3))
            if attempt == MAX_REPAIRS:
                raise
            failure = str(exc)
            repair = {"format_error": exc.code, "serialization_guidance": FORMAT_REPAIR_GUIDANCE}
            continue
        elapsed = round(deadline.elapsed() - started, 3)
        deadline.require(seconds=1, reserve=EXPORT_RESERVE)
        policy = extracted.get("policy")
        try:
            assert_no_secrets(policy, forbidden_values=secrets)
            compiled = compile_policy_contract(policy, sources, openapi, declarations=declarations)
            # Execution-shape errors belong to bounded extraction repairs,
            # before the policy is frozen; plans cannot repair bank rules.
            requirements = compile_execution_requirements(policy, sources, openapi, declarations=declarations)
        except (PolicyContractError, ValueError) as exc:
            errors = getattr(exc, "errors", [str(exc)])
            _write_json(run_dir / f"policy.rejected-{attempt + 1}.json", sanitize(
                {"policy": policy, "validation_errors": errors}, secrets=secrets))
            trace.emit("policy_validation", passed=False, errors=errors, attempt=attempt + 1, elapsed_seconds=elapsed)
            if attempt == MAX_REPAIRS:
                raise ValueError("Policy extraction failed after bounded repairs: " + "; ".join(errors)) from exc
            failure = "Policy extraction failed: " + "; ".join(errors)
            repair = {"validation_errors": errors, "previous_policy": sanitize(policy, secrets=secrets)}
            continue
        trace.emit("policy_execution_requirements", passed=True, sha256=requirements["sha256"],
                   selected_actions=requirements["actions"], required_kinds=requirements["required_kinds"],
                   attempt=attempt + 1, elapsed_seconds=elapsed)
        return copy.deepcopy(policy), compiled, requirements, attempt
    raise AssertionError("unreachable policy extraction state")


def _candidate(path: Path, policy_path: Path | None) -> dict:
    value = read_json(_within_project(path))
    if not isinstance(value, dict):
        raise ValueError("A workflow candidate must be a JSON object")
    workflow = value.get("workflow", value)
    policy = value.get("policy")
    if policy_path:
        policy = read_json(_within_project(policy_path))
    return {"workflow": workflow, "policy": policy}


def candidate_errors(candidate: dict, sources: list[dict], openapi: dict, require_policy: bool,
                     declarations: str = "strict") -> list[str]:
    errors = []
    try:
        assert_no_secrets(candidate, forbidden_values=_known_secrets())
    except (ValueError, RuntimeError) as exc:
        return [str(exc)]
    policy = candidate.get("policy")
    if policy is None:
        if require_policy:
            errors.append("Generated candidates require a provenance policy")
    else:
        try:
            compile_policy_contract(policy, sources, openapi, declarations=declarations)
        except PolicyContractError as exc:
            errors.extend(getattr(exc, "errors", [str(exc)]))
    errors.extend(validate_workflow(candidate.get("workflow", {}), openapi, policy if isinstance(policy, dict) else None))
    return errors


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare_evaluator_input(args, run_dir: Path, secrets: tuple[str, ...]) -> dict | None:
    """Freeze evaluator-only criteria; neither their content nor binding enters generation."""
    if not args.test_cases:
        return None
    source = _within_project(args.test_cases)
    original = source.read_bytes()
    value = read_json(source)
    if source.read_bytes() != original:
        raise HostedTestInfrastructureError("Evaluator input changed while it was being loaded")
    assert_no_secrets(value, forbidden_values=secrets)
    # Autonomous development repair must not silently consume reserved blind
    # feedback. The separate frozen-candidate evaluator owns holdout lifecycle.
    rows = value.get("cases", []) if isinstance(value, dict) else value if isinstance(value, list) else []
    if isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("split") == "holdout"
        and (not args.test_brief or row.get("brief") == args.test_brief)
        for row in rows
    ):
        raise ValueError("Autonomous repair refuses holdout cases; use frozen-candidate evaluation with consumption tracking")
    snapshot = run_dir / "evaluator-cases.json"
    _write_json(snapshot, value)
    return {"source_file": str(source.relative_to(PROJECT)),
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "canonical_sha256": digest(value), "snapshot_file": snapshot.name,
            "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "selection": {"brief": args.test_brief}}


def _verify_evaluator_input(binding: dict, run_dir: Path) -> Path:
    """Reject changed criteria before they can justify repair or an accepted export."""
    try:
        source = _within_project(Path(binding["source_file"]))
        snapshot = _within_project(run_dir / binding["snapshot_file"])
        unchanged = (hashlib.sha256(source.read_bytes()).hexdigest() == binding["source_sha256"]
                     and hashlib.sha256(snapshot.read_bytes()).hexdigest() == binding["snapshot_sha256"])
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        raise HostedTestInfrastructureError("Evaluator input changed; the candidate and original evidence were preserved without repair")
    return snapshot


def _test_deployment_matches(result: dict, deployment: dict) -> bool:
    """Bind supplied evaluator pins to this attempt, beyond internal consistency."""
    pin = result.get("deployment_pin")
    if pin is None:
        return True  # Legacy injected local runners do not report hosted pins.
    expected = {key: deployment.get(key) for key in (
        "workflow_id", "agent_id", "version_id", "version_number", "published", "model", "reasoning_effort")}
    expected["graph_sha256"] = digest(deployment["workflow"])
    return all(all(value == expected[key] for key, value in pin[side].items() if key in expected)
               for side in ("before", "after"))


def _complete_run(run_dir: Path, manifest: dict, trace: Trace, source_record: dict,
                  secrets: tuple[str, ...], deadline: Deadline, **end_fields: Any) -> dict:
    verify_source_unchanged(PROJECT, source_record)
    deadline.require()
    if "elapsed_seconds" in manifest:
        manifest["elapsed_seconds"] = round(deadline.elapsed(), 3)
    if "remaining_seconds" in end_fields:
        end_fields["remaining_seconds"] = deadline.remaining()
    manifest = sanitize({**manifest, "builder_source_sha256": source_record["builder_source_sha256"],
                         "builder_source_record_sha256": digest(source_record)}, secrets=secrets)
    _write_json(run_dir / "manifest.json", manifest)
    trace.emit("end", **end_fields)
    events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    _write_json(run_dir / "build-provenance.json", bind_run_provenance(source_record, manifest, events))
    deadline.require()
    return manifest


def _attempt_snapshot(directory: Path, name: str, value: Any, secrets: tuple[str, ...]) -> None:
    """Keep evaluator/deployment evidence out of model repair context."""
    _write_json(directory / name, sanitize(value, secrets=secrets))


def _attempt_status(directory: Path, record: dict, secrets: tuple[str, ...], **changes: Any) -> None:
    record.update(changes)
    _attempt_snapshot(directory, "attempt.json", record, secrets)


def _write_policy_artifacts(run_dir: Path, candidate: dict, sources: list[dict], openapi: dict, trace: Trace,
                            declarations: str = "strict") -> str | None:
    policy = candidate.get("policy")
    if policy is None:
        return None
    contract = compile_policy_contract(policy, sources, openapi, declarations=declarations)
    _write_json(run_dir / "policy.json", policy)
    _write_json(run_dir / "policy.contract.json", contract)
    trace.emit("policy_contract", sha256=contract["sha256"], sources=len(contract["sources"]),
               active_rules=len(contract["active_rules"]), permitted_operations=len(contract["permitted_operations"]),
               declared_limitations=[entry["category"] for entry in contract.get("declared_limitations", [])])
    return contract["sha256"]


def _known_secrets() -> tuple[str, ...]:
    return tuple(v for k, v in os.environ.items() if v and
                 (k.endswith(("API_KEY", "ACCESS_TOKEN", "SECRET", "PASSWORD")) or
                  k in {"FREYA_BANK_TEAM_KEY", "FREYA_API_KEY_2"} or k.startswith("FREYA_TOOL_AUTH_")))




def execute(args, *, generator=None, client_factory: Callable = FreyaClient, test_runner: Callable | None = None,
            deadline: Deadline | None = None) -> dict:
    deadline = deadline or Deadline(1800)
    output_dir = _within_project(args.output_dir, must_exist=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_dir / run_id
    run_dir.mkdir()
    secret_values = _known_secrets()
    trace = Trace(run_dir / "trace.jsonl", secrets=secret_values)
    mode = "extract-policy-only" if args.extract_policy_only else "validate-only" if args.validate_only else "offline-fixture" if args.offline else "generate-only" if args.generate_only else "live"
    trace.emit("start", mode=mode, model=args.model if mode in {"live", "generate-only", "extract-policy-only"} else None)
    attempt_dir = None
    attempt_record = None
    attempt_open = False
    attempt_number = 0
    source_record = None
    evaluator_input = None
    stage_durations = {}
    declarations = getattr(args, "declarations", "strict")
    relaxed_record = {"declaration_mode": "relaxed"} if declarations == "relaxed" else {}
    try:
        if sum(bool(value) for value in (args.offline, args.validate_only, args.generate_only, args.extract_policy_only)) > 1:
            raise ValueError("offline, validate-only, generate-only and extract-policy-only modes are mutually exclusive")
        if (args.offline or args.validate_only) and not args.workflow_file:
            raise ValueError("Offline and validate-only modes require --workflow-file; no fabricated scaffold is generated")
        if args.workflow_file and not (args.offline or args.validate_only):
            raise ValueError("--workflow-file is reserved for offline or validate-only fixtures")
        if args.policy_file and args.extract_policy_only:
            raise ValueError("--extract-policy-only cannot resume --policy-file; use --generate-only")
        if args.test_cases and mode != "live":
            raise ValueError("Hosted evaluator cases can only be used by a live builder invocation")
        if args.adopt_workflow and mode != "live":
            raise ValueError("--adopt-workflow can only be used by a live builder invocation")
        if mode == "live" and client_factory is FreyaClient and not os.environ.get("FREYA_API_KEY", "").strip():
            raise PlatformError("FREYA_API_KEY is required before live workflow generation; use --generate-only for a local build")
        sources, openapi = _load_inputs(args)
        if mode == "live":
            assert_live_contract(openapi)
        source_manifest = [{k: v for k, v in source.items() if k != "text"} for source in sources]
        trace.emit("inputs", sources=source_manifest, tool_api_sha256=digest(openapi))
        evaluator_input = _prepare_evaluator_input(args, run_dir, secret_values)
        if evaluator_input is not None:
            _write_json(run_dir / "evaluator-input.json", evaluator_input)
            trace.emit("evaluator_input_frozen", **evaluator_input)
        source_record = capture_source_provenance(PROJECT, {
            "mode": mode, "model": args.model, "reasoning_effort": args.reasoning_effort,
            "codex_executable": args.codex, "deadline_seconds": deadline.duration_seconds,
            "export_reserve_seconds": EXPORT_RESERVE, "max_repairs": MAX_REPAIRS,
            "generation_budgets": {"policy_initial_cap_seconds": POLICY_INITIAL_CAP,
                                   "policy_repair_cap_seconds": POLICY_REPAIR_CAP,
                                   "first_workflow_reserve_seconds": FIRST_WORKFLOW_RESERVE,
                                   "workflow_cap_seconds": WORKFLOW_CAP,
                                   "repair_admission": {"base_seconds": REPAIR_BASE_SECONDS,
                                                        "seconds_per_character": REPAIR_SECONDS_PER_CHARACTER,
                                                        "fraction": REPAIR_ADMISSION_FRACTION}},
            "controller_dependency_injection": generator is not None or client_factory is not FreyaClient or test_runner is not None,
            "evaluator_input": evaluator_input,
            **({"declaration_mode": "relaxed"} if declarations == "relaxed" else {}),
        })
        _write_json(run_dir / "builder-source.json", source_record)
        trace.emit("builder_source_snapshot", builder_source_sha256=source_record["builder_source_sha256"],
                   builder_source_record_sha256=digest(source_record),
                   resolved_config=source_record["resolved_config"])
        if mode in {"offline-fixture", "validate-only"}:
            candidate = _candidate(args.workflow_file, args.policy_file)
            errors = candidate_errors(candidate, sources, openapi, require_policy=False, declarations=declarations)
            if errors:
                raise ValueError("Candidate validation failed: " + "; ".join(errors))
            _write_json(run_dir / "workflow.json", candidate["workflow"])
            contract_sha256 = _write_policy_artifacts(run_dir, candidate, sources, openapi, trace, declarations)
            manifest = {"mode": mode, "status": "validated", "sources": source_manifest,
                        "tool_api_sha256": digest(openapi), "workflow_sha256": digest(candidate["workflow"]),
                        "policy_provenance_checked": candidate.get("policy") is not None,
                        "policy_contract_sha256": contract_sha256,
                        "action_guard_compilation_verified": False,
                        "hosted_behavior_verified": False, "run_directory": str(run_dir.relative_to(PROJECT)),
                        **relaxed_record}
            return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                 status="validated", hosted_behavior_verified=False)

        generator = generator or CodexGenerator(run_dir, trace, deadline, executable=args.codex, model=args.model,
                                                reasoning_effort=args.reasoning_effort)
        trace.emit("stage_started", stage="policy")
        stage_started = deadline.elapsed()
        if args.policy_file:
            policy = read_json(_model_input_path(args.policy_file))
            assert_no_secrets(policy, forbidden_values=secret_values)
            compiled = compile_policy_contract(policy, sources, openapi, declarations=declarations)
            requirements = compile_execution_requirements(policy, sources, openapi, declarations=declarations)
            trace.emit("policy_execution_requirements", passed=True, sha256=requirements["sha256"],
                       selected_actions=requirements["actions"], required_kinds=requirements["required_kinds"])
            policy_repairs = 0
            policy_origin = "provided_and_revalidated"
        else:
            policy, compiled, requirements, policy_repairs = _extract_policy(
                generator, sources, openapi, run_dir, trace, deadline, secret_values,
                workflow_pending=mode != "extract-policy-only", declarations=declarations)
            policy_origin = "extracted"
        _stage_completed(trace, deadline, stage_durations, "policy", stage_started,
                         repairs=policy_repairs, origin=policy_origin)
        frozen_policy = copy.deepcopy(policy)
        policy_sha256 = digest(frozen_policy)
        contract_sha256 = _write_policy_artifacts(run_dir, {"policy": frozen_policy}, sources, openapi, trace, declarations)
        _write_json(run_dir / "execution-requirements.json", requirements)
        trace.emit("policy_frozen", policy_sha256=policy_sha256, contract_sha256=contract_sha256,
                   execution_requirements_sha256=requirements["sha256"])
        if declarations == "relaxed":
            # Listed, never claimed as controller enforcement: these hold only as dialogue rules.
            unguarded = compile_action_bindings(frozen_policy, openapi, declarations=declarations)
            relaxed_record.update({"prompt_enforced_limitations": [
                {"category": entry["category"], "rule_ids": entry["rule_ids"], "enforced_by": "dialogue_prompt"}
                for entry in compiled["prompt_enforced_limitations"]],
                "unguarded_close_codes": {category: data["unbound_codes"] for category, data in unguarded["categories"].items()
                                          if "unbound_codes" in data and unguarded.get("declaration_mode") == "relaxed"}})
            trace.emit("relaxed_declarations", **relaxed_record)
        if mode == "extract-policy-only":
            deadline.require()
            manifest = {"mode": mode, "status": "policy_extracted", "sources": source_manifest,
                        "tool_api_sha256": digest(openapi), "model": args.model, "reasoning_effort": args.reasoning_effort,
                        "repairs": policy_repairs, "policy_contract_sha256": contract_sha256,
                        "execution_requirements_sha256": requirements["sha256"],
                        "policy_provenance_checked": True, "workflow_created": False, "hosted_behavior_verified": False,
                        "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                        "run_directory": str(run_dir.relative_to(PROJECT)), **relaxed_record}
            return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                 status="policy_extracted", remaining_seconds=deadline.remaining())
        client = None
        repair = failure = None
        tests = None
        repairs = policy_repairs
        workflow_repairs = 0
        adopted = False
        while True:
            if repair:
                _admit_repair(trace, deadline, "plan", attempt_number + 1, repair.get("previous_plan"), failure,
                              cap=WORKFLOW_CAP, reserve=EXPORT_RESERVE)
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            attempt_number += 1
            attempt_dir = run_dir / "attempts" / f"plan-{attempt_number:03d}"
            attempt_dir.mkdir(parents=True)
            attempt_record = {"number": attempt_number, "directory": str(attempt_dir.relative_to(run_dir)),
                              "status": "running", "phase": "generation",
                              "policy_contract_sha256": contract_sha256, "policy_sha256": policy_sha256,
                              "tool_api_sha256": digest(openapi), "evaluator_input": evaluator_input}
            attempt_open = True
            _attempt_status(attempt_dir, attempt_record, secret_values)
            trace.emit("stage_started", stage="workflow", attempt=workflow_repairs + 1,
                       contract_sha256=contract_sha256, attempt_directory=attempt_record["directory"])
            stage_started = deadline.elapsed()
            workflow_prompt = make_workflow_prompt(compiled, openapi, repair, execution_requirements=requirements)
            _attempt_snapshot(attempt_dir, "generation-context.json",
                              {"contract_sha256": contract_sha256, "prompt": workflow_prompt}, secret_values)
            try:
                generated = generator.generate_plan(workflow_prompt, contract_sha256=contract_sha256)
            except GenerationFormatError as exc:
                elapsed = round(deadline.elapsed() - stage_started, 3)
                stage_durations["workflow"] = round(stage_durations.get("workflow", 0) + elapsed, 3)
                _attempt_snapshot(attempt_dir, "generation-format.json", {"format_error": exc.code}, secret_values)
                _attempt_status(attempt_dir, attempt_record, secret_values, status="format_rejected", phase="generation")
                attempt_open = False
                trace.emit("workflow_format_validation", passed=False, format_error=exc.code,
                           attempt=attempt_number, elapsed_seconds=elapsed)
                if repairs >= MAX_REPAIRS:
                    raise
                repairs += 1
                workflow_repairs += 1
                failure = str(exc)
                repair = {"format_error": exc.code, "serialization_guidance": FORMAT_REPAIR_GUIDANCE}
                continue
            _attempt_snapshot(attempt_dir, "proposal.json", generated, secret_values)
            _attempt_status(attempt_dir, attempt_record, secret_values, phase="validation")
            _stage_completed(trace, deadline, stage_durations, "workflow", stage_started, attempt=workflow_repairs + 1)
            stage_started = deadline.elapsed()
            if (digest(frozen_policy) != policy_sha256 or compile_policy_contract(
                    frozen_policy, sources, openapi, declarations=declarations)["sha256"] != contract_sha256):
                raise ValueError("Frozen policy contract changed during workflow generation")
            if not isinstance(generated, dict) or set(generated) != {"plan", "contract_sha256"} or generated.get("contract_sha256") != contract_sha256:
                raise ValueError("Plan output must match the frozen contract and cannot replace policy")
            plan = copy.deepcopy(generated["plan"])
            _attempt_snapshot(attempt_dir, "plan.json", plan, secret_values)
            guard_context = None
            try:
                assert_no_secrets(plan, forbidden_values=secret_values)
                coverage_errors = validate_plan_execution(plan, requirements, frozen_policy, sources, openapi,
                                                          declarations=declarations)
                if coverage_errors:
                    raise GuardCompileError(coverage_errors)
                _attempt_snapshot(attempt_dir, "execution-requirements.json", requirements, secret_values)
                bindings = compile_action_bindings(frozen_policy, openapi, declarations=declarations)
                _attempt_snapshot(attempt_dir, "action-bindings.json", bindings, secret_values)
                execution_policy = compile_execution_policy(
                    frozen_policy, bindings, required_kinds=required_execution_kinds(plan))
                _attempt_snapshot(attempt_dir, "execution-policy.json", execution_policy, secret_values)
                guard_context = {"plan": plan, "contract": compiled,
                                 "execution_policy": execution_policy, "bindings": bindings}
                _attempt_snapshot(attempt_dir, "guard-context.json", guard_context, secret_values)
                guard_bundle = compile_guarded_workflow(plan, compiled, openapi, execution_policy, bindings)
                _attempt_snapshot(attempt_dir, "guard-manifest.json", guard_bundle["guard_manifest"], secret_values)
                _attempt_snapshot(attempt_dir, "workflow.json", guard_bundle["workflow"], secret_values)
                candidate = {"policy": copy.deepcopy(frozen_policy), "workflow": guard_bundle["workflow"]}
                errors = validate_workflow(candidate["workflow"], openapi, frozen_policy,
                                           guard_context=guard_context)
            except MissingExecutionRules as exc:
                trace.emit("frozen_contract_failure", missing_kinds=list(exc.missing_kinds),
                           policy_preserved=True, plan_repair_attempted=False)
                raise FrozenPolicyContractError(
                    "Frozen policy lacks required execution rules; no plan repair was attempted: "
                    + ", ".join(exc.missing_kinds)) from exc
            except (GuardCompileError, PolicyError, ValueError) as exc:
                errors = getattr(exc, "errors", [str(exc)])
                candidate = {"policy": copy.deepcopy(frozen_policy), "plan": plan}
            if errors:
                _attempt_snapshot(attempt_dir, "validation.json", {"errors": errors, "candidate": candidate}, secret_values)
                _attempt_status(attempt_dir, attempt_record, secret_values, status="validation_rejected", phase="validation")
                attempt_open = False
                _write_json(run_dir / f"candidate.rejected-{repairs + 1}.json", sanitize(
                    {"candidate": candidate, "validation_errors": errors}, secrets=secret_values))
                trace.emit("validation", passed=False, errors=errors, attempt=attempt_number,
                           elapsed_seconds=round(deadline.elapsed() - stage_started, 3))
                if repairs >= MAX_REPAIRS:
                    raise ValueError("Candidate validation failed after bounded repairs: " + "; ".join(errors))
                repairs += 1
                workflow_repairs += 1
                failure = "Candidate validation failed: " + "; ".join(errors)
                repair = {"validation_errors": errors, "previous_plan": sanitize(plan, secrets=secret_values)}
                continue
            _attempt_status(attempt_dir, attempt_record, secret_values, status="validated", phase="compilation",
                            plan_sha256=digest(plan), workflow_sha256=digest(candidate["workflow"]))
            trace.emit("validation", passed=True, rules=len(candidate["policy"]["rules"]), attempt=attempt_number,
                       elapsed_seconds=round(deadline.elapsed() - stage_started, 3))
            _write_json(run_dir / "plan.json", plan)
            _write_json(run_dir / "execution-policy.json", execution_policy)
            _write_json(run_dir / "action-bindings.json", bindings)
            _write_json(run_dir / "guard-manifest.json", guard_bundle["guard_manifest"])
            _write_json(run_dir / "workflow.json", candidate["workflow"])
            trace.emit("action_guard_compilation", verified=True,
                       plan_sha256=digest(plan), guard_manifest_sha256=digest(guard_bundle["guard_manifest"]))
            if mode == "generate-only":
                deadline.require()
                _attempt_status(attempt_dir, attempt_record, secret_values, status="generated_validated", phase="complete")
                manifest = {"mode": mode, "status": "generated_validated", "sources": source_manifest,
                            "tool_api_sha256": digest(openapi), "model": args.model, "reasoning_effort": args.reasoning_effort,
                            "repairs": repairs, "workflow_sha256": digest(candidate["workflow"]),
                            "policy_provenance_checked": True, "hosted_behavior_verified": False,
                            "action_guard_compilation_verified": True, "plan_sha256": digest(plan),
                            "guard_manifest_sha256": digest(guard_bundle["guard_manifest"]),
                            "policy_contract_sha256": contract_sha256,
                            "execution_requirements_sha256": requirements["sha256"],
                            "policy_origin": policy_origin, "policy_repairs": policy_repairs, "workflow_repairs": workflow_repairs,
                            "selected_attempt": attempt_number, "selected_attempt_directory": attempt_record["directory"],
                            "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                            "run_directory": str(run_dir.relative_to(PROJECT)), **relaxed_record}
                return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                     status="generated_validated", remaining_seconds=deadline.remaining())
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            stage_started = deadline.elapsed()
            if client is None:
                lease_path = getattr(args, "simulation_lease", None)
                client = client_factory(deadline=deadline, reserve=EXPORT_RESERVE,
                                        lease_path=lease_path.resolve() if lease_path else output_dir / ".simulation.lock")
            if args.adopt_workflow and not adopted:
                binding = client.adopt_workflow(args.slot or args.brief[0].stem, args.adopt_workflow, output_dir / ".freya-slots.json")
                trace.emit("workflow_adopted", **binding)
                adopted = True
            runtime_workflow = bind_controller_auth(candidate["workflow"], openapi)
            _attempt_status(attempt_dir, attempt_record, secret_values, phase="deployment",
                            authenticated_workflow_sha256=digest(runtime_workflow))
            client.validate_workflow(args.slot or args.brief[0].stem, runtime_workflow)
            deployment = client.save_workflow(args.slot or args.brief[0].stem, runtime_workflow, output_dir / ".freya-slots.json")
            deployment["model"] = args.model
            deployment["reasoning_effort"] = args.reasoning_effort
            if evaluator_input and evaluator_input["selection"]["brief"]:
                deployment["brief"] = evaluator_input["selection"]["brief"]
            _attempt_snapshot(attempt_dir, "deployment.json", deployment, secret_values)
            # Revalidate the actual stored workflow, before optional tests.
            deployed_definition = {k: v for k, v in deployment["workflow"].items() if k != "agent_config"}
            # Validate the authored form; the adapter separately verifies injected model config.
            # Auth is controller-bound after exact guarded compilation. Only
            # specifically recognized serializer defaults may be added on save.
            try:
                assert_equivalent_stored_workflow(deployed_definition, runtime_workflow)
            except ValueError as exc:
                raise ValueError("Deployed definition differs from the controller-authenticated guarded workflow: " + str(exc)) from exc
            deployed_errors = validate_workflow(candidate["workflow"], openapi, candidate["policy"],
                                                guard_context=guard_context)
            if deployed_errors:
                raise ValueError("Deployed definition failed validation: " + "; ".join(deployed_errors))
            trace.emit("deployment", **{k: v for k, v in deployment.items() if k != "workflow"},
                       steps=len(deployment["workflow"]["nodes"]), tools=len(operations(openapi)), attempt=attempt_number)
            _stage_completed(trace, deadline, stage_durations, "deployment", stage_started, attempt=attempt_number)
            _attempt_status(attempt_dir, attempt_record, secret_values, status="published", phase="deployment")
            if evaluator_input is None:
                break
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            stage_started = deadline.elapsed()
            if test_runner is None:
                from benchmarks.hosted import run_builder_tests
                test_runner = run_builder_tests
            _attempt_status(attempt_dir, attempt_record, secret_values, phase="testing")
            cases_path = _verify_evaluator_input(evaluator_input, run_dir)
            tests = test_runner(client, deployment, cases_path, attempt_dir, deadline)
            _stage_completed(trace, deadline, stage_durations, "testing", stage_started, attempt=attempt_number)
            _attempt_snapshot(attempt_dir, "tests.json", tests, secret_values)
            # Canonical convenience result remains the selected/latest tested
            # candidate; every authoritative attempt snapshot is retained above.
            _attempt_snapshot(run_dir, "benchmark-results.json", tests, secret_values)
            trace.emit("tests", result=tests, attempt=attempt_number,
                       attempt_directory=attempt_record["directory"])
            _verify_evaluator_input(evaluator_input, run_dir)
            if _test_infrastructure_failed(tests) or not _test_deployment_matches(tests, deployment):
                trace.emit("test_infrastructure_failure", candidate_preserved=True, model_repair_attempted=False)
                raise HostedTestInfrastructureError(
                    "Hosted test infrastructure or evidence failed; the existing candidate was preserved and was not regenerated")
            if tests.get("status") in {"passed", "completed"} and tests.get("total", 0) > 0 and tests.get("passed") == tests.get("total"):
                break
            _attempt_status(attempt_dir, attempt_record, secret_values, status="behavioral_failure", phase="testing")
            attempt_open = False
            if repairs >= MAX_REPAIRS:
                raise ValueError("The supplied hosted acceptance cases still fail after bounded repairs")
            repairs += 1
            workflow_repairs += 1
            failure = "The supplied hosted acceptance cases still fail"
            repair = {"test_summary": _repair_test_summary(tests, frozen_policy),
                      "previous_plan": sanitize(plan, secrets=secret_values)}

        client.reserve = 0
        deadline.require()
        stage_started = deadline.elapsed()
        if evaluator_input is not None:
            _verify_evaluator_input(evaluator_input, run_dir)
        _attempt_status(attempt_dir, attempt_record, secret_values, phase="export")
        exported = client.export_workflow(deployment, run_dir / "submission.workflow.json")
        _stage_completed(trace, deadline, stage_durations, "export", stage_started, attempt=attempt_number)
        _attempt_snapshot(attempt_dir, "export.json", exported, secret_values)
        # An export response can arrive after its allocation. Preserve it as
        # evidence, but never report a build exceeding the global limit as done.
        deadline.require()
        if evaluator_input is not None:
            _verify_evaluator_input(evaluator_input, run_dir)
        _attempt_status(attempt_dir, attempt_record, secret_values, status="exported", phase="complete",
                        export_sha256=digest(exported))
        manifest = {"mode": mode, "status": "exported", "sources": source_manifest, "tool_api_sha256": digest(openapi),
                    "model": args.model, "reasoning_effort": args.reasoning_effort, "repairs": repairs,
                    "deployment": {k: v for k, v in deployment.items() if k != "workflow"},
                    "authored_workflow_sha256": digest(candidate["workflow"]), "export_sha256": digest(exported),
                    "action_guard_compilation_verified": True, "plan_sha256": digest(plan),
                    "guard_manifest_sha256": digest(guard_bundle["guard_manifest"]),
                    "policy_contract_sha256": contract_sha256,
                    "execution_requirements_sha256": requirements["sha256"],
                    "policy_origin": policy_origin, "policy_repairs": policy_repairs, "workflow_repairs": workflow_repairs,
                    "selected_attempt": attempt_number, "selected_attempt_directory": attempt_record["directory"],
                    "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                    "hosted_behavior_verified": tests is not None, "tests": sanitize(tests, secrets=secret_values),
                    "evaluator_input": evaluator_input,
                    "run_directory": str(run_dir.relative_to(PROJECT)), **relaxed_record}
        return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                             status="exported", export_sha256=manifest["export_sha256"], remaining_seconds=deadline.remaining())
    except Exception as exc:
        if attempt_open and attempt_dir is not None and attempt_record is not None:
            _attempt_status(attempt_dir, attempt_record, secret_values, status="failed", error=str(exc),
                            failure_kind="test_infrastructure" if isinstance(exc, HostedTestInfrastructureError)
                            else "frozen_policy_contract" if isinstance(exc, FrozenPolicyContractError) else "builder")
        _write_json(run_dir / "manifest.json", sanitize({"mode": mode, "status": "failed", "error": str(exc),
                                                         "failure_kind": "test_infrastructure" if isinstance(exc, HostedTestInfrastructureError)
                                                         else "frozen_policy_contract" if isinstance(exc, FrozenPolicyContractError) else "builder",
                                                         "hosted_behavior_verified": False,
                                                         "builder_source_sha256": source_record["builder_source_sha256"] if source_record else None,
                                                         "builder_source_record_sha256": digest(source_record) if source_record else None,
                                                         "last_attempt": attempt_number or None,
                                                         "last_attempt_directory": attempt_record["directory"] if attempt_record else None,
                                                         "evaluator_input": evaluator_input,
                                                         "run_directory": str(run_dir.relative_to(PROJECT)),
                                                         **relaxed_record}, secrets=secret_values))
        trace.emit("end", status="failed", error=str(exc), remaining_seconds=deadline.remaining(),
                   stage_durations=stage_durations)
        raise


def main(argv: list[str] | None = None) -> int:
    try:
        from .config import load_local_environment
        load_local_environment(PROJECT / ".env")
        args = parser().parse_args(argv)
        print(json.dumps(sanitize(execute(args)), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": sanitize(str(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
