"""Report workflow shapes that verified Freya platform behaviour breaks.

Report only: a finding never rejects or changes a candidate. Each rule names the
kit document or the hosted evidence (issue 8 comments) it rests on. Local replay
cannot show these failures: it uses evaluator-chosen frames, whole values at full
confidence and no sandbox limits. A clean lint is not hosted evidence either.
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Any

from .guards import PREFIX, execution_action_catalogue
from .validation import _router_judged, code_write_schema_errors

PAYLOAD_LIMIT = 131072  # "Code and data for code_exec total 131235 bytes, over the 131072-byte limit"
RUNTIME_DATA = 48255  # runtime data when a hosted result step first failed (comment 6110207779)
GROWTH_PER_LOOP = 1526  # runtime data added per guarded action loop in that call
MIN_LOOPS = 20
MIN_TURNS = 20
MAX_CHECKED_CONFIDENCE = 0.8
READ_ACTIONS = {"verify_identity", "get_account"}

EVIDENCE = {
    "routing_prompt_on_intent_node": "kit workflow-format.md routing_prompt: it replaces the router's intent instruction; "
        "probes 6109617253 Q1: with it 12/12 calls took the first row, without it 9/9 routed correctly; round 1 R1: 54 empty routing errors",
    "same_target_with_always": "kit workflow-format.md Order of evaluation: same-target rows merge at load time and the merged row "
        "is unconditional if one is; platform audit N1",
    "enum_write_refused": "probes 6109617253 Q3: a code write outside a variable's declared enum_values (checked by name across the "
        "workflow, '' included) is dropped with validation_errors; first check: 27 dropped consent resets (6109482247 F7)",
    "code_payload_limit": "rounds 1-2 R4/S2: 'Code and data for code_exec total 131235 bytes, over the 131072-byte limit'; runtime "
        "data was 48,255 bytes at the first failure and grew 1,526 bytes per loop (6110207779)",
    "controller_known_input": "diagnosis 6109482247 F2: an extracted identity field was never extracted and every verify was refused; "
        "round 2 S4: an extracted field refused later answers (order); S5: an extracted close proposal was refused into quarantine",
    "checked_value_confidence": "kit workflow-format.md Extraction schema: confidence comes from token probabilities and a value below "
        "min_confidence is dropped without trace; first check: a spoken birth date dropped at 0.889 < 0.90 (F3); probes Q2: 0.974 in words",
    "date_year_not_required": "probes 6109617253 Q2: a date said without its year was stored with the current year at confidence 1.0; "
        "ws2 pieces pass P1 (6109931493): placeholder-year dates went to the bank",
    "relative_date_platform_clock": "kit workflow-format.md: date extraction anchors to the real clock; rounds 1 and ws2 R8: 'tomorrow' "
        "resolved against the platform date, not the call's now 3 days later, and a valid callback was refused",
    "split_value_not_joined": "probes 6109617253 Q2: each half of a 4-digit value failed validation_pattern and the halves were never "
        "joined; the first half of a 4-digit amount was accepted alone as the amount; ws2 P1: fragments were sent as identity answers",
    "answer_format_unstated": "round 2 S3: an answer whose extraction text did not name the API's format went out in words and the bank "
        "refused it as a format error",
    "no_tool_line_missing": "kit workflow-format.md Rules for task_message: a node without tools states that no tool call is allowed in "
        "this turn (in loop_prompt too); diagnosis F5: a tool-less node spoke pseudo tool calls",
    "speak_after_tool_result_missing": "kit tools-and-code.md Tool descriptions and Tool results and the next turn; platform audit N6: "
        "two 'no speakable content' errors, then silence until the 420 s cap",
    "turn_cap_low": "kit workflow-format.md Limits: size the cap from real conversations; round 1 (6109852909): the cap counts caller "
        "turns per entry and 6 calls ended silently at 10; ws2 P1: split answers reach it sooner",
    "end_without_close": "the scorer requires close_call as the last bank call; diagnosis F6, audit N3 and round 1 R2: refused-close and "
        "turn-cap paths ended at a silent terminal with no close_call",
    "stale_allow_redispatch": "round 1 R4 (6109852909, 6110116409): a failed code step writes nothing, so its own allow row re-sent the bank request "
        "(30 resends in 40 s); round 2 S2: 52 verify resends and get_account x49",
    "platform_clock_reference": "kit workflow-format.md System variables: system dates and extraction date placeholders are the real "
        "clock; R8 (6110032192): the platform date was 3 days before the call's now",
    "array_result_mapping": "round 2 S1 (6110250735): a response_mapping of an array result wrote '' on the platform and the sealed "
        "result step quarantined the call after the first good account read",
}
_NO_TOOL = re.compile(r"no tool calls? (?:is|are) allowed|\b(?:do not|don't|never|not)\b[^.\n]*\bcall(?:ing)? (?:a |any )?tools?\b", re.I)
_SPEAK_AFTER = re.compile(r"speak after every tool result|after every tool result, (?:speak|follow)|never end a turn with (?:nothing|neither)", re.I)
_JOIN = re.compile(r"\b(?:join|combine|concatenat\w*|pieces?|consecutive|in (?:two |several )?parts|across (?:caller )?turns|split)\b", re.I)
_DIGITS_ONLY = re.compile(r"\^?(?:\\d|\[0-9\])(?:\{\d+(?:,\d*)?\}|\+)\$?")
_DATE_PATTERN = re.compile(r"(?:\\d|\[0-9\])\{4\}-")
_CLOCK = re.compile(r"\{\{\s*(system\.todays_date[^}]*|system\.iso_utc_now|date\.\w+|time\.now)\s*\}\}")
_SPEC = re.compile(r"spec = json\.loads\(('(?:[^'\\]|\\.)*')\)")
_FORMATS = ("yyyy-mm-dd", "hh:mm", "digit")


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _leaves(condition: Any):
    if isinstance(condition, dict):
        children = condition.get("conditions")
        if isinstance(children, list):
            for child in children:
                yield from _leaves(child)
        else:
            yield condition


def _always(row: dict) -> bool:
    return not row.get("is_tool_call") and any(leaf.get("operator") == "always" for leaf in _leaves(row.get("condition")))


def _tests(row: dict, variables: set, operand: str | None = None) -> bool:
    return any(leaf.get("variable") in variables and (operand is None or str(leaf.get("operand")) == operand)
               for leaf in _leaves(row.get("condition")))


def _functions(node: dict):
    for placement in ("pre_actions", "tools"):
        for tool in _list(node.get(placement)):
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
                yield placement, tool


def _code(tool: dict) -> str | None:
    function = tool["function"]
    if function.get("handler") != "__function__:code_exec":
        return None
    code = ((function.get("parameters") or {}).get("properties") or {}).get("code") or {}
    return code.get("default") if isinstance(code.get("default"), str) else None


def _spec(code: str) -> dict | None:
    """The guard compiler's embedded step spec (phase, action, inputs, catalog), if this is a sealed step."""
    match = _SPEC.search(code)
    try:
        return json.loads(ast.literal_eval(match.group(1))) if match else None
    except (ValueError, SyntaxError, TypeError):
        return None


def lint_workflow(workflow: Any, policy: Any = None) -> dict:
    """Return findings (rule, node, severity, detail, evidence) and their counts."""
    workflow = workflow.get("workflow", workflow) if isinstance(workflow, dict) else {}
    workflow = workflow if isinstance(workflow, dict) else {}
    globals_ = workflow.get("global_nodes") if isinstance(workflow.get("global_nodes"), dict) else {}
    authored = workflow.get("nodes") if isinstance(workflow.get("nodes"), dict) else {}
    nodes = {name: node for name, node in {**authored, **globals_}.items() if isinstance(node, dict)}
    policy_rules = {rule.get("kind"): rule.get("value") for rule in _list(policy.get("rules") if isinstance(policy, dict) else None)
                    if isinstance(rule, dict)}
    findings = []

    def add(rule: str, node: str, severity: str, detail: str) -> None:
        finding = {"rule": rule, "node": node, "severity": severity, "detail": detail, "evidence": EVIDENCE[rule]}
        if finding not in findings:
            findings.append(finding)

    codes, specs = {}, {}
    for name, node in nodes.items():
        for placement, tool in _functions(node):
            code = _code(tool)
            if code is not None:
                codes.setdefault(name, []).append(code)
                spec = _spec(code)
                if spec:
                    specs[name] = spec
    catalog = next((spec["catalog"] for spec in specs.values() if isinstance(spec.get("catalog"), dict)), {})
    by_operation = {op.get("name"): op for op in catalog.values() if isinstance(op, dict)}
    roles = {action: entry["operation_role"] for action, entry in execution_action_catalogue().items()}
    entries = {}
    for name, node in nodes.items():
        for entry in _list(node.get("extraction_schema")):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                entries.setdefault(entry["name"], []).append((name, entry))

    # Checked inputs: sealed guard inputs and model-free API body defaults that read an extracted variable.
    bindings = []  # (node, action, input field, variable, API field description)
    for name, spec in specs.items():
        if spec.get("phase") == "prepare" and isinstance(spec.get("inputs"), dict):
            operation = catalog.get(roles.get(spec.get("action"), ""), {})
            for field, variable in spec["inputs"].items():
                actual = (operation.get("fields") or {}).get(field, field)
                leaf = ((operation.get("request_schema") or {}).get("properties") or {}).get(actual) or {}
                bindings.append((name, spec["action"], field, variable, str(leaf.get("description") or "")))
    for name, node in nodes.items():
        for _, tool in _functions(node):
            function = tool["function"]
            body = ((function.get("parameters") or {}).get("properties") or {}).get("body") or {}
            for field, leaf in ((body.get("properties") or {}) if isinstance(body, dict) else {}).items():
                match = re.fullmatch(r"\{\{(\w+)\}\}", str((leaf or {}).get("default") or ""))
                if match:
                    bindings.append((name, str(function.get("name") or ""), field, match.group(1),
                                     str((leaf or {}).get("description") or "")))
    bindings = [binding for binding in bindings if binding[3] in entries]
    bound = {}
    for binding in bindings:
        bound.setdefault(binding[3], []).append(binding)

    # Routing.
    for name, node in nodes.items():
        rows = [row for row in _list(node.get("transitions")) if isinstance(row, dict)]
        ordinary = [row for row in rows if not row.get("is_tool_call")]
        if node.get("routing_prompt") and any(_router_judged(row.get("condition")) for row in ordinary):
            add("routing_prompt_on_intent_node", name, "blocker", "routing_prompt on a node with matches_intent rows")
        targets = Counter(row.get("target_node") for row in ordinary)
        for target, count in targets.items():
            indexes = [i for i, row in enumerate(rows) if row.get("target_node") == target and not row.get("is_tool_call")]
            if count > 1 and any(_always(rows[i]) for i in indexes):
                between = any(rows[i].get("target_node") != target and not rows[i].get("is_tool_call")
                              for i in range(indexes[0], indexes[-1]))
                add("same_target_with_always", name, "high" if between else "low",
                    f"rows {indexes} all go to {target} and one is unconditional; "
                    + ("the merged row can shadow the rows between them" if between else "adjacent, so the merge changes nothing"))

    # Code steps.
    enum_schemas = {var: [entry for _, entry in found if "enum_values" in entry]
                    for var, found in entries.items() if any("enum_values" in entry for _, entry in found)}
    for name, node_codes in codes.items():
        for code in node_codes:
            for error in code_write_schema_errors(code, enum_schemas, name) if enum_schemas else []:
                if "refuses" in error:
                    add("enum_write_refused", name, "high", error)
            size = len(code.encode("utf-8"))
            headroom = PAYLOAD_LIMIT - size - RUNTIME_DATA
            loops = headroom // GROWTH_PER_LOOP
            if loops < MIN_LOOPS:
                add("code_payload_limit", name, "blocker" if headroom < 0 else "high" if loops < 10 else "medium",
                    f"code {size} bytes + {RUNTIME_DATA} bytes of observed runtime data leaves {headroom} bytes "
                    f"of the {PAYLOAD_LIMIT}-byte limit, about {max(loops, 0)} more loops at {GROWTH_PER_LOOP} bytes each")

    # Extraction feeding checked inputs.
    clock = ("now" in _list(policy_rules.get("call_variables"))
             or any("now" in _list((spec.get("profile") or {}).get("call_variables")) for spec in specs.values())
             or "now" in (workflow.get("test_variables") or {})
             or "{{now}}" in json.dumps(workflow) or any(re.search(r"vars(?:\.get\(|\[)'now'", code)
                                                         for node_codes in codes.values() for code in node_codes))
    for variable, uses in bound.items():
        for node_name, action, field, _, _ in uses:
            if field in {"field", "product_id"} or action == "close_call" and field in {"outcome", "reason"}:
                add("controller_known_input", node_name, "high" if field == "field" else "medium",
                    f"{action}.{field} reads the extracted variable {variable}, but the controller holds the "
                    + {"field": "next unverified factor", "product_id": "product order"}.get(field, "eligible close rows"))
        for node_name, entry in entries[variable]:
            confidence = entry.get("min_confidence", 0.95)
            if type(confidence) in (int, float) and confidence > MAX_CHECKED_CONFIDENCE:
                # Drops were observed on free values (dates, amounts in words); none on enum proposals.
                add("checked_value_confidence", node_name, "medium" if entry.get("enum_values") else "high",
                    f"{variable} min_confidence {confidence} feeds {', '.join(sorted({u[1] + '.' + u[2] for u in uses}))}")
    for variable, found in entries.items():
        uses = bound.get(variable, [])
        identity = any(use[1] == "verify_identity" and use[2] == "answer" for use in uses)
        api_text = " ".join(use[4] for use in uses).lower()
        for node_name, entry in found:
            kind, pattern = entry.get("type"), str(entry.get("validation_pattern") or "")
            text = str(entry.get("description") or "") + " " + str(entry.get("extraction_prompt") or "")
            dated = kind in {"date", "datetime"} or bool(_DATE_PATTERN.search(pattern)) or identity and "yyyy-mm-dd" in api_text
            if dated and "year" not in text.lower():
                add("date_year_not_required", node_name, "high" if identity else "low",
                    f"{variable} can hold a date whose year the caller never said; nothing asks for the year")
            if dated and not identity and clock and "{{now}}" not in text:
                add("relative_date_platform_clock", node_name, "high",
                    f"{variable} resolves relative days without the call clock {{{{now}}}}")
            digits = bool(_DIGITS_ONLY.fullmatch(pattern))
            numeric = kind in {"integer", "float"} and bool(uses)
            if (digits or identity or numeric) and not _JOIN.search(text):
                add("split_value_not_joined", node_name, "high" if digits or identity else "medium",
                    f"{variable} is taken from one caller turn; a value said in two parts is never joined")
            if uses and kind == "string" and not pattern:
                missing = [marker for marker in _FORMATS if marker in api_text and marker not in text.lower()]
                if missing:
                    add("answer_format_unstated", node_name, "high",
                        f"{variable} feeds an API field that requires {', '.join(missing)}, which its extraction text never states")

    # Speaking nodes.
    system = str(workflow.get("system_prompt") or "")
    for name, node in nodes.items():
        if node.get("node_type") != "conversation" or node.get("passthrough"):
            continue
        rows = [row for row in _list(node.get("transitions")) if isinstance(row, dict)]
        tool_exits = [row for row in rows if row.get("is_tool_call")]
        task, loop = str(node.get("task_message") or ""), node.get("loop_prompt")
        if not _list(node.get("tools")) and not tool_exits:
            if not _NO_TOOL.search(task) or isinstance(loop, str) and loop and not _NO_TOOL.search(loop):
                add("no_tool_line_missing", name, "medium", "a speaking node without tools never says that no tool call is allowed")
        elif not _SPEAK_AFTER.search(system + "\n" + str(node.get("role_message") or "") + "\n" + task):
            only = all(row.get("is_tool_call") or nodes.get(row.get("target_node"), {}).get("passthrough") for row in rows)
            add("speak_after_tool_result_missing", name, "high" if only else "medium",
                "a node with tool calls never says to speak after every tool result")

    # Graph: turn caps, ends without close_call, re-dispatch after a failed code step.
    close_name = str((policy_rules.get("closing_sequence") or {}).get("operation") or "close_call") \
        if isinstance(policy_rules.get("closing_sequence"), dict) else "close_call"
    closers = {name for name, node in nodes.items()
               if any(tool["function"].get("name") == close_name and tool["function"].get("handler") == "__function__:api_call"
                      for _, tool in _functions(node))}
    lifecycle_close = any(isinstance(tool, dict) and (tool.get("function") or {}).get("name") == close_name
                          and "end_of_call" in _list((tool.get("config") or {}).get("alwaysRunsAt"))
                          for tool in _list(workflow.get("tools")))
    ends = {name for name, node in nodes.items() if node.get("node_type") == "end_call"}

    def edges(name: str):
        node = nodes.get(name, {})
        for row in _list(node.get("transitions")):
            if isinstance(row, dict):
                yield row.get("target_node"), row
        for field in ("passthrough_default_transition", "max_turn_transition"):
            if node.get(field):
                yield node[field], None

    def silent_end(start: str) -> bool:
        """Whether an automatic chain from start ends the call with no row into a close_call step."""
        cursor, seen = start, set()
        while cursor in nodes and cursor not in seen and cursor not in closers:
            seen.add(cursor)
            node = nodes[cursor]
            if cursor in ends:
                return not lifecycle_close
            rows = [row for row in _list(node.get("transitions")) if isinstance(row, dict)]
            if (not (node.get("passthrough") or node.get("node_type") in {"code", "api_request"})
                    or any(row.get("target_node") in closers for row in rows)):
                return False
            cursor = rows[-1].get("target_node") if rows and _always(rows[-1]) else node.get("passthrough_default_transition")
        return False

    for name, node in nodes.items():
        cap = node.get("max_turn_transition")
        if node.get("node_type") != "conversation" or node.get("passthrough") or not cap:
            continue
        turns = node.get("max_node_turns") if type(node.get("max_node_turns")) is int else 10
        if turns < MIN_TURNS and cap not in {row.get("target_node") for row in _list(node.get("transitions")) if isinstance(row, dict)}:
            silent = silent_end(cap)
            add("turn_cap_low", name, "high" if silent else "medium",
                f"max_node_turns {turns} per entry, then {cap}" + (", which ends the call with no close_call" if silent else ""))
    if not lifecycle_close:
        roots = [workflow.get("initial_node"), *globals_]
        reachable, pending = set(), [root for root in roots if root in nodes]
        while pending:
            name = pending.pop()
            if name in reachable:
                continue
            reachable.add(name)
            if name not in closers:
                pending.extend(target for target, _ in edges(name) if target in nodes and target not in reachable)
        sources = {}
        for name in reachable - closers:
            for target, row in edges(name):
                if target in ends and not (row and (_tests(row, {PREFIX + "route"}, "closed")
                                                     or any(str(leaf.get("variable", "")).startswith(close_name + "_")
                                                            for leaf in _leaves(row.get("condition"))))):
                    flagged = name == workflow.get("initial_node") or bool(row and _tests(row, {PREFIX + "quarantined"}, "yes"))
                    sources.setdefault(target, []).append((name, flagged))
        for target, found in sorted(sources.items()):
            names = sorted({source for source, _ in found})
            add("end_without_close", target, "medium" if all(flagged for _, flagged in found) else "high",
                f"reached without close_call from {len(names)} nodes: {', '.join(names[:12])}" + (" ..." if len(names) > 12 else ""))
    for name, node in nodes.items():
        if node.get("node_type") != "code":
            continue
        rows = [row for row in _list(node.get("transitions")) if isinstance(row, dict)]
        own = {name + "_failure_count", name + "_response.status", name + "_status_code"}
        source = "\n".join(codes.get(name, []))
        for index, row in enumerate(rows):
            target = row.get("target_node")
            # Only a route value this step itself writes goes stale when the step fails.
            written = {str(leaf.get("variable")) for leaf in _leaves(row.get("condition"))
                       if f"vars['{leaf.get('variable')}']" in source or f'vars["{leaf.get("variable")}"]' in source}
            if (nodes.get(target, {}).get("node_type") != "api_request" or not written
                    or any(_tests(earlier, own) for earlier in rows[:index])):
                continue
            loop = name in {t for t, _ in edges(target)}
            action = (specs.get(name) or {}).get("action", "")
            add("stale_allow_redispatch", name, ("high" if action in READ_ACTIONS else "blocker") if loop else "medium",
                f"row {index} re-enters {target}; a failed step leaves the earlier route value in place")

    # Platform clock in decisions, and container results mapped to strings.
    def scan(value: Any, where: str) -> None:
        if isinstance(value, str):
            for match in _CLOCK.finditer(value):
                add("platform_clock_reference", where, "high" if clock else "medium", "uses {{" + match.group(1) + "}}")
        elif isinstance(value, dict):
            for item in value.values():
                scan(item, where)
        elif isinstance(value, list):
            for item in value:
                scan(item, where)
    for name, node in nodes.items():
        scan(node, name)
    scan({key: value for key, value in workflow.items() if key not in {"nodes", "global_nodes"}}, "workflow")
    if policy is not None:
        scan(policy, "policy")
    for name, node in nodes.items():
        for placement, tool in _functions(node):
            operation = by_operation.get(tool["function"].get("name"))
            if placement != "pre_actions" or not operation or "dumps(" in str(tool.get("post_process_code") or ""):
                continue
            for mapping in _list(node.get("response_mapping")):
                field = str((mapping or {}).get("path") or "").removeprefix("data.")
                if {"array", "object"} & set(_list((operation.get("result_types") or {}).get(field))):
                    add("array_result_mapping", name, "blocker",
                        f"{mapping.get('path')} is an {'/'.join(operation['result_types'][field])} result mapped to {mapping.get('variable')}")

    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda item: (order[item["severity"]], item["rule"], item["node"]))
    return {"version": 1, "findings": findings,
            "summary": {"findings": len(findings),
                        "by_severity": dict(Counter(item["severity"] for item in findings)),
                        "by_rule": dict(sorted(Counter(item["rule"] for item in findings).items()))},
            "auto_fixes": [], "hosted_behavior_verified": False}
