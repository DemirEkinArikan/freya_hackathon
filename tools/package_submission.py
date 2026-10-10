#!/usr/bin/env python3
"""Allowlisted source packaging; deployment readiness is a separate evidence gate.

Final manifests require team, repo, full HEAD commit, run_command, and mappings
named workflows, traces, runs and inputs, each keyed by hidden-1 and hidden-2.
runs points to each controller manifest.json; inputs contains brief paths under
briefs and the matching OpenAPI path under tool_api. Selected attempt snapshots
are resolved next to the controller manifest, never guessed from an older run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import math
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.safety import assert_no_secrets, digest, read_json
from builder.config import read_local_environment
from builder.source_provenance import _source_files, bind_run_provenance

ROOT_FILES = {"README.md", "AGENTS.md", "pyproject.toml", ".python-version", ".env.example", ".gitignore"}
SOURCE_DIRS = {"builder", "benchmarks", "tests", "tools", "docs", "build-the-builder"}
SKIP_PARTS = {"__pycache__", ".git", ".venv", "generated", "holdout", "results", "traces", "out", "artifacts"}
SKIP_NAMES = {".env", "env.sh", ".DS_Store", ".freya-agents.json", ".personas.json", "kickoff_email.txt", "meeting.md", "meeting_summary.md"}
EXTENSIONS = {".py", ".sh", ".json", ".jsonl", ".md", ".toml", ".txt"}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _event_name(event):
    return event.get("event") or event.get("kind") or event.get("type")


def validate_trace(events, workflow):
    """Check this builder's structured trace, not the authenticity of live calls."""
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Trace events must be objects")
    starts = [event for event in events if _event_name(event) in ("start", "build_start")]
    ends = [event for event in events if _event_name(event) in ("end", "build_end")]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Trace requires explicit start and end events")
    if starts[0].get("mode") != "live" or ends[0].get("status") != "exported":
        raise ValueError("Offline, generated-only or failed traces cannot establish final deployment")
    if events[0] is not starts[0] or events[-1] is not ends[0]:
        raise ValueError("Trace start and end must bound all recorded events")
    if ends[0].get("export_sha256") != digest(workflow):
        raise ValueError("Selected trace does not match the submitted workflow export")
    times = []
    for event in events:
        try:
            timestamp = dt.datetime.fromisoformat(event["time"])
            if timestamp.utcoffset() is None:
                raise ValueError("timezone absent")
        except (KeyError, TypeError, ValueError):
            raise ValueError("Trace events require timezone-aware start/end and event times") from None
        times.append(timestamp)
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise ValueError("Trace event times must be ordered")
    if (times[-1] - times[0]).total_seconds() > 1800:
        raise ValueError("Selected builder trace exceeds the organizer's 30-minute budget")
    model_calls = [event for event in events if _event_name(event) == "model_call"]
    if not model_calls:
        raise ValueError("Live builder trace requires its model-call records")
    for call in model_calls:
        if not _nonempty(call.get("model")) or not _nonempty(call.get("purpose")) or any(character in call["purpose"] for character in ("\n", "\r")):
            raise ValueError("Each model call requires its model and one-line purpose")
    if not any(type(call.get("prompt_characters")) is int and call["prompt_characters"] > 0 for call in model_calls):
        raise ValueError("Live builder trace requires substantive generation, not only isolation audit calls")
    latest_deployment = None
    latest_tests = None
    for event in events[1:-1]:
        kind = _event_name(event)
        if kind == "deployment":
            if (not _nonempty(event.get("workflow_id")) or not _nonempty(event.get("agent_id"))
                    or not _nonempty(event.get("version_id")) or type(event.get("version_number")) is not int
                    or event["version_number"] < 1 or event.get("published") is not True):
                raise ValueError("Trace deployment requires published workflow, agent and version identifiers")
            latest_deployment = event
            latest_tests = None
        elif kind == "tests":
            if latest_deployment is None:
                raise ValueError("Trace tests require a preceding deployment")
            _validate_trace_tests(event.get("result"), latest_deployment, workflow)
            latest_tests = event["result"]
    if not latest_deployment or any(latest_deployment.get(key) != workflow[key] for key in ("workflow_id", "agent_id")):
        raise ValueError("Selected deployment identifiers do not match the export")
    if events.index(model_calls[-1]) > events.index(latest_deployment):
        raise ValueError("Recorded generation must precede the selected deployment")
    if any(_event_name(event) == "tests" for event in events) and latest_tests is None:
        raise ValueError("Selected deployment has no recorded tests after its final update")
    if latest_tests and (latest_tests.get("status") not in ("completed", "passed") or latest_tests["passed"] != latest_tests["total"]):
        raise ValueError("Selected deployment's final recorded tests did not pass")


def _validate_trace_tests(result, deployment, workflow):
    """Check recorded hosted references while allowing earlier failed repair runs."""
    if (not isinstance(result, dict) or type(result.get("total")) is not int or result["total"] < 1
            or type(result.get("passed")) is not int or not 0 <= result["passed"] <= result["total"]
            or type(result.get("reps")) is not int or result["reps"] < 1
            or not isinstance(result.get("rows"), list) or not isinstance(result.get("simulations"), list)):
        raise ValueError("Trace test result requires counts, repetitions and hosted evidence references")
    simulations, simulation_ids, iteration_ids = {}, set(), set()
    for simulation in result["simulations"]:
        if (not isinstance(simulation, dict) or type(simulation.get("repetition")) is not int
                or not 1 <= simulation["repetition"] <= result["reps"] or simulation["repetition"] in simulations
                or any(simulation.get(key) is not None and not _nonempty(simulation[key]) for key in ("simulation_id", "iteration_id"))):
            raise ValueError("Trace test simulation references are missing or invalid")
        simulations[simulation["repetition"]] = simulation
        if (simulation.get("simulation_id") is not None and simulation["simulation_id"] in simulation_ids
                or simulation.get("iteration_id") is not None and simulation["iteration_id"] in iteration_ids):
            raise ValueError("Trace test repetitions reuse simulation/iteration evidence")
        if simulation.get("simulation_id") is not None:
            simulation_ids.add(simulation["simulation_id"])
        if simulation.get("iteration_id") is not None:
            iteration_ids.add(simulation["iteration_id"])
    seen, calls, fixture_passes = set(), set(), {}
    for row in result["rows"]:
        if (not isinstance(row, dict) or not _nonempty(row.get("fixture_id")) or type(row.get("repetition")) is not int
                or not 1 <= row["repetition"] <= result["reps"] or type(row.get("pass")) is not bool):
            raise ValueError("Trace test row has no valid fixture/repetition evidence reference")
        reference = (row["fixture_id"], row["repetition"])
        if reference in seen:
            raise ValueError("Trace test rows repeat a fixture/repetition reference")
        seen.add(reference)
        fixture_passes.setdefault(row["fixture_id"], {})[row["repetition"]] = row["pass"]
        simulation = simulations.get(row["repetition"])
        if simulation and any(key in row and row[key] != simulation.get(key) for key in ("simulation_id", "iteration_id")):
            raise ValueError("Trace test row differs from its simulation/iteration reference")
        if row["pass"]:
            if (not simulation or not _nonempty(row.get("call_id")) or simulation.get("status") != "completed"
                    or any(not _nonempty(simulation.get(key)) for key in ("simulation_id", "iteration_id"))):
                raise ValueError("Passing trace test row requires a completed simulation and call reference")
            if any(row.get(key) != simulation[key] for key in ("simulation_id", "iteration_id")):
                raise ValueError("Passing trace test row requires its simulation/iteration identifiers")
            if row["call_id"] in calls:
                raise ValueError("Passing trace test rows reuse call evidence")
            calls.add(row["call_id"])
            judge = row.get("judge")
            if not isinstance(judge, dict) or judge.get("callId") != row["call_id"] or judge.get("outcome") != "pass":
                raise ValueError("Passing trace test row requires matching judge call evidence")
            if not isinstance(row.get("ledger"), dict) or not row["ledger"]:
                raise ValueError("Passing trace test row requires its retained ledger")
            transcript = row.get("transcript")
            if not isinstance(transcript, dict) or not isinstance(transcript.get("messages"), list) or not transcript["messages"]:
                raise ValueError("Passing trace test row requires its retained transcript messages")
            if any(key in transcript and transcript[key] != row["call_id"] for key in ("id", "callId")):
                raise ValueError("Trace test transcript differs from its judged call")
            from benchmarks.hosted import attribute_version
            attribution, failures = attribute_version(judge, transcript, deployment["version_number"])
            if failures or not attribution["verified"]:
                raise ValueError("Passing trace test row lacks matching per-call version proof")
    complete = (len(fixture_passes) == result["total"] and len(seen) == result["total"] * result["reps"]
                and all(set(repetitions) == set(range(1, result["reps"] + 1)) for repetitions in fixture_passes.values()))
    if result["passed"] == result["total"] and not complete:
        raise ValueError("Trace test evidence is incomplete for its declared repetitions")
    proven_passes = sum(set(repetitions) == set(range(1, result["reps"] + 1)) and all(repetitions.values())
                        for repetitions in fixture_passes.values())
    if result["passed"] != proven_passes:
        raise ValueError("Trace test summary differs from its recorded repetition outcomes")
    if result["passed"] == result["total"]:
        pin = result.get("deployment_pin")
        if (not isinstance(pin, dict) or pin.get("verified") is not True or pin.get("per_call_version_verified") is not True
                or not isinstance(pin.get("before"), dict) or not isinstance(pin.get("after"), dict)
                or pin["before"] != pin["after"]):
            raise ValueError("Passing trace tests require verified before/after deployment and per-call pins")
        for checked in (pin["before"], pin["after"]):
            if (any(checked.get(key) != deployment[key] for key in ("workflow_id", "agent_id", "version_id"))
                    or type(checked.get("version_number")) is not int or checked["version_number"] != deployment["version_number"]
                    or checked.get("published") is not True or checked.get("graph_sha256") != digest(workflow["workflow"])):
                raise ValueError("Passing trace test deployment pin differs from the selected export/version")


def safe_path(path):
    if not isinstance(path, (str, Path)) or not str(path):
        raise ValueError('Submission evidence requires a file path')
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Submission path escapes the standalone repository")
    cursor = path
    while cursor != ROOT and cursor.is_relative_to(ROOT):
        if cursor.is_symlink():
            raise ValueError("Submission symlinks are forbidden")
        cursor = cursor.parent
    if not path.is_file():
        raise ValueError("Submission evidence file does not exist")
    return resolved


def source_files():
    result = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if (any(p in SKIP_PARTS for p in rel.parts) or path.name in SKIP_NAMES
                or path.name.startswith('.env.') and path.name != '.env.example'):
            continue
        included = (len(rel.parts) == 1 and path.name in ROOT_FILES) or (
            rel.parts[0] in SOURCE_DIRS and (path.suffix in EXTENSIONS or path.name in {"VERSION", "env.example"}))
        if included and (path.is_file() or path.is_symlink()):
            result.append(safe_path(path))
    return result


def check_content(path, *, evidence=False):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    # An overridden local key is still private, and short opaque credentials
    # must not bypass exact-value exclusion.
    credentials = [*read_local_environment(ROOT / '.env').items(), *os.environ.items()]
    known = tuple(v for k, v in credentials if v and any(s in k.upper()
                  for s in ('API_KEY', 'TEAM_KEY', 'TOKEN', 'SECRET', 'TOOL_AUTH', 'PASSWORD')))
    # Test source intentionally contains synthetic token examples. Known live
    # values are always forbidden; production source/evidence also gets patterns.
    if not evidence and path.relative_to(ROOT).parts[0] == "tests" and path.suffix == '.py':
        if any(value in text for value in known):
            raise ValueError("Credential detected in test source")
    else:
        try:
            assert_no_secrets(text, forbidden_values=known)
            if path.suffix == '.json':
                assert_no_secrets(read_json(path), forbidden_values=known)
            elif path.suffix == '.jsonl':
                for line in text.splitlines():
                    if line.strip():
                        assert_no_secrets(strict_json(line), forbidden_values=known)
        except ValueError as exc:
            raise ValueError(f"{path.relative_to(ROOT)}: credential-like material requires review") from exc
    return hashlib.sha256(raw).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON keys are forbidden')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON values are forbidden')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read_evidence(path):
    path = safe_path(path)
    check_content(path, evidence=True)
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError('Evidence JSON must be an object')
    return path, value


def validate_provenance(path, run, events):
    """Bind original invocation records to matching runtime bytes, not an invented invocation commit.

    The controller records source bytes/configuration, not git HEAD. The final
    submission commit is checked separately against all packaged files.
    """
    source_path, source = read_evidence(path.parent / 'builder-source.json')
    provenance_path, provenance = read_evidence(path.parent / 'build-provenance.json')
    if type(source.get('version')) is not int or source['version'] != 1:
        raise ValueError('Original invocation source record requires version 1')
    if source.get('files') != _source_files(ROOT):
        raise ValueError('Invocation runtime source inventory differs from the submitted source')
    config = source.get('resolved_config')
    if (not isinstance(config, dict) or config.get('mode') != 'live'
            or config.get('controller_dependency_injection') is not False
            or type(config.get('deadline_seconds')) not in (int, float) or config['deadline_seconds'] != 1800
            or type(config.get('export_reserve_seconds')) not in (int, float) or config['export_reserve_seconds'] != 300
            or not isinstance(config.get('python_version'), str)
            or not re.fullmatch(r'3\.11\.[0-9]+', config['python_version'])
            or config.get('python_implementation') != 'CPython'
            or not _nonempty(config.get('model')) or not _nonempty(config.get('codex_executable'))
            or config.get('reasoning_effort') not in {'low', 'medium', 'high', 'xhigh'}
            or type(config.get('max_repairs')) is not int or config['max_repairs'] != 2):
        raise ValueError('Invocation resolved configuration requires real live Python 3.11 generation and 1800/300 budget')
    if (run.get('model') != config['model'] or run.get('reasoning_effort') != config['reasoning_effort']
            or events[0].get('model') != config['model']):
        raise ValueError('Generation model/configuration differs from the original invocation')
    if digest(provenance) != digest(bind_run_provenance(source, run, events)):
        raise ValueError('Original build provenance does not bind the source record, manifest and trace')
    snapshots = [event for event in events if _event_name(event) == 'builder_source_snapshot']
    calls = [event for event in events if _event_name(event) == 'model_call']
    if (len(snapshots) != 1 or snapshots[0].get('builder_source_sha256') != source.get('builder_source_sha256')
            or snapshots[0].get('builder_source_record_sha256') != digest(source)
            or digest(snapshots[0].get('resolved_config')) != digest(config)
            or events.index(snapshots[0]) >= events.index(calls[0])):
        raise ValueError('Trace requires original source/configuration binding before every model call')
    try:
        captured = dt.datetime.fromisoformat(source['captured_at'])
        if captured.utcoffset() is None:
            raise ValueError()
        if not (dt.datetime.fromisoformat(events[0]['time']) <= captured
                <= dt.datetime.fromisoformat(snapshots[0]['time'])):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Original source capture timestamp must precede its trace snapshot inside the run') from None
    for call in calls:
        if call.get('model') != config['model'] or call.get('reasoning_effort') != config['reasoning_effort']:
            raise ValueError('Trace model calls differ from the invocation generation configuration')
    audits = [event for event in events if _event_name(event) == 'generation_isolation_audit']
    substantive = [event for event in calls if type(event.get('prompt_characters')) is int and event['prompt_characters'] > 0]
    if (len(audits) != 1 or events.index(audits[0]) >= events.index(substantive[0])
            or events.index(audits[0]) <= events.index(calls[0])):
        raise ValueError('Mandatory tool-free isolation audit must pass before substantive generation')
    audit = audits[0]
    if (audit.get('status') != 'pass' or audit.get('failures') != [] or audit.get('canary_exposure') != []
            or audit.get('response_valid') is not True or audit.get('model_reported_tools') != []
            or any(type(audit.get(key)) is not int or audit[key] != expected for key, expected in (
                ('returncode', 0), ('model_reported_tool_count', 0), ('unrecognized_tool_name_count', 0),
                ('omitted_skill_count', 0), ('completed_turn_count', 1)))
            or not isinstance(audit.get('observed_item_types'), list)
            or any(kind not in {'reasoning', 'agent_message', 'error'} for kind in audit['observed_item_types'])
            or not isinstance(audit.get('model_catalog'), dict)):
        raise ValueError('Mandatory generation isolation audit lacks passing tool-free evidence')
    catalog = audit['model_catalog']
    restrictions = {'shell_type': 'disabled', 'apply_patch_tool_type': None,
                    'experimental_supported_tools': [], 'node_repl_disabled': True,
                    'tool_mode': 'direct', 'supports_search_tool': False}
    catalog_events = [event for event in events if _event_name(event) == 'generation_model_catalog']
    if (catalog.get('source') != 'installed_cli_bundled' or catalog.get('model') != config['model']
            or digest(catalog.get('restricted_capabilities')) != digest(restrictions)
            or any(not isinstance(catalog.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', catalog[key])
                   for key in ('source_model_sha256', 'generation_catalog_sha256'))
            or len(catalog_events) != len(substantive)
            or any(digest({key: event.get(key) for key in catalog}) != digest(catalog) for event in catalog_events)):
        raise ValueError('Generation model catalog differs from the tool-free audit/configuration')
    for call in substantive:
        start = events.index(call)
        next_calls = [events.index(later) for later in calls if events.index(later) > start]
        stop = min(next_calls) if next_calls else len(events)
        if sum(start < events.index(event) < stop for event in catalog_events) != 1:
            raise ValueError('Each substantive generation call requires its matching restricted model catalog')
    return [source_path, provenance_path]


def validate_run(path, run, workflow, events, inputs):
    """Bind retained controller evidence; this does not authenticate live calls."""
    if run.get('mode') != 'live' or run.get('status') != 'exported':
        raise ValueError('Selected build manifest must describe a successful live export')
    graph = workflow.get('workflow')
    agent_config = graph.get('agent_config') if isinstance(graph, dict) else None
    llm = agent_config.get('llmConfig') if isinstance(agent_config, dict) else None
    if not isinstance(llm, dict) or any(llm.get(key) != 'experimental-5' for key in ('provider', 'model')):
        raise ValueError('Selected published export requires the fixed experimental-5 phone model')
    if not isinstance(run.get('deployment'), dict):
        raise ValueError('Selected build manifest requires its deployment record')
    if (type(run['deployment'].get('version_number')) is not int
            or run['deployment'].get('published') is not True
            or not isinstance(run.get('policy_contract_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', run['policy_contract_sha256'])):
        raise ValueError('Selected build requires published version and policy contract fingerprint')
    number = run.get('selected_attempt')
    directory = f'attempts/plan-{number:03d}' if type(number) is int and 1 <= number <= 3 else None
    if directory is None or run.get('selected_attempt_directory') != directory:
        raise ValueError('Selected attempt requires its canonical bounded attempt directory')
    attempt_dir = path.parent / directory
    retained = []
    for filename in ('attempt.json', 'export.json', 'deployment.json'):
        evidence_path, value = read_evidence(attempt_dir / filename)
        retained.append(evidence_path)
        if filename == 'attempt.json':
            if (type(value.get('number')) is not int or value.get('number') != number or value.get('directory') != directory
                    or value.get('status') != 'exported' or value.get('phase') != 'complete'
                    or value.get('export_sha256') != digest(workflow)
                    or any(value.get(key) != run.get(key) for key in ('tool_api_sha256', 'policy_contract_sha256'))):
                raise ValueError('Selected attempt record differs from the successful build manifest/export')
        elif filename == 'export.json' and digest(value) != digest(workflow):
            raise ValueError('Selected attempt export differs from the submitted export')
        elif filename == 'deployment.json':
            if type(value.get('version_number')) is not int or value.get('published') is not True:
                raise ValueError('Selected attempt requires a published deployment version')
            if any(value.get(key) != run.get('deployment', {}).get(key)
                   for key in ('workflow_id', 'agent_id', 'version_id', 'version_number', 'published')):
                raise ValueError('Selected attempt deployment differs from the build manifest')
            if digest(value.get('workflow')) != digest(workflow.get('workflow')):
                raise ValueError('Selected attempt published graph differs from the export')
    deployments = [event for event in events if _event_name(event) == 'deployment']
    selected = deployments[-1]
    if (type(selected.get('attempt')) is not int or selected.get('attempt') != number or run.get('export_sha256') != digest(workflow)
            or any(selected.get(key) != run.get('deployment', {}).get(key)
                   for key in ('workflow_id', 'agent_id', 'version_id', 'version_number', 'published'))):
        raise ValueError('Selected attempt/build manifest differs from the final trace deployment/export')
    test_events = [event for event in events[events.index(selected) + 1:] if _event_name(event) == 'tests']
    if test_events:
        for event in test_events:
            if type(event.get('attempt')) is not int or event.get('attempt') != number or event.get('attempt_directory') != directory:
                raise ValueError('Selected trace tests refer to another attempt')
        test_path, tests = read_evidence(attempt_dir / 'tests.json')
        retained.append(test_path)
        if digest(tests) != digest(run.get('tests')) or digest(tests) != digest(test_events[-1].get('result')):
            raise ValueError('Selected attempt tests differ from the manifest/trace')
    elif run.get('tests') is not None:
        raise ValueError('Build manifest tests are absent from the selected trace')
    if run.get('hosted_behavior_verified') is not bool(test_events):
        raise ValueError('Build manifest hosted evidence claim differs from its trace tests')
    elapsed = run.get('elapsed_seconds')
    remaining = events[-1].get('remaining_seconds')
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (elapsed, remaining)) or elapsed > 1800 or remaining > 1800:
        raise ValueError('Successful build requires finite in-budget deadline evidence')
    trace_duration = (dt.datetime.fromisoformat(events[-1]['time']) - dt.datetime.fromisoformat(events[0]['time'])).total_seconds()
    # elapsed is rounded before writing the manifest; the end event follows it.
    if elapsed + 1 < trace_duration or elapsed + remaining > 1800.01:
        raise ValueError('Build manifest and trace deadline evidence disagree')
    if not isinstance(inputs, dict) or not isinstance(inputs.get('briefs'), list) or not inputs['briefs']:
        raise ValueError('Selected build requires brief and tool API input paths')
    sources = []
    for index, entry in enumerate(inputs['briefs'], 1):
        brief = safe_path(entry); check_content(brief, evidence=True)
        retained.append(brief)
        sources.append({'id': f'brief-{index}', 'file': brief.name, 'sha256': digest(brief.read_text(encoding='utf-8'))})
    api_path, api = read_evidence(inputs.get('tool_api', ''))
    retained.append(api_path)
    input_events = [event for event in events if _event_name(event) == 'inputs']
    if (len(input_events) != 1 or run.get('sources') != sources or input_events[0].get('sources') != sources
            or run.get('tool_api_sha256') != digest(api) or input_events[0].get('tool_api_sha256') != digest(api)):
        raise ValueError('Input fingerprints differ from the selected build manifest/trace')
    retained.extend(validate_provenance(path, run, events))
    retained.extend(validate_generated_artifacts(path, run, events))
    return retained


def validate_generated_artifacts(path, run, events):
    """Retain selected generated artifacts and check their recorded fingerprints."""
    directory = path.parent / run['selected_attempt_directory']
    retained, artifacts = [], {}
    for filename in ('plan.json', 'action-bindings.json', 'execution-policy.json', 'guard-context.json',
                     'guard-manifest.json', 'workflow.json', 'proposal.json'):
        artifact_path, artifact = read_evidence(directory / filename)
        retained.append(artifact_path)
        artifacts[filename] = artifact
    policy_path, policy = read_evidence(path.parent / 'policy.json')
    contract_path, contract = read_evidence(path.parent / 'policy.contract.json')
    retained.extend([policy_path, contract_path])
    attempt = read_json(directory / 'attempt.json')
    fingerprinted = {key: value for key, value in contract.items() if key != 'sha256'}
    if (contract.get('sha256') != digest(fingerprinted) or contract.get('sha256') != run['policy_contract_sha256']
            or attempt.get('policy_sha256') != digest(policy)):
        raise ValueError('Original policy/contract artifacts differ from their recorded fingerprints')
    if (run.get('action_guard_compilation_verified') is not True
            or run.get('plan_sha256') != digest(artifacts['plan.json'])
            or attempt.get('plan_sha256') != digest(artifacts['plan.json'])
            or run.get('guard_manifest_sha256') != digest(artifacts['guard-manifest.json'])
            or run.get('authored_workflow_sha256') != digest(artifacts['workflow.json'])
            or attempt.get('workflow_sha256') != digest(artifacts['workflow.json'])):
        raise ValueError('Selected generated plan, guards or authored workflow differ from recorded fingerprints')
    proposal = artifacts['proposal.json']
    context = artifacts['guard-context.json']
    if (proposal.get('contract_sha256') != contract['sha256'] or digest(proposal.get('plan')) != digest(artifacts['plan.json'])
            or digest(context.get('plan')) != digest(artifacts['plan.json']) or digest(context.get('contract')) != digest(contract)
            or digest(context.get('bindings')) != digest(artifacts['action-bindings.json'])
            or digest(context.get('execution_policy')) != digest(artifacts['execution-policy.json'])):
        raise ValueError('Selected generated proposal and guard context differ from their retained artifacts')
    compilation = [event for event in events if _event_name(event) == 'action_guard_compilation']
    if (not compilation or compilation[-1].get('verified') is not True
            or any(compilation[-1].get(key) != run.get(key) for key in ('plan_sha256', 'guard_manifest_sha256'))):
        raise ValueError('Selected generated artifact fingerprints differ from final compilation trace')
    return retained


def repository_url():
    """Resolve the declared repository from this checkout's origin without network access."""
    origin = subprocess.run(['git', 'config', '--get', 'remote.origin.url'], cwd=ROOT,
                            text=True, capture_output=True, check=True).stdout.strip()
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', origin)
    if not match:
        raise ValueError('Source origin must identify its GitHub repository')
    return 'https://github.com/' + match[1]


def check_manifest(value):
    if not isinstance(value, dict):
        raise ValueError('Submission manifest must be an object')
    for key in ("team", "repo", "commit", "run_command", "workflows", "traces", "runs", "inputs"):
        if not value.get(key):
            raise ValueError(f"Submission manifest missing {key}")
    for key in ('team', 'repo', 'run_command'):
        if not _nonempty(value[key]):
            raise ValueError(f'Submission {key} must be a nonempty string')
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', value['repo']):
        raise ValueError('Submission repo must be a GitHub repository URL')
    if value['repo'].rstrip('/').removesuffix('.git').casefold() != repository_url().casefold():
        raise ValueError('Submission repo differs from the source checkout origin')
    if not isinstance(value['commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', value['commit']):
        raise ValueError('Submission commit must be the full source HEAD commit')
    for key in ('workflows', 'traces', 'runs', 'inputs'):
        if not isinstance(value[key], dict) or set(value[key]) != {'hidden-1', 'hidden-2'}:
            raise ValueError('Require exactly two workflows, traces, build manifests and input sets')
    for kind in ('workflows', 'traces', 'runs'):
        if len({safe_path(entry) for entry in value[kind].values()}) != 2:
            raise ValueError(f'Two hidden builds require distinct {kind} files')
    bundles = []
    for inputs in value['inputs'].values():
        if not isinstance(inputs, dict) or not isinstance(inputs.get('briefs'), list) or not inputs['briefs']:
            raise ValueError('Each hidden build requires ordered brief paths and a tool API')
        bundles.append(digest({'briefs': [safe_path(entry).read_text(encoding='utf-8') for entry in inputs['briefs']],
                               'tool_api': read_json(safe_path(inputs.get('tool_api', '')))}))
    if len(set(bundles)) != 2:
        raise ValueError('Two hidden builds require distinct input bundles')
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    if value['commit'] != head:
        raise ValueError("Manifest commit does not match the source checkout")
    result, exported, sources = [], {}, set()
    for kind in ("workflows", "traces"):
        for name, entry in value[kind].items():
            path = safe_path(entry); check_content(path, evidence=True)
            if kind == "workflows":
                workflow = read_json(path)
                if not isinstance(workflow, dict) or not workflow.get("workflow_id") or not workflow.get("agent_id") or not workflow.get("workflow"):
                    raise ValueError("Require the actual platform-exported workflow envelope")
                exported[name] = workflow
            else:
                events = [strict_json(line) for line in path.read_text().splitlines() if line.strip()]
                validate_trace(events, exported[name])
            result.append((path, f"{kind}/{name}{''.join(path.suffixes)}"))
    for name, entry in value['runs'].items():
        path, run = read_evidence(entry)
        events = [strict_json(line) for line in safe_path(value['traces'][name]).read_text().splitlines() if line.strip()]
        retained = validate_run(path, run, exported[name], events, value['inputs'][name])
        sources.add(run['builder_source_sha256'])
        result.append((path, f'runs/{name}/manifest.json'))
        for retained_path in retained:
            if retained_path.is_relative_to(path.parent):
                arcname = f'runs/{name}/{retained_path.relative_to(path.parent).as_posix()}'
            else:
                arcname = retained_path.relative_to(ROOT).as_posix()
            result.append((retained_path, arcname))
    if len(sources) != 1:
        raise ValueError('Both hidden builds must use the same builder source and resolved configuration')
    return result


def verify_committed(paths):
    """Bind the packaged source/evidence bytes to the declared HEAD commit."""
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        result = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
        if result.returncode or result.stdout != path.read_bytes():
            raise ValueError(f"{rel}: final source/evidence must match the committed checkout")


def archive_plan(files, evidence):
    """Check all names before validation succeeds or an output is opened."""
    archived = {}
    entries = [(path, path.relative_to(ROOT).as_posix()) for path in files] + evidence
    for path, name in entries:
        if name == 'package-index.json':
            raise ValueError('Package evidence collides with reserved package-index.json')
        if not isinstance(name, str) or Path(name).is_absolute() or any(part in ('..', '.') for part in name.split('/')):
            raise ValueError('Package archive path must be a relative confined file path')
        if name in archived and archived[name] != path:
            raise ValueError('Package source/evidence paths collide')
        archived[name] = path
    return archived


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--output", type=Path, default=ROOT / "artifacts" / "source-rehearsal.zip")
    args = ap.parse_args(argv)
    if args.source_only and args.manifest:
        ap.error("--source-only and --manifest are mutually exclusive")
    if not args.check and not args.source_only and not args.manifest:
        ap.error("Packaging requires --manifest, or explicit --source-only for a rehearsal")
    files = source_files(); hashes = {str(p.relative_to(ROOT)): check_content(p) for p in files}
    evidence, manifest = [], None
    if args.manifest:
        manifest_path = safe_path(args.manifest); check_content(manifest_path, evidence=True)
        manifest = read_json(manifest_path); evidence = check_manifest(manifest)
    archived = archive_plan(files, evidence)
    evidence_hashes = {name: check_content(path, evidence=True) for name, path in archived.items() if name not in hashes}
    if args.manifest:
        verify_committed(files + [path for path, _ in evidence])
    if args.check:
        print(json.dumps({"source_check": "pass", "files": len(files), "export_trace_consistency": "checked" if manifest else "pending", "hosted_readiness": "requires scored runtime evidence; source checks are not competition readiness"}))
        return 0
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Package output must remain inside the repository")
    if output in files + [path for path, _ in evidence] or args.manifest and output == manifest_path:
        raise ValueError('Package output must not overwrite source or evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    packaged_manifest = None if manifest is None else json.loads(json.dumps(manifest))
    source_binding = None
    if packaged_manifest is not None:
        for kind in ("workflows", "traces"):
            packaged_manifest[kind] = {name: f"{kind}/{name}{''.join(safe_path(entry).suffixes)}"
                                       for name, entry in manifest[kind].items()}
        packaged_manifest['runs'] = {name: f'runs/{name}/manifest.json' for name in manifest['runs']}
        archive_paths = {path: arcname for path, arcname in evidence}
        packaged_manifest['inputs'] = {
            name: {'briefs': [archive_paths[safe_path(entry)] for entry in inputs['briefs']],
                   'tool_api': archive_paths[safe_path(inputs['tool_api'])]}
            for name, inputs in manifest['inputs'].items()}
        source_binding = {'submission_commit': manifest['commit'], 'invocation_commit': None,
                          'invocation_commit_status': 'Controller records runtime source bytes, not invocation git HEAD',
                          'builder_source_sha256': {name: read_json(safe_path(entry))['builder_source_sha256']
                                                    for name, entry in manifest['runs'].items()}}
    descriptor, temporary_name = tempfile.mkstemp(prefix='.submission-', suffix='.zip', dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, path in archived.items():
                out.write(path, name)
            out.writestr("package-index.json", json.dumps({"source_files": hashes,
                "evidence_files": evidence_hashes,
                "source_only": manifest is None, "submission": packaged_manifest, "source_binding": source_binding}, indent=2))
        with zipfile.ZipFile(temporary) as out:
            if len(out.namelist()) != len(set(out.namelist())):
                raise ValueError('Package archive contains duplicate paths')
            for name, expected in {**hashes, **evidence_hashes}.items():
                if hashlib.sha256(out.read(name)).hexdigest() != expected:
                    raise ValueError('Package payload changed after fingerprint verification')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"package": str(output), "source_only": manifest is None, "files": len(files)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
