"""One-command, bounded and auditable banking workflow generation."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "builder"

from .platform import FreyaClient, PlatformError
from .contract import compile_policy_contract, PolicyContractError
from .runtime import CodexGenerator, Deadline, DeadlineExceeded, Trace
from .safety import assert_no_secrets, canonical_json, digest, read_json, sanitize
from .toolbindings import assert_live_contract, bind_controller_auth, derive_tools, operations, resolve_refs
from .validation import validate_workflow
from .action_bindings import compile_action_bindings
from .policy import compile_execution_policy, PolicyError, MissingExecutionRules
from .guards import compile_guarded_workflow, required_execution_kinds, GuardCompileError
from .stored_workflow import assert_equivalent_stored_workflow
from .source_provenance import capture_source_provenance, verify_source_unchanged, bind_run_provenance

PROJECT = Path(__file__).resolve().parents[1]
KIT = PROJECT / "build-the-builder"
EXPORT_RESERVE = 300
MAX_REPAIRS = 2


class FrozenPolicyContractError(ValueError):
    """The fixed policy lacks executable semantics; plan repair is unjustified."""


class HostedTestInfrastructureError(RuntimeError):
    """Evaluation could not establish behavior; changing the candidate is unjustified."""


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(description=__doc__)
    out.add_argument("--brief", type=Path, action="append", required=True, help="Explicit banking source; repeat for amendments")
    out.add_argument("--tool-api", type=Path, required=True, help="This brief's OpenAPI JSON")
    out.add_argument("--output-dir", type=Path, default=Path("artifacts"))
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
    payload = {"sources": sources, "openapi": openapi}
    if repair:
        payload["repair"] = repair
    assert_no_secrets(payload, forbidden_values=_known_secrets())
    return header + "\nAdapt to the supplied banking objective" + policy_instructions + "\n\nAUTHORIZED INPUTS:\n" + canonical_json(payload)


def make_workflow_prompt(contract: dict, openapi: dict, repair: dict | None = None) -> str:
    """Use resolved values only; historical quotations are not operative rules."""
    template = (PROJECT / "builder/prompts/workflow.txt").read_text(encoding="utf-8")
    reference = (PROJECT / "builder/prompts/workflow-reference.txt").read_text(encoding="utf-8")
    payload = {"policy_contract_sha256": contract["sha256"], "goal": contract["goal"],
               "active_rules": {kind: copy.deepcopy(rule["value"]) for kind, rule in contract["active_rules"].items()},
               "permitted_operations": contract["permitted_operations"], "sources": contract["sources"],
               "runtime_reference": reference}
    if repair:
        payload["repair"] = repair
    assert_no_secrets(payload, forbidden_values=_known_secrets())
    return template + "\n\nAUTHORIZED INPUTS:\n" + canonical_json(payload)


def _extract_policy(generator, sources, openapi, run_dir, trace, deadline, secrets):
    repair = None
    for attempt in range(MAX_REPAIRS + 1):
        deadline.require(seconds=1, reserve=EXPORT_RESERVE)
        extracted = generator.generate(make_policy_prompt(sources, openapi, repair),
                                       purpose="Extract banking policy and source/API bindings", max_seconds=450)
        policy = extracted.get("policy")
        try:
            assert_no_secrets(policy, forbidden_values=secrets)
            compiled = compile_policy_contract(policy, sources, openapi)
            # Execution-shape errors belong to bounded extraction repairs,
            # before the policy is frozen; plans cannot repair bank rules.
            compile_execution_policy(policy, compile_action_bindings(policy, openapi))
        except (PolicyContractError, ValueError) as exc:
            errors = getattr(exc, "errors", [str(exc)])
            _write_json(run_dir / f"policy.rejected-{attempt + 1}.json", sanitize(
                {"policy": policy, "validation_errors": errors}, secrets=secrets))
            trace.emit("policy_validation", passed=False, errors=errors)
            if attempt == MAX_REPAIRS:
                raise ValueError("Policy extraction failed after bounded repairs: " + "; ".join(errors)) from exc
            repair = {"validation_errors": errors, "previous_policy": sanitize(policy, secrets=secrets)}
            continue
        return copy.deepcopy(policy), compiled, attempt
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


def candidate_errors(candidate: dict, sources: list[dict], openapi: dict, require_policy: bool) -> list[str]:
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
            compile_policy_contract(policy, sources, openapi)
        except PolicyContractError as exc:
            errors.extend(getattr(exc, "errors", [str(exc)]))
    errors.extend(validate_workflow(candidate.get("workflow", {}), openapi, policy if isinstance(policy, dict) else None))
    return errors


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _write_policy_artifacts(run_dir: Path, candidate: dict, sources: list[dict], openapi: dict, trace: Trace) -> str | None:
    policy = candidate.get("policy")
    if policy is None:
        return None
    contract = compile_policy_contract(policy, sources, openapi)
    _write_json(run_dir / "policy.json", policy)
    _write_json(run_dir / "policy.contract.json", contract)
    trace.emit("policy_contract", sha256=contract["sha256"], sources=len(contract["sources"]),
               active_rules=len(contract["active_rules"]), permitted_operations=len(contract["permitted_operations"]))
    return contract["sha256"]


def _known_secrets() -> tuple[str, ...]:
    return tuple(v for k, v in os.environ.items() if v and
                 (k.endswith(("API_KEY", "ACCESS_TOKEN", "SECRET", "PASSWORD")) or
                  k in {"FREYA_BANK_TEAM_KEY"} or k.startswith("FREYA_TOOL_AUTH_")))


_SAFE_TEST_DIAGNOSTICS = {
    "outcome/reason do not match the independent oracle":
        "Choose the closing outcome and reason from the supplied brief and observed conversation state.",
    "promises do not match the independent oracle":
        "Recheck commitment eligibility, confirmation, write arguments, and successful-write state against the supplied brief.",
    "notes do not match the independent oracle":
        "Recheck whether a note is allowed or required, its scope, and its reason against the supplied brief.",
    "callbacks do not match the independent oracle":
        "Recheck callback eligibility and the agreed date and time against the supplied brief's calendar rules.",
    "transfers do not match the independent oracle":
        "Recheck transfer eligibility and queue selection against the supplied brief and tool contract.",
    "close_call must occur exactly once and be the final tool":
        "Close the call without duplication after all permitted actions; prevent further tool calls after closing.",
    "Brief A forbids add_note":
        "Remove note writes when the applicable supplied brief forbids them.",
    "a transfer forbids notes before or after it":
        "Enforce the applicable transfer and note exclusion before performing either write.",
    "identity/account tools used with a third party or unreached customer":
        "Gate identity checks and account access on reaching the intended customer.",
    "tool is outside the seven permitted banking capabilities":
        "Use only capabilities declared in the supplied tool contract.",
    "identity field is not permitted by the contract":
        "Select identity fields only from the supplied contract and applicable policy.",
    "an already verified identity factor was checked again":
        "Preserve successful identity checks and do not ask for a verified factor again.",
    "identity factors were asked out of order":
        "Ask identity factors in the order required by the supplied brief.",
    "identity check continued after total wrong-answer limit":
        "Stop identity checks when the supplied brief's wrong-answer limit is reached.",
    "account access or promise occurred before both identity factors passed":
        "Prevent account access and commitment writes until all required identity factors have passed.",
    "promise retries exceeded one retry per product":
        "Enforce the supplied brief's retry limit separately for each product; distinguish explicit rejection from ambiguous persistence.",
    "more than one successful promise write for a product":
        "Prevent duplicate successful commitment writes for the same product.",
    "recorded-success claim precedes successful write":
        "Claim that an action was recorded only after the tool confirms the write succeeded.",
}


def _repair_test_summary(result: dict, policy: dict | None = None) -> dict:
    # Only exact, data-free scorer messages are accepted. Free-form judge reasons may
    # contain scripts, customer identities, or expected amounts and never reach the model.
    known_rule_ids = {rule.get("id") for rule in (policy or {}).get("rules", [])
                      if isinstance(rule, dict) and isinstance(rule.get("id"), str)}
    fallbacks = {
        "behavior": "Recheck the applicable banking actions and their ordering against the supplied brief and tool contract.",
        "conversation": "Recheck the conversation instructions and required confirmations in the supplied brief.",
        "honesty": "Keep claims about account state and completed actions consistent with observed successful tool results.",
        "policy": "Recheck the applicable constraints in the supplied brief and their enforcement before tool calls.",
    }
    failures, seen = [], set()
    for failure in result.get("failures", []):
        if not isinstance(failure, dict):
            continue
        category = failure.get("category")
        if not isinstance(category, str) or category not in fallbacks:
            category = "behavior"
        message = failure.get("message")
        diagnostic = _SAFE_TEST_DIAGNOSTICS.get(message, fallbacks[category]) if isinstance(message, str) else fallbacks[category]
        rule_id = failure.get("rule_id")
        rule_id = rule_id if isinstance(rule_id, str) and rule_id in known_rule_ids else None
        key = (category, diagnostic, rule_id)
        if key in seen:
            continue
        seen.add(key)
        row = {"category": category, "diagnostic": diagnostic}
        if rule_id is not None:
            row["rule_id"] = rule_id
        failures.append(row)
        if len(failures) >= 32:
            break
    return {"status": "behavioral_failure", "passed": result.get("passed"), "total": result.get("total"),
            "failures": failures}


def _test_infrastructure_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    total, passed = result.get("total"), result.get("passed")
    if type(total) is not int or total <= 0 or type(passed) is not int or not 0 <= passed <= total:
        return True
    failures = result.get("failures", [])
    if not isinstance(failures, list) or any(not isinstance(failure, dict) for failure in failures):
        return True
    status = result.get("status")
    if not isinstance(status, str) or status not in {"passed", "completed", "failed", "behavioral_failure"}:
        return True
    if (passed < total and not failures) or (passed == total and failures):
        return True
    return any(
        isinstance(failure.get("category"), str) and failure["category"] in {"infrastructure", "evidence"}
        for failure in failures)


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
        source_record = capture_source_provenance(PROJECT, {
            "mode": mode, "model": args.model, "reasoning_effort": args.reasoning_effort,
            "codex_executable": args.codex, "deadline_seconds": deadline.limit - deadline.start,
            "export_reserve_seconds": EXPORT_RESERVE, "max_repairs": MAX_REPAIRS,
            "controller_dependency_injection": generator is not None or client_factory is not FreyaClient or test_runner is not None,
        })
        _write_json(run_dir / "builder-source.json", source_record)
        trace.emit("builder_source_snapshot", builder_source_sha256=source_record["builder_source_sha256"],
                   builder_source_record_sha256=digest(source_record),
                   resolved_config=source_record["resolved_config"])
        if mode in {"offline-fixture", "validate-only"}:
            candidate = _candidate(args.workflow_file, args.policy_file)
            errors = candidate_errors(candidate, sources, openapi, require_policy=False)
            if errors:
                raise ValueError("Candidate validation failed: " + "; ".join(errors))
            _write_json(run_dir / "workflow.json", candidate["workflow"])
            contract_sha256 = _write_policy_artifacts(run_dir, candidate, sources, openapi, trace)
            manifest = {"mode": mode, "status": "validated", "sources": source_manifest,
                        "tool_api_sha256": digest(openapi), "workflow_sha256": digest(candidate["workflow"]),
                        "policy_provenance_checked": candidate.get("policy") is not None,
                        "policy_contract_sha256": contract_sha256,
                        "action_guard_compilation_verified": False,
                        "hosted_behavior_verified": False, "run_directory": str(run_dir.relative_to(PROJECT))}
            return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                 status="validated", hosted_behavior_verified=False)

        generator = generator or CodexGenerator(run_dir, trace, deadline, executable=args.codex, model=args.model,
                                                reasoning_effort=args.reasoning_effort)
        stage_durations = {}
        trace.emit("stage_started", stage="policy")
        stage_started = time.monotonic()
        if args.policy_file:
            policy = read_json(_model_input_path(args.policy_file))
            assert_no_secrets(policy, forbidden_values=secret_values)
            compiled = compile_policy_contract(policy, sources, openapi)
            compile_execution_policy(policy, compile_action_bindings(policy, openapi))
            policy_repairs = 0
            policy_origin = "provided_and_revalidated"
        else:
            policy, compiled, policy_repairs = _extract_policy(
                generator, sources, openapi, run_dir, trace, deadline, secret_values)
            policy_origin = "extracted"
        stage_durations["policy"] = round(time.monotonic() - stage_started, 3)
        trace.emit("stage_completed", stage="policy", elapsed_seconds=stage_durations["policy"],
                   repairs=policy_repairs, origin=policy_origin)
        frozen_policy = copy.deepcopy(policy)
        policy_sha256 = digest(frozen_policy)
        contract_sha256 = _write_policy_artifacts(run_dir, {"policy": frozen_policy}, sources, openapi, trace)
        trace.emit("policy_frozen", policy_sha256=policy_sha256, contract_sha256=contract_sha256)
        if mode == "extract-policy-only":
            deadline.require()
            manifest = {"mode": mode, "status": "policy_extracted", "sources": source_manifest,
                        "tool_api_sha256": digest(openapi), "model": args.model, "reasoning_effort": args.reasoning_effort,
                        "repairs": policy_repairs, "policy_contract_sha256": contract_sha256,
                        "policy_provenance_checked": True, "workflow_created": False, "hosted_behavior_verified": False,
                        "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                        "run_directory": str(run_dir.relative_to(PROJECT))}
            return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                 status="policy_extracted", remaining_seconds=deadline.remaining())
        client = None
        repair = None
        tests = None
        repairs = policy_repairs
        workflow_repairs = 0
        adopted = False
        while True:
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            attempt_number += 1
            attempt_dir = run_dir / "attempts" / f"plan-{attempt_number:03d}"
            attempt_dir.mkdir(parents=True)
            attempt_record = {"number": attempt_number, "directory": str(attempt_dir.relative_to(run_dir)),
                              "status": "running", "phase": "generation",
                              "policy_contract_sha256": contract_sha256, "policy_sha256": policy_sha256,
                              "tool_api_sha256": digest(openapi)}
            attempt_open = True
            _attempt_status(attempt_dir, attempt_record, secret_values)
            trace.emit("stage_started", stage="workflow", attempt=workflow_repairs + 1,
                       contract_sha256=contract_sha256, attempt_directory=attempt_record["directory"])
            stage_started = time.monotonic()
            workflow_prompt = make_workflow_prompt(compiled, openapi, repair)
            _attempt_snapshot(attempt_dir, "generation-context.json",
                              {"contract_sha256": contract_sha256, "prompt": workflow_prompt}, secret_values)
            generated = generator.generate_plan(workflow_prompt, contract_sha256=contract_sha256)
            _attempt_snapshot(attempt_dir, "proposal.json", generated, secret_values)
            _attempt_status(attempt_dir, attempt_record, secret_values, phase="validation")
            stage_durations["workflow"] = round(stage_durations.get("workflow", 0) + time.monotonic() - stage_started, 3)
            trace.emit("stage_completed", stage="workflow", elapsed_seconds=round(time.monotonic() - stage_started, 3),
                       attempt=workflow_repairs + 1)
            if digest(frozen_policy) != policy_sha256 or compile_policy_contract(frozen_policy, sources, openapi)["sha256"] != contract_sha256:
                raise ValueError("Frozen policy contract changed during workflow generation")
            if not isinstance(generated, dict) or set(generated) != {"plan", "contract_sha256"} or generated.get("contract_sha256") != contract_sha256:
                raise ValueError("Plan output must match the frozen contract and cannot replace policy")
            plan = copy.deepcopy(generated["plan"])
            _attempt_snapshot(attempt_dir, "plan.json", plan, secret_values)
            guard_context = None
            try:
                assert_no_secrets(plan, forbidden_values=secret_values)
                bindings = compile_action_bindings(frozen_policy, openapi)
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
                trace.emit("validation", passed=False, errors=errors, attempt=attempt_number)
                if repairs >= MAX_REPAIRS:
                    raise ValueError("Candidate validation failed after bounded repairs: " + "; ".join(errors))
                repairs += 1
                workflow_repairs += 1
                repair = {"validation_errors": errors, "previous_plan": sanitize(plan, secrets=secret_values)}
                continue
            _attempt_status(attempt_dir, attempt_record, secret_values, status="validated", phase="compilation",
                            plan_sha256=digest(plan), workflow_sha256=digest(candidate["workflow"]))
            trace.emit("validation", passed=True, rules=len(candidate["policy"]["rules"]), attempt=attempt_number)
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
                            "policy_origin": policy_origin, "policy_repairs": policy_repairs, "workflow_repairs": workflow_repairs,
                            "selected_attempt": attempt_number, "selected_attempt_directory": attempt_record["directory"],
                            "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                            "run_directory": str(run_dir.relative_to(PROJECT))}
                return _complete_run(run_dir, manifest, trace, source_record, secret_values, deadline,
                                     status="generated_validated", remaining_seconds=deadline.remaining())
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            if client is None:
                client = client_factory(deadline=deadline, reserve=EXPORT_RESERVE, lease_path=output_dir / ".simulation.lock")
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
            if args.test_brief:
                deployment["brief"] = args.test_brief
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
            _attempt_status(attempt_dir, attempt_record, secret_values, status="published", phase="deployment")
            if not args.test_cases:
                break
            deadline.require(seconds=1, reserve=EXPORT_RESERVE)
            if test_runner is None:
                from benchmarks.hosted import run_builder_tests
                test_runner = run_builder_tests
            _attempt_status(attempt_dir, attempt_record, secret_values, phase="testing")
            tests = test_runner(client, deployment, _within_project(args.test_cases), attempt_dir, deadline)
            _attempt_snapshot(attempt_dir, "tests.json", tests, secret_values)
            # Canonical convenience result remains the selected/latest tested
            # candidate; every authoritative attempt snapshot is retained above.
            _attempt_snapshot(run_dir, "benchmark-results.json", tests, secret_values)
            trace.emit("tests", result=tests, attempt=attempt_number,
                       attempt_directory=attempt_record["directory"])
            if _test_infrastructure_failed(tests):
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
            repair = {"test_summary": _repair_test_summary(tests, frozen_policy),
                      "previous_plan": sanitize(plan, secrets=secret_values)}

        client.reserve = 0
        deadline.require()
        _attempt_status(attempt_dir, attempt_record, secret_values, phase="export")
        exported = client.export_workflow(deployment, run_dir / "submission.workflow.json")
        _attempt_snapshot(attempt_dir, "export.json", exported, secret_values)
        # An export response can arrive after its allocation. Preserve it as
        # evidence, but never report a build exceeding the global limit as done.
        deadline.require()
        _attempt_status(attempt_dir, attempt_record, secret_values, status="exported", phase="complete",
                        export_sha256=digest(exported))
        manifest = {"mode": mode, "status": "exported", "sources": source_manifest, "tool_api_sha256": digest(openapi),
                    "model": args.model, "reasoning_effort": args.reasoning_effort, "repairs": repairs,
                    "deployment": {k: v for k, v in deployment.items() if k != "workflow"},
                    "authored_workflow_sha256": digest(candidate["workflow"]), "export_sha256": digest(exported),
                    "action_guard_compilation_verified": True, "plan_sha256": digest(plan),
                    "guard_manifest_sha256": digest(guard_bundle["guard_manifest"]),
                    "policy_contract_sha256": contract_sha256,
                    "policy_origin": policy_origin, "policy_repairs": policy_repairs, "workflow_repairs": workflow_repairs,
                    "selected_attempt": attempt_number, "selected_attempt_directory": attempt_record["directory"],
                    "stage_durations": stage_durations, "elapsed_seconds": round(deadline.elapsed(), 3),
                    "hosted_behavior_verified": tests is not None, "tests": sanitize(tests, secrets=secret_values),
                    "run_directory": str(run_dir.relative_to(PROJECT))}
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
                                                         "run_directory": str(run_dir.relative_to(PROJECT))}, secrets=secret_values))
        trace.emit("end", status="failed", error=str(exc), remaining_seconds=deadline.remaining())
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
