"""Conservative local checks against the kit's documented workflow format.

Passing these checks establishes JSON/graph/contract properties only. It does
not prove identity gating, consent, truthful dialogue, runtime sandbox support,
or termination of conversations. See ``assess_workflow`` for that distinction.
"""
from __future__ import annotations

import ast
import math
import re
from collections import deque
from typing import Any, Mapping
from urllib.parse import urlsplit

from .policy import rule_value, validate_policy
from .safety import assert_no_secrets
from .call_variables import CallVariablesError, declared_call_variables

NODE_TYPES = {"conversation", "api_request", "code", "end_call", "transfer_call",
              "integration", "knowledge_base", "reschedule_call", "invoke_agent", "exit"}
HANDLERS = {"__function__:api_call", "__function__:code_exec", "__function__:end_call"}
OPERATORS = {"equals", "not_equals", "greater_than", "less_than", "contains",
             "is_set", "is_not_set", "always", "matches_intent"}
VARIABLE_OPERATORS = OPERATORS - {"always", "matches_intent"}
_NAME = re.compile(r"^[a-zA-Z_]\w*$")
_FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "getattr", "setattr",
                    "delattr", "globals", "locals", "input", "breakpoint", "help", "exit", "quit", "iter"}
_ALLOWED_MODULES = {"json", "re", "datetime", "decimal", "math", "collections"}
_FORBIDDEN_ATTRIBUTES = {"read", "read_text", "read_bytes", "write_text", "write_bytes",
                         "system", "popen", "connect", "request", "urlopen", "socket",
                         "load", "dump", "today", "now", "utcnow", "sys", "os", "modules",
                         "environ", "getenv", "open", "exec", "eval", "compile", "getattr",
                         "setattr", "delattr", "globals", "locals", "builtins"}
_EVENT_NODE_TYPES = {"conversation", "api_request", "code", "end_call"}


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_errors(value: Any, path: str) -> list[str]:
    """Bound recursive validators before parsing untrusted candidate structures."""
    stack = [(value, 0, frozenset())]
    count = 0
    while stack:
        item, depth, ancestors = stack.pop()
        count += 1
        if depth > 80 or count > 200000:
            return [path + ": JSON exceeds local depth/complexity limits"]
        if isinstance(item, (dict, list)):
            if id(item) in ancestors:
                return [path + ": cyclic input is not JSON"]
            lineage = ancestors | {id(item)}
            if isinstance(item, dict) and any(not isinstance(k, str) for k in item):
                return [path + ": JSON object keys must be strings"]
            stack.extend((child, depth + 1, lineage) for child in
                         (item.values() if isinstance(item, dict) else item))
        elif type(item) not in {str, int, float, bool, type(None)}:
            return [path + ": input must contain only JSON values"]
        elif type(item) is float and not math.isfinite(item):
            return [path + ": nonfinite numbers are not JSON"]
    return []


def _nulls(value: Any, path: str, errors: list[str]) -> None:
    if value is None:
        errors.append(path + ": null values are forbidden")
    elif isinstance(value, dict):
        for key, child in value.items():
            _nulls(child, path + "." + str(key), errors)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _nulls(child, f"{path}[{i}]", errors)


def _condition(value: Any, path: str, errors: list[str], *, outer: bool = True) -> None:
    if not isinstance(value, dict):
        errors.append(path + ": condition must be an object")
        return
    if "logic" in value or "conditions" in value:
        if not isinstance(value.get("logic"), str) or value["logic"] not in ({"or"} if outer else {"or", "and"}):
            errors.append(path + ": outer condition groups require logic 'or'")
        children = value.get("conditions")
        if not isinstance(children, list) or not children:
            errors.append(path + ": conditions must be a nonempty list")
        else:
            for i, child in enumerate(children):
                _condition(child, f"{path}.conditions[{i}]", errors, outer=False)
        return
    op = value.get("operator")
    if not isinstance(op, str) or op not in OPERATORS:
        errors.append(path + ": unknown condition operator")
        op = None
    if op in VARIABLE_OPERATORS:
        if outer:
            errors.append(path + ": variable conditions require an outer OR group")
        if not isinstance(value.get("variable"), str) or not value["variable"]:
            errors.append(path + ": variable condition requires variable")
        if op not in {"is_set", "is_not_set"} and "operand" not in value:
            errors.append(path + ": comparison requires operand")
        if type(value.get("operand")) is bool:
            errors.append(path + ": route with string flags, not boolean operands")
    if op == "matches_intent" and not isinstance(value.get("description"), str):
        errors.append(path + ": matches_intent requires description")


def _assigned_vars(nodes: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        for item in ast.walk(node):
            if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                for target in targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "vars":
                        if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                            names.add(target.slice.value)
    return names


def _definite_vars(nodes: list[ast.stmt]) -> set[str]:
    assigned = set()
    for node in nodes:
        if isinstance(node, (ast.Raise, ast.Return, ast.Break, ast.Continue)):
            break
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            assigned.update(_assigned_vars([node]))
        elif isinstance(node, ast.If):
            assigned.update(_definite_vars(node.body) & _definite_vars(node.orelse))
        elif isinstance(node, ast.Try):
            branches = [_definite_vars(node.body + node.orelse)] + [_definite_vars(h.body) for h in node.handlers]
            if node.handlers:
                assigned.update(set.intersection(*branches))
            assigned.update(_definite_vars(node.finalbody))
    return assigned


def validate_code(code: Any, path: str = "code", *, require_fallback: bool = True,
                  guard_request_config: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(code, str) or not code.strip():
        return [path + ": code must be a nonempty string"]
    if len(code) > 100000:
        return [path + ": code exceeds local source-size limit"]
    if "{{" in code or "}}" in code:
        errors.append(path + ": code must read vars, not contain template braces")
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError):
        return errors + [path + ": Python syntax is invalid"]
    if len(list(ast.walk(tree))) > 12000:
        errors.append(path + ": code exceeds local validation complexity limit")
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    functions.update({node.targets[0].id: node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                      and any(isinstance(child, ast.Lambda) for child in ast.walk(node.value))})
    aliases = {name: {name} for name in functions}
    assignments = [(target.id, {v.id for v in ast.walk(n.value) if isinstance(v, ast.Name)})
                   for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and isinstance(n.value, (ast.Name, ast.List, ast.Tuple, ast.Dict, ast.Lambda))
                   for assignment in n.targets for target in ast.walk(assignment)
                   if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)]
    for _ in range(len(assignments) + 1):
        changed = False
        for target, sources in assignments:
            before = set(aliases.get(target, set()))
            aliases.setdefault(target, set()).update(set().union(*(aliases.get(source, set()) for source in sources)))
            changed |= before != aliases[target]
        if not changed:
            break
    references = {name: set().union(*(aliases.get(n.id, set()) for n in ast.walk(node) if isinstance(n, ast.Name)
                         and isinstance(n.ctx, ast.Load)))
                  for name, node in functions.items()}
    for name in references:
        pending, seen = list(references[name]), set()
        while pending:
            target = pending.pop()
            if target == name:
                errors.append(path + ": recursive callable references are forbidden")
                break
            if target not in seen:
                seen.add(target)
                pending.extend(references[target] - seen)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                errors.append(path + ": relative imports are forbidden")
            if any(name not in _ALLOWED_MODULES for name in names):
                errors.append(path + ": file/network or unapproved imports are forbidden")
            if any(alias.name.startswith("_") for alias in node.names):
                errors.append(path + ": private module imports are forbidden")
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    errors.append(path + ": wildcard imports hide the callable surface and are forbidden")
                if any(alias.name in _FORBIDDEN_CALLS or alias.name in _FORBIDDEN_ATTRIBUTES for alias in node.names):
                    errors.append(path + ": unsafe imported functions are forbidden, including aliases")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(path + ": dunder names are forbidden")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in {"Exception", "BaseException"}:
            errors.append(path + ": exception fallback classes must not be rebound")
        if ((isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in {"Exception", "BaseException"})
                or isinstance(node, ast.alias) and (node.asname or node.name) in {"Exception", "BaseException"}
                or isinstance(node, ast.ExceptHandler) and node.name in {"Exception", "BaseException"}):
            errors.append(path + ": exception fallback classes must not be rebound")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _FORBIDDEN_CALLS:
            errors.append(path + ": unsafe builtin references are forbidden, including aliases")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRIBUTES):
            errors.append(path + ": unsafe/file/network/real-clock attribute is forbidden")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            errors.append(path + ": unsafe execution or file access is forbidden")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars":
            errors.append(path + ": vars is a workflow mapping, not a callable")
        if isinstance(node, ast.While):
            try:
                unreachable = not bool(ast.literal_eval(node.test))
            except (ValueError, TypeError, SyntaxError, RecursionError):
                unreachable = False
            if not unreachable:
                errors.append(path + ": unbounded while loop is forbidden; use bounded iteration")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
            try:
                args = [ast.literal_eval(arg) for arg in node.args]
                if all(type(arg) is int for arg in args) and len(range(*args)) > 10000:
                    errors.append(path + ": literal range exceeds local iteration limit")
            except OverflowError:
                errors.append(path + ": literal range exceeds local iteration limit")
            except (ValueError, TypeError, RecursionError):
                pass
        if guard_request_config and isinstance(node, ast.Name) and node.id == "config":
            parent = parents.get(node)
            if not (isinstance(parent, ast.Subscript) and parent.value is node
                    and isinstance(parent.slice, ast.Constant) and parent.slice.value == "body"):
                errors.append(path + ": request config may only access literal body; URL/method/headers are protected")
    if require_fallback:
        tries = [node for node in tree.body if isinstance(node, ast.Try)]
        if not tries or any(not node.handlers for node in tries):
            errors.append(path + ": code node requires try/except and fallback values")
        else:
            for attempt in tries:
                defaults = _definite_vars(tree.body[:tree.body.index(attempt)])
                if not any(handler.type is None or isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}
                           for handler in attempt.handlers):
                    errors.append(path + ": code node requires a broad exception fallback")
                written = _assigned_vars(attempt.body)
                for handler in attempt.handlers:
                    if any(isinstance(n, ast.Raise) for statement in handler.body for n in ast.walk(statement)):
                        errors.append(path + ": exception fallback must not re-raise")
                    if written - defaults - _definite_vars(handler.body):
                        errors.append(path + ": exception path omits assigned-variable fallbacks")
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and type(node.value.value) is bool:
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "vars":
                        errors.append(path + ": write string routing flags instead of booleans")
    return sorted(set(errors))


def _mapping(mapping: Any, path: str, errors: list[str]) -> None:
    if not isinstance(mapping, list):
        errors.append(path + ": response_mapping must be an array")
        return
    variables = set()
    for i, entry in enumerate(mapping):
        if not isinstance(entry, dict) or not isinstance(entry.get("variable"), str) or not entry.get("variable"):
            errors.append(f"{path}[{i}]: mapping requires a variable")
            continue
        variable = entry["variable"]
        if not _NAME.fullmatch(variable) or variable in variables:
            errors.append(f"{path}[{i}]: mapping variable must be a unique identifier")
        variables.add(variable)
        if not isinstance(entry.get("path"), str) or not entry["path"].startswith("data."):
            errors.append(f"{path}[{i}]: API result mappings must begin with data.")
        elif not re.fullmatch(r"data\.[A-Za-z_]\w*(?:\.(?:[A-Za-z_]\w*|[0-9]+))*", entry["path"]):
            errors.append(f"{path}[{i}]: invalid API result mapping path")


def _response_mapping(mapping: Any, tool: dict, operations: dict, path: str,
                      errors: list[str]) -> None:
    """Check result fields against the supplied API and explicit hook additions."""
    props = _obj(_obj(_obj(tool.get("function")).get("parameters")).get("properties"))
    url, method = _obj(props.get("url")).get("default"), _obj(props.get("method")).get("default")
    if not isinstance(url, str) or not isinstance(method, str) or not isinstance(mapping, list):
        return
    operation = operations.get((url, method.upper()), (None, {}))[1]
    schemas = [_obj(_obj(media).get("schema")) for response in _obj(operation.get("responses")).values()
               for media in _obj(_obj(response).get("content")).values()]
    additions = set()
    hook = tool.get("post_process_code", "")
    if isinstance(hook, str):
        try:
            for node in ast.walk(ast.parse(hook)):
                if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                        and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                        and isinstance(node.value, ast.Subscript) and isinstance(node.value.value, ast.Name)
                        and node.value.value.id == "result" and isinstance(node.value.slice, ast.Constant)
                        and node.value.slice.value == "data"):
                    additions.add(node.slice.value)
        except (SyntaxError, RecursionError):
            pass
    for entry in mapping:
        result_path = _obj(entry).get("path")
        if not isinstance(result_path, str) or not result_path.startswith("data."):
            continue
        parts = result_path.split(".")[1:]
        if len(parts) == 1 and parts[0] in additions:
            continue
        def exists(schema: dict) -> bool:
            for part in parts:
                schema = (_obj(schema.get("items")) if schema.get("type") == "array" and part.isdigit()
                          else _obj(_obj(schema.get("properties")).get(part)))
                if not schema:
                    return False
            return True
        if not any(exists(schema) for schema in schemas):
            errors.append(path + ": result mapping path is absent from OpenAPI response schemas/hooks")


def _operations(openapi: Mapping[str, Any]) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    operations = {}
    servers = [s["url"].rstrip("/") for s in openapi.get("servers", [])
               if isinstance(s, dict) and isinstance(s.get("url"), str)] if isinstance(openapi.get("servers", []), list) else []
    for endpoint, methods in _obj(openapi.get("paths")).items():
        if not isinstance(endpoint, str):
            continue
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"} or not isinstance(operation, dict):
                continue
            name = operation.get("operationId", endpoint.strip("/").replace("/", "_"))
            for server in servers:
                operations[(server + endpoint, method.upper())] = (name, operation)
    return operations


def _tool(tool: Any, path: str, errors: list[str], operations: dict, placement: str,
          policy: Mapping[str, Any] | None = None) -> tuple[str | None, str | None]:
    if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
        errors.append(path + ": tool must contain type function and a function object")
        return None, None
    function = tool["function"]
    name, handler = function.get("name"), function.get("handler")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        errors.append(path + ": invalid function name")
        name = None
    if not isinstance(handler, str) or handler not in HANDLERS:
        errors.append(path + ": handler is not in the documented allowlist")
        handler = None
    params = function.get("parameters")
    if not isinstance(params, dict) or params.get("type") != "object" or not isinstance(params.get("properties"), dict):
        errors.append(path + ": parameters require an object schema with properties")
        return name, handler
    props = params["properties"]
    required_params = params.get("required", [])
    if (not isinstance(required_params, list) or any(not isinstance(x, str) or x not in props for x in required_params)):
        errors.append(path + ": parameters.required must reference declared properties")
    if placement == "pre_actions" and tool.get("response_mapping"):
        errors.append(path + ": pre_action response_mapping belongs on the node")
    elif "response_mapping" in tool:
        _mapping(tool["response_mapping"], path + ".response_mapping", errors)
        if handler == "__function__:api_call":
            _response_mapping(tool["response_mapping"], tool, operations, path, errors)
    for hook in ("pre_process_code", "post_process_code"):
        if tool.get(hook):
            if handler != "__function__:api_call":
                errors.append(path + ": Python hooks only belong on API calls")
            errors.extend(validate_code(tool[hook], path + "." + hook, require_fallback=False,
                                        guard_request_config=True))
    if handler == "__function__:code_exec":
        code = _obj(props.get("code")).get("default")
        if placement == "tools" and code is None:
            errors.append(path + ": model-written code is forbidden; use reviewed baked code")
        errors.extend(validate_code(code, path + ".code"))
        timeout = _obj(props.get("timeout_ms")).get("default", 30000)
        if type(timeout) not in {int, float} or not 0 < timeout <= 30000:
            errors.append(path + ": code timeout must be positive and at most 30000ms")
    if handler == "__function__:end_call":
        mode = _obj(props.get("message_mode")).get("default")
        if not isinstance(mode, str) or mode not in {"none", "custom", "llm"}:
            errors.append(path + ": end_call requires a baked valid message_mode")
        if mode == "custom" and not _obj(props.get("spoken_message")).get("default"):
            errors.append(path + ": custom farewell requires spoken_message")
        if mode == "llm" and not _obj(props.get("llm_prompt")).get("default"):
            errors.append(path + ": llm farewell requires llm_prompt")
        if _obj(props.get("interruptible")).get("default") is True and not _obj(props.get("trigger_pattern_both")).get("default"):
            errors.append(path + ": interruptible close requires trigger_pattern_both")
    if handler == "__function__:api_call":
        config = tool.get("config")
        if not isinstance(config, dict) or type(config.get("timeoutMs")) not in {int, float} or config["timeoutMs"] <= 0:
            errors.append(path + ": API call requires a positive config.timeoutMs")
        if not isinstance(config, dict) or type(config.get("maxRetries")) is not int or config["maxRetries"] < 0:
            errors.append(path + ": API call requires a nonnegative config.maxRetries")
        url = props.get("url", {}).get("default") if isinstance(props.get("url"), dict) else None
        method = props.get("method", {}).get("default") if isinstance(props.get("method"), dict) else None
        if not isinstance(url, str) or not isinstance(method, str) or (url, method.upper()) not in operations:
            errors.append(path + ": URL/method defaults must match an OpenAPI operation exactly")
            return name, handler
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme != "https":
            errors.append(path + ": API URL must be a credential-free HTTPS operation")
        expected_name, operation = operations[(url, method.upper())]
        # Distinct wrappers can use the same endpoint; don't invent unknown bank tools.
        try:
            aliases = rule_value(policy or {}, "tool_aliases", {})
        except (TypeError, ValueError):
            aliases = {}
        aliases = _obj(aliases)
        if name != expected_name and aliases.get(name) != expected_name:
            errors.append(path + ": function name must match OpenAPI or an explicit policy alias")
        allowed = (policy or {}).get("allowed_actions")
        if isinstance(allowed, list) and expected_name not in allowed:
            errors.append(path + ": action is not permitted by the supplied policy")
        schema = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        body = props.get("body")
        if not isinstance(body, dict) or body.get("type") != "object" or not isinstance(body.get("properties"), dict):
            errors.append(path + ": body fields must be leaves under body.properties")
            return name, handler
        fields = body["properties"]
        required = body.get("required", [])
        if not isinstance(required, list):
            errors.append(path + ": body.required must be an array")
            required = []
        if any(not isinstance(x, str) or x not in fields for x in required) or len(set(x for x in required if isinstance(x, str))) != len(required):
            errors.append(path + ": body.required must reference unique declared fields")
        for required_name in schema.get("required", []):
            field = fields.get(required_name)
            if not isinstance(field, dict):
                errors.append(path + ": body is missing an OpenAPI required field")
            elif required_name not in required and ("default" not in field or field["default"] == ""
                    or (isinstance(field["default"], str) and "{{" in field["default"] and required_name != "customer_id")):
                errors.append(path + ": unbaked required fields must appear in body.required")
        for field_name, field in fields.items():
            expected = schema.get("properties", {}).get(field_name)
            if expected is None or not isinstance(field, dict):
                errors.append(path + ": body contains an unknown or malformed field")
                continue
            if field.get("type") != expected.get("type"):
                errors.append(path + ": body field type differs from OpenAPI")
            if "default" not in field:
                errors.append(path + ": each body leaf requires default (literal or variable template)")
            literal = field.get("default")
            templated = isinstance(literal, str) and "{{" in literal
            if templated:
                if not re.fullmatch(r"\{\{[A-Za-z_]\w*\}\}", literal):
                    errors.append(path + ": body binding must be one complete variable template")
            elif "default" in field:
                kind = expected.get("type")
                matches = {"string": isinstance(literal, str), "integer": type(literal) is int,
                           "number": type(literal) in {int, float}, "boolean": type(literal) is bool,
                           "object": isinstance(literal, dict), "array": isinstance(literal, list)}
                if not matches.get(kind, False):
                    errors.append(path + ": fixed body value type differs from OpenAPI")
                elif kind in {"integer", "number"} and ("minimum" in expected and literal < expected["minimum"]
                        or "maximum" in expected and literal > expected["maximum"]):
                    errors.append(path + ": fixed body value violates OpenAPI numeric bounds")
            if expected.get("enum"):
                enum = field.get("enum", [])
                literal = field.get("default")
                if literal is not None and not (isinstance(literal, str) and "{{" in literal):
                    if literal not in expected["enum"]:
                        errors.append(path + ": fixed body value is outside OpenAPI enum")
                elif not isinstance(enum, list) or not enum or any(x not in expected["enum"] for x in enum):
                    errors.append(path + ": model-filled enum fields must use OpenAPI values")
            if "format" in field and field["format"] != expected.get("format"):
                errors.append(path + ": body field format differs from OpenAPI")
        retries = config.get("maxRetries") if isinstance(config, dict) else None
        if expected_name not in {"get_account"} and isinstance(retries, int) and retries > 0:
            errors.append(path + ": automatic retries on ledger writes/identity checks are unsafe")
    return name, handler


def validate_workflow(workflow: Any, openapi: Mapping[str, Any], policy: Mapping[str, Any] | None = None,
                      *, guard_context: Mapping[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    readonly_call_inputs: set[str] = set()
    for value, label in ((workflow, "workflow"), (openapi, "openapi"), (policy, "policy"),
                         (guard_context, "guard_context")):
        errors.extend(_json_errors(value, label))
    if errors:
        return errors
    if guard_context is not None:
        from .guards import validate_guarded_workflow
        if not isinstance(guard_context, Mapping):
            errors.append("guard_context must be controller-owned compilation inputs")
        else:
            try:
                errors.extend(validate_guarded_workflow(workflow, openapi=dict(openapi), **dict(guard_context)))
            except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
                errors.append("guard_context has unsupported or missing compilation inputs")
        if errors:
            # No candidate execution or expensive source inspection is needed
            # once correspondence to the frozen controller has failed.
            return sorted(set(errors))
        try:
            readonly_call_inputs.update(declared_call_variables(guard_context["contract"]))
        except (CallVariablesError, KeyError) as exc:
            return [str(exc)]
    if not isinstance(workflow, dict):
        return ["workflow must be an object"]
    _nulls(workflow, "workflow", errors)
    try:
        assert_no_secrets(workflow)
    except ValueError:
        errors.append("workflow contains credential-like material")
    if "agent_config" in workflow:
        errors.append("workflow.agent_config must be omitted for this event")
    if not isinstance(openapi, Mapping):
        return errors + ["openapi must be an object"]
    operations = _operations(openapi)
    nodes = workflow.get("nodes")
    globals_ = workflow.get("global_nodes", {})
    if not isinstance(nodes, dict) or not nodes:
        return errors + ["workflow.nodes must be a nonempty object"]
    if not isinstance(globals_, dict):
        errors.append("workflow.global_nodes must be an object")
        globals_ = {}
    overlap = set(nodes) & set(globals_)
    if overlap:
        errors.append("normal/global node names must not overlap")
    all_nodes = {**nodes, **globals_}
    # Public data bindings are declarations, not evidence of runtime persistence.
    # Exact trusted recompilation separately seals all protected code and state.
    operation_names = {value[0] for value in operations.values() if isinstance(value[0], str)}
    declared = {"customer_id", "now", "BANK_TOKEN"}
    declared.update(readonly_call_inputs)
    declared.update(_obj(workflow.get("test_variables")))
    if any(name.startswith("bg_") for name in _obj(workflow.get("test_variables"))):
        errors.append("workflow.test_variables cannot override protected guard values")
    def declarations(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "extraction_schema" and isinstance(child, list):
                    declared.update(x["name"] for x in child if isinstance(x, dict) and isinstance(x.get("name"), str))
                elif key == "response_mapping" and isinstance(child, list):
                    declared.update(x["variable"] for x in child if isinstance(x, dict) and isinstance(x.get("variable"), str))
                elif key == "code" and isinstance(child, dict) and isinstance(child.get("default"), str):
                    try:
                        declared.update(_assigned_vars(ast.parse(child["default"]).body))
                    except (SyntaxError, RecursionError):
                        pass
                declarations(child)
        elif isinstance(value, list):
            for child in value:
                declarations(child)
    declarations(workflow)
    for tool in (x for n in all_nodes.values() if isinstance(n, dict) for p in ("tools", "pre_actions")
                 for x in (n[p] if isinstance(n.get(p), list) else []) if isinstance(x, dict)):
        fn = _obj(tool.get("function"))
        if isinstance(fn.get("name"), str):
            # Documented engine result bindings, not invented static defaults.
            declared.update(fn["name"] + suffix for suffix in ("_status_code", "_result", "_args"))
    def text_bindings(value: Any, path: str, *, spoken: bool = False, readback: bool = False) -> None:
        if isinstance(value, str):
            stripped = re.sub(r"\{\{[^{}]+\}\}", "", value)
            if "{{" in stripped or "}}" in stripped:
                errors.append(path + ": malformed unresolved template")
            for match in re.finditer(r"\{\{([^{}]+)\}\}", value):
                binding = match[1]
                root = binding.split(".")[0]
                if root in readonly_call_inputs and binding not in readonly_call_inputs:
                    errors.append(path + ": call-input template must reference one declared variable exactly")
                if root not in declared and not binding.startswith("extractor."):
                    errors.append(path + ": unresolved required binding " + binding)
                if (root == "customer_id" or root.startswith("bg_") and root != "bg_facts" and not (readback and root == "bg_readback")
                        or any(root.startswith(op + "_") for op in operation_names)
                        or spoken and root == "bg_facts"):
                    errors.append(path + ": authored text cannot read raw protected engine values")
        elif isinstance(value, dict):
            for key, child in value.items():
                text_bindings(child, path + "." + str(key), spoken=spoken or key == "on_enter", readback=readback)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                text_bindings(child, f"{path}[{i}]", spoken=spoken, readback=readback)
    if "system_prompt" in workflow:
        text_bindings(workflow["system_prompt"], "workflow.system_prompt")
    for name, node in all_nodes.items():
        if isinstance(node, dict):
            for field in ("task_message", "routing_prompt", "on_enter", "extraction_schema"):
                if field in node:
                    # Custom wording gains the compiler's protected readback
                    # only after byte-exact controller recompilation above.
                    # An authored copy or a node prefix alone proves nothing.
                    question = (guard_context.get("execution_policy", {}).get("profile", {}).get("confirmation_question")
                                if guard_context is not None else None)
                    compiled_question = (isinstance(question, str) and isinstance(name, str)
                                         and name.startswith("bg_") and name.endswith("_confirm")
                                         and node[field] == {"type": "tts", "text": "{{bg_readback}}. " + question})
                    readback = (field == "on_enter" and (compiled_question or node[field] == {
                        "type": "tts", "text": "Please confirm these exact terms: {{bg_readback}}. Do you agree?"}))
                    text_bindings(node[field], "node." + str(name) + "." + field, readback=readback)
    initial = workflow.get("initial_node")
    if not isinstance(initial, str) or initial not in nodes:
        errors.append("workflow.initial_node must identify a normal node")
    graph: dict[str, set[str]] = {name: set() for name in all_nodes}
    terminals: set[str] = set()
    api_nodes, close_nodes = set(), set()
    automatic: dict[str, str] = {}
    for name, node in all_nodes.items():
        path = "node." + str(name)
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            errors.append(path + ": invalid node key")
        if not isinstance(node, dict):
            errors.append(path + ": node must be an object")
            continue
        if node.get("name") != name:
            errors.append(path + ": name must match its node key")
        node_type = node.get("node_type", "conversation" if name in globals_ else None)
        if not isinstance(node_type, str) or node_type not in NODE_TYPES:
            errors.append(path + ": unknown node_type")
            node_type = None
        elif node_type not in _EVENT_NODE_TYPES:
            errors.append(path + ": node type invokes tools outside the bank-only event allowlist")
        if node.get("tools") and node_type != "conversation":
            errors.append(path + ": tools belong only on conversation nodes")
        if node.get("pre_actions") and node_type not in {"api_request", "code"}:
            errors.append(path + ": pre_actions belong on API/code nodes")
        if "response_mapping" in node:
            if node_type not in {"api_request", "code"}:
                errors.append(path + ": nonempty node response_mapping belongs on API/code nodes")
            _mapping(node["response_mapping"], path + ".response_mapping", errors)
            if node_type == "api_request" and isinstance(node.get("pre_actions"), list):
                api_tools = [t for t in node["pre_actions"] if isinstance(t, dict)
                             and _obj(t.get("function")).get("handler") == "__function__:api_call"]
                if len(api_tools) == 1:
                    _response_mapping(node["response_mapping"], api_tools[0], operations, path, errors)
                elif node["response_mapping"]:
                    errors.append(path + ": node result mappings require one unambiguous API pre_action")
        for flag in ("passthrough", "on_enter_first_entry_only", "disable_inactivity_monitor", "suppress_tool_results"):
            if flag in node and type(node[flag]) is not bool:
                errors.append(path + ": node boolean flags must be booleans")
        entry = node.get("on_enter", {"type": "llm"})
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str) or entry["type"] not in {"llm", "tts", "silent", "audio"}:
            errors.append(path + ": invalid on_enter")
        elif entry.get("type") == "tts" and not isinstance(entry.get("text"), str):
            errors.append(path + ": tts entry requires text")
        elif entry.get("type") == "audio":
            errors.append(path + ": external audio URLs are outside the bank-only event allowlist")
        for placement in ("tools", "pre_actions"):
            tools = node.get(placement, [])
            if not isinstance(tools, list):
                errors.append(path + "." + placement + ": must be an array")
                continue
            for i, tool in enumerate(tools):
                function_name, handler = _tool(tool, f"{path}.{placement}[{i}]", errors, operations, placement, policy)
                if handler == "__function__:api_call":
                    api_nodes.add(name)
                    if function_name == "close_call":
                        if name in close_nodes or placement != "pre_actions":
                            errors.append(path + ": close_call must be one engine-run final action")
                        close_nodes.add(name)
                if handler == "__function__:api_call" and placement == "pre_actions" and isinstance(tool, dict):
                    props = _obj(_obj(_obj(tool.get("function")).get("parameters")).get("properties"))
                    for field in _obj(_obj(props.get("body")).get("properties")).values():
                        default = _obj(field).get("default")
                        if isinstance(default, str) and re.fullmatch(r"\{\{[A-Za-z_]\w*\}\}", default) and default[2:-2] not in declared:
                            errors.append(path + ": engine-run API has an unresolved required binding")
                if handler == "__function__:end_call":
                    errors.append(path + ": call-ending handlers belong only on a final end_call terminal")
        if node_type == "end_call":
            terminals.add(name)
            terminal = node.get("terminal")
            _, handler = _tool(terminal, path + ".terminal", errors, operations, "terminal", policy)
            if handler != "__function__:end_call":
                errors.append(path + ": end_call terminal requires end_call handler")
            if node.get("transitions", []):
                errors.append(path + ": end_call must not have outgoing transitions")
            if node.get("disable_inactivity_monitor") is not True:
                errors.append(path + ": end_call must disable inactivity monitor")
        elif "terminal" in node:
            errors.append(path + ": terminal belongs only on end_call nodes")
        transitions = node.get("transitions", [])
        if not isinstance(transitions, list):
            errors.append(path + ": transitions must be an array")
            transitions = []
        for i, transition in enumerate(transitions):
            tpath = f"{path}.transitions[{i}]"
            if not isinstance(transition, dict):
                errors.append(tpath + ": transition must be an object")
                continue
            target = transition.get("target_node")
            if target == "__previous__" and name in globals_:
                graph[name].update(nodes)
            elif not isinstance(target, str) or target not in all_nodes:
                errors.append(tpath + ": target_node is unknown")
            else:
                graph[name].add(target)
            _condition(transition.get("condition"), tpath + ".condition", errors)
            if guard_context is not None and not name.startswith("bg_"):
                def condition_bindings(condition: Any) -> None:
                    if isinstance(condition, dict):
                        if "variable" in condition and (not isinstance(condition["variable"], str) or condition["variable"] not in declared):
                            errors.append(tpath + ": authored route has an unresolved required binding")
                        for child in condition.get("conditions", []) if isinstance(condition.get("conditions", []), list) else []:
                            condition_bindings(child)
                condition_bindings(transition.get("condition"))
            if transition.get("condition") == {"operator": "always"} and i != len(transitions) - 1:
                errors.append(tpath + ": unconditional route shadows later transitions")
            if "is_tool_call" in transition and type(transition["is_tool_call"]) is not bool:
                errors.append(tpath + ": is_tool_call must be boolean")
        for field in ("passthrough_default_transition", "max_turn_transition"):
            target = node.get(field)
            if target:
                if target == "__previous__" and name in globals_:
                    graph[name].update(nodes)
                elif not isinstance(target, str) or target not in all_nodes:
                    errors.append(path + ": " + field + " target is unknown")
                else:
                    graph[name].add(target)
        if node.get("passthrough"):
            if not node.get("passthrough_default_transition"):
                errors.append(path + ": passthrough requires default transition")
            if not transitions or _obj(transitions[-1]).get("condition") != {"operator": "always"}:
                errors.append(path + ": passthrough requires a final unconditional fallback")
            if len(transitions) == 1 and _obj(transitions[0]).get("condition") == {"operator": "always"}:
                target = _obj(transitions[0]).get("target_node")
                if isinstance(target, str) and target in all_nodes:
                    automatic[name] = target
        if "max_node_turns" in node:
            if type(node["max_node_turns"]) is not int or node["max_node_turns"] < 1:
                errors.append(path + ": max_node_turns must be a positive integer")
            if not node.get("max_turn_transition"):
                errors.append(path + ": max_node_turns requires a max_turn_transition")
        extraction = node.get("extraction_schema", [])
        if not isinstance(extraction, list):
            errors.append(path + ": extraction_schema must be an array")
        else:
            for i, variable in enumerate(extraction):
                ep = f"{path}.extraction_schema[{i}]"
                if not isinstance(variable, dict) or not isinstance(variable.get("name"), str) or not _NAME.fullmatch(variable["name"]):
                    errors.append(ep + ": extracted variable requires name")
                    continue
                if not isinstance(variable.get("type"), str) or variable["type"] not in {"string", "integer", "float", "boolean", "date", "datetime", "name", "phone_number"}:
                    errors.append(ep + ": unknown extraction type")
                if type(variable.get("min_confidence")) not in {int, float} or not 0 <= variable["min_confidence"] <= 1:
                    errors.append(ep + ": min_confidence must be explicit and within 0..1")
                if "enum" in variable:
                    errors.append(ep + ": extraction uses documented enum_values, not enum")
                if "enum_values" in variable:
                    values = variable["enum_values"]
                    if not isinstance(values, list) or not values:
                        errors.append(ep + ": enum_values must be a nonempty array")
                    elif variable.get("type") in {"string", "date", "datetime", "name", "phone_number"} and any(not isinstance(v, str) for v in values):
                        errors.append(ep + ": textual extraction enums require string values")
                if "max_retries" in variable and (type(variable["max_retries"]) is not int or variable["max_retries"] < 0):
                    errors.append(ep + ": max_retries must be a nonnegative integer")
                if "validation_pattern" in variable:
                    try:
                        if not isinstance(variable["validation_pattern"], str):
                            raise TypeError
                        re.compile(variable["validation_pattern"])
                    except (TypeError, re.error):
                        errors.append(ep + ": validation_pattern must be a valid regex string")
                custom = variable.get("extraction_prompt")
                if custom and (not isinstance(custom, str) or any("{{" + key + "}}" not in custom for key in ("extractor.speech", "extractor.conversation.last-10", "extractor.type_preamble", "extractor.variables_list"))):
                    errors.append(ep + ": custom extraction prompt omits required input placeholders")
        if name in globals_:
            _condition(node.get("condition"), path + ".condition", errors)
            excluded = node.get("excluded_nodes", [])
            if not isinstance(excluded, list) or any(not isinstance(x, str) or x not in all_nodes for x in excluded):
                errors.append(path + ": excluded_nodes must reference existing nodes")
    for name, node in globals_.items():
        if isinstance(node, dict):
            excluded = node.get("excluded_nodes", [])
            if isinstance(excluded, list) and any(terminal not in excluded for terminal in terminals):
                errors.append("global node." + name + ": terminal nodes must be excluded")
    if not isinstance(workflow.get("tools", []), list):
        errors.append("workflow.tools must be an array")
    for i, tool in enumerate(workflow.get("tools", []) if isinstance(workflow.get("tools", []), list) else []):
        function_name, handler = _tool(tool, f"workflow.tools[{i}]", errors, operations, "lifecycle", policy)
        if handler == "__function__:end_call":
            errors.append(f"workflow.tools[{i}]: lifecycle must not duplicate terminal closing")
        if function_name == "close_call":
            errors.append(f"workflow.tools[{i}]: lifecycle close_call cannot establish final-close discipline")
        when = _obj(tool.get("config")).get("alwaysRunsAt", []) if isinstance(tool, dict) else []
        if not isinstance(when, list) or not when or any(not isinstance(x, str) or x not in {"start_of_call", "end_of_call"} for x in when):
            errors.append(f"workflow.tools[{i}]: lifecycle requires implemented alwaysRunsAt")
    reachable: set[str] = set()
    queue = deque([initial] if isinstance(initial, str) and initial in graph else [])
    while queue:
        name = queue.popleft()
        if name in reachable:
            continue
        reachable.add(name)
        queue.extend(graph[name] - reachable)
    # Global activation is conditional; conservatively include it as reachable.
    reachable.update(globals_)
    for name in nodes:
        if name not in reachable:
            errors.append("node." + name + ": unreachable from initial_node")
    can_end = set(terminals)
    while True:
        added = {name for name, targets in graph.items() if targets & can_end} - can_end
        if not added:
            break
        can_end.update(added)
    for name in reachable - can_end:
        errors.append("node." + name + ": no graph path to a call-ending handler")
    for name in automatic:
        cursor, seen = name, set()
        while cursor in automatic:
            if cursor in seen:
                errors.append("node." + name + ": unconditional passthrough cycle cannot terminate")
                break
            seen.add(cursor)
            cursor = automatic[cursor]
    for name in close_nodes:
        pending, seen = list(graph[name]), set()
        while pending:
            target = pending.pop()
            if target in seen:
                continue
            seen.add(target)
            if target in api_nodes or _obj(all_nodes[target]).get("node_type") == "conversation":
                errors.append("node." + name + ": close_call must be final before the call-ending terminal")
            if target not in terminals:
                pending.extend(graph[target] - seen)
    return sorted(set(errors))


def require_valid_workflow(workflow: Any, openapi: Mapping[str, Any], policy: Mapping[str, Any] | None = None,
                           *, guard_context: Mapping[str, Any] | None = None) -> None:
    errors = validate_workflow(workflow, openapi, policy, guard_context=guard_context)
    if errors:
        raise ValueError("Invalid workflow:\n" + "\n".join(errors))


def assess_workflow(workflow: Any, openapi: Mapping[str, Any], policy: Mapping[str, Any] | None = None,
                    *, guard_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Explicitly separates local facts from unproven conversation semantics."""
    errors = validate_workflow(workflow, openapi, policy, guard_context=guard_context)
    unverified: set[str] = set()
    def inspect(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"pre_process_code", "post_process_code"} and isinstance(value, str):
                    code = value
                elif key == "code" and isinstance(value, dict) and isinstance(value.get("default"), str):
                    code = value["default"]
                else:
                    inspect(value)
                    continue
                if len(code) > 100000:
                    continue
                try:
                    for node in ast.walk(ast.parse(code)):
                        if isinstance(node, ast.Import):
                            unverified.update(a.name.split(".")[0] for a in node.names)
                        elif isinstance(node, ast.ImportFrom):
                            unverified.add((node.module or "").split(".")[0])
                except (SyntaxError, RecursionError):
                    pass
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    if not _json_errors(workflow, "workflow"):
        inspect(workflow)
    unverified -= {"json", "re", "datetime"}
    return {"structural_errors": errors, "structurally_valid": not errors,
            "action_guard_compilation_verified": guard_context is not None and not errors,
            "behavioral_compliance_proven": False,
            "unverified_code_modules": sorted(unverified),
            "requires_host_tests": [
                "identity evidence gates account access and sensitive dialogue on every path",
                "consent gates every write and changed proposals invalidate consent",
                "product ids, integer-cent amounts and dates follow supplied policy and now",
                "notes remain staged until transfers are ruled out; close_call is exactly last",
                "tool results and Python variable writes persist in the deployed platform",
                "conversation loops terminate within caller and platform time limits",
                "authored dialogue makes only source-authorized financial commitments, fee and interest claims",
                "identity factors follow the supplied ordering and challenge policy in actual dialogue",
            ]}
