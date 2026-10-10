"""Preflight approved development API inputs before creating export files."""
import math
import re

from builder.safety import canonical_json
from builder.toolbindings import operations
from .development_protocol import REQUEST_TYPES, RESULT_ROLES


def _matches_type(value, kind):
    return {"string": type(value) is str, "integer": type(value) is int,
            "number": type(value) in (int, float) and math.isfinite(value),
            "boolean": type(value) is bool, "object": isinstance(value, dict),
            "array": isinstance(value, list)}.get(kind, False)


def _schema(spec, path, depth=0):
    if depth > 32 or not isinstance(spec, dict) or spec.get("type") not in {"string", "integer", "number", "boolean", "object", "array"}:
        raise ValueError(path + ": a bounded explicit supported schema type is required")
    if any(key in spec for key in ("oneOf", "anyOf", "allOf", "not", "$ref")):
        raise ValueError(path + ": ambiguous or unresolved schema is unsupported")
    kind = spec["type"]
    if "description" in spec and not isinstance(spec["description"], str):
        raise ValueError(path + ": schema descriptions must be text")
    if "enum" in spec:
        values = spec["enum"]
        if (not isinstance(values, list) or not values or any(not _matches_type(v, kind) for v in values)
                or len({canonical_json(v) for v in values}) != len(values)):
            raise ValueError(path + ": enum must contain distinct values of its declared type")
    if kind == "object":
        fields, required = spec.get("properties"), spec.get("required", [])
        if (not isinstance(fields, dict) or not fields or len(fields) > 512
                or any(not isinstance(k, str) or not k for k in fields)):
            raise ValueError(path + ": object requires nonempty declared fields")
        if (not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                or len(set(required)) != len(required) or set(required) - set(fields)):
            raise ValueError(path + ": required fields must exist without duplicates")
        for name, field in fields.items():
            _schema(field, path + "." + name, depth + 1)
    elif kind == "array":
        _schema(spec.get("items"), path + "[]", depth + 1)
    for key in ("minimum", "maximum"):
        if key in spec and (kind not in {"number", "integer"} or not _matches_type(spec[key], "number")):
            raise ValueError(path + ": numeric bounds must be finite numbers")
    if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
        raise ValueError(path + ": numeric bounds are reversed")


def _semantic_fields(schema, label):
    result = {}
    for wire, field in schema["properties"].items():
        match = re.match(r"Semantic " + label + r" field: ([a-z_]+)(?:\.|$)", field.get("description", ""))
        if match:
            semantic = match[1]
            if semantic in result:
                raise ValueError("ambiguous semantic " + label + " field")
            result[semantic] = wire
        elif label == "request":
            match = re.fullmatch(r"Semantic field: ([a-z_]+)", field.get("description", ""))
            if match:
                semantic = match[1]
                if semantic in result:
                    raise ValueError("ambiguous semantic request field")
                result[semantic] = wire
    return result


def validate_input_api(openapi):
    """Check schema execution shape, not banking policy interpretation or calls."""
    bound = operations(openapi)
    # Use the same strict local-reference/composition boundary as compilation;
    # permissive last-wins allOf resolution is insufficient export evidence.
    from builder.contract import _operation_catalogue
    _operation_catalogue(openapi, [operation["name"] for operation in bound])
    for operation in bound:
        name = operation["name"]
        body = operation["body_schema"]
        _schema(body, name + ".request")
        request = _semantic_fields(body, "request")
        if name in REQUEST_TYPES and set(REQUEST_TYPES[name]) - set(request):
            raise ValueError(name + ": request lacks explicit semantic field bindings")
        for semantic, kind in REQUEST_TYPES.get(name, {}).items():
            allowed = {"number", "integer"} if kind == "number" else {kind}
            if body["properties"][request[semantic]]["type"] not in allowed:
                raise ValueError(name + ": unsupported semantic request type")
        path_fields = set()
        declared = set()
        for parameter in operation["parameters"]:
            if (not isinstance(parameter, dict) or parameter.get("in") not in {"path", "query", "header"}
                    or not isinstance(parameter.get("name"), str) or not parameter["name"]):
                raise ValueError(name + ": unsupported parameter declaration")
            key = (parameter["in"], parameter["name"])
            if key in declared:
                raise ValueError(name + ": duplicate parameter declaration")
            declared.add(key)
            _schema(parameter.get("schema"), name + ".parameter")
            if parameter["in"] == "path":
                if parameter.get("required") is not True:
                    raise ValueError(name + ": path parameters must be required")
                path_fields.add(parameter["name"])
        if path_fields != set(re.findall(r"\{([^{}]+)\}", operation["url"])):
            raise ValueError(name + ": URL path variables must have exact parameter bindings")
        responses = operation["responses"]
        if not isinstance(responses, dict) or not responses:
            raise ValueError(name + ": response schema is missing")
        success = False
        for status, response in responses.items():
            if not isinstance(status, str) or not (status == "default" or re.fullmatch(r"[1-5][0-9]{2}", status)):
                raise ValueError(name + ": invalid declared response status")
            successful = status.startswith("2")
            if (not successful and status != "default" and isinstance(response, dict)
                    and not response.get("content")):
                if not isinstance(response.get("description"), str) or not response["description"].strip():
                    raise ValueError(name + ": bodyless fault response needs an explicit description")
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema") if isinstance(response, dict) else None
            _schema(schema, name + ".response." + status)
            if schema["type"] != "object":
                raise ValueError(name + ": trusted response must be an object")
            fields = _semantic_fields(schema, "result")
            mandatory = {"ok"}
            if name == "verify_identity": mandatory |= {"verified", "wrong_answers_left"}
            elif name == "get_account": mandatory.add("products")
            elif name == "create_promise": mandatory.add("promise_id")
            if (successful or status == "default") and name in RESULT_ROLES and mandatory - set(fields):
                raise ValueError(name + ": response lacks trusted semantic fields")
            for semantic, wire in fields.items():
                if not successful and status != "default":
                    continue
                field = schema["properties"][wire]
                if semantic in {"ok", "verified"}:
                    valid = field["type"] == "boolean" or field["type"] == "string" and bool(field.get("enum")) and set(field["enum"]) <= {"true", "false", "True", "False"}
                elif semantic == "wrong_answers_left": valid = field["type"] == "integer"
                elif semantic == "products": valid = field["type"] == "array" and field["items"]["type"] == "object"
                else: valid = field["type"] == "string"
                if not valid:
                    raise ValueError(name + ": unsupported trusted result type")
            success |= successful
        if not success:
            raise ValueError(name + ": an explicit success response is required")
    return {"operations": len(bound), "request_response_schemas_verified": True}
