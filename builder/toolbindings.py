"""Derive Freya tool declarations from the supplied OpenAPI contract."""
from __future__ import annotations

import copy
import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlparse


class BindingError(ValueError):
    pass


def assert_live_contract(openapi: dict) -> None:
    """Offline fixtures are welcome; reserved/private targets cannot be deployed."""
    for operation in operations(openapi):
        parsed = urlparse(operation["url"])
        host = (parsed.hostname or "").rstrip(".").lower()
        reserved = host in {"localhost", "example.com", "example.org", "example.net"} or host.endswith(
            (".invalid", ".test", ".example", ".localhost", ".local", ".internal", ".example.com", ".example.org", ".example.net"))
        if parsed.scheme != "https" or reserved or "{" in host or not host:
            raise BindingError("Live tool contracts cannot target reserved, unresolved, or private test hosts")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            continue
        if not address.is_global:
            raise BindingError("Live tool contracts cannot target private or local IP addresses")


def resolve_refs(value: Any, document: dict, stack: tuple[str, ...] = ()) -> Any:
    """Resolve local JSON pointers only; never retrieve external documents."""
    if isinstance(value, list):
        return [resolve_refs(v, document, stack) for v in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in stack:
            raise BindingError("Only nonrecursive local OpenAPI references are supported")
        target: Any = document
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise BindingError(f"Unresolved OpenAPI reference: {ref}") from None
        merged = {**resolve_refs(target, document, stack + (ref,)),
                  **{k: resolve_refs(v, document, stack) for k, v in value.items() if k != "$ref"}}
        return merged
    resolved = {k: resolve_refs(v, document, stack) for k, v in value.items()}
    if "allOf" in resolved:
        combined: dict = {k: v for k, v in resolved.items() if k != "allOf"}
        for member in resolved["allOf"]:
            combined["properties"] = {**combined.get("properties", {}), **member.get("properties", {})}
            combined["required"] = sorted(set(combined.get("required", []) + member.get("required", [])))
            for key, item in member.items():
                if key not in ("properties", "required"):
                    combined.setdefault(key, item)
        resolved = combined
    return resolved


def operations(openapi: dict) -> list[dict]:
    """Return a normalized, schema-derived operation catalogue."""
    doc = resolve_refs(openapi, openapi)
    found: list[dict] = []
    names: set[str] = set()
    for path, entry in doc.get("paths", {}).items():
        for method, operation in entry.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            servers = operation.get("servers") or entry.get("servers") or doc.get("servers")
            if not servers or not isinstance(servers[0].get("url"), str):
                raise BindingError("Every tool operation needs an explicit OpenAPI server")
            base = servers[0]["url"].rstrip("/")
            # Resolve declared server variables from contract defaults, not the environment.
            for key, spec in servers[0].get("variables", {}).items():
                if "default" not in spec:
                    raise BindingError("OpenAPI server variables need defaults")
                base = base.replace("{" + key + "}", str(spec["default"]))
            parsed = urlparse(base)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise BindingError("Tool servers must be HTTPS URLs without embedded credentials")
            name = operation.get("operationId") or path.rstrip("/").rsplit("/", 1)[-1]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in names:
                raise BindingError("Tool operation names must be unique identifiers")
            names.add(name)
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {"type": "object", "properties": {}})
            if body.get("type", "object") != "object" or any(k in body for k in ("oneOf", "anyOf")):
                raise BindingError("Tool request bodies must have an unambiguous object schema")
            params = entry.get("parameters", []) + operation.get("parameters", [])
            found.append({"name": name, "method": method.upper(), "url": base + "/" + path.lstrip("/"),
                          "body_schema": body, "parameters": params,
                          "description": operation.get("description") or operation.get("summary") or name,
                          "responses": operation.get("responses", {}),
                          "security": operation.get("security", doc.get("security", [])),
                          "security_schemes": doc.get("components", {}).get("securitySchemes", {})})
    if not found:
        raise BindingError("The supplied OpenAPI has no tool operations")
    return found


def _leaf(spec: dict, name: str, baked_variables: set[str]) -> dict:
    result = {k: copy.deepcopy(v) for k, v in spec.items() if k in {
        "type", "description", "enum", "format", "items", "properties", "required", "minimum", "maximum", "pattern", "minLength", "maxLength", "additionalProperties"}}
    result.setdefault("type", "string")
    if name in baked_variables:
        result["default"] = "{{" + name + "}}"
    elif "default" in spec:
        result["default"] = copy.deepcopy(spec["default"])
    else:
        # Omitting default leaves the exact field schema visible to the dialogue model.
        result.setdefault("description", f"Supply {name} from the verified conversation and permitted contract")
    return result


def derive_tools(openapi: dict, baked_variables: set[str] | None = None) -> list[dict]:
    """Build templates without secrets; only supplied call variables are baked."""
    baked_variables = set(baked_variables or ())
    tools: list[dict] = []
    for op in operations(openapi):
        body = op["body_schema"]
        headers = {"Content-Type": {"type": "string", "default": "application/json"}}
        url = op["url"]
        query: list[str] = []
        for param in op["parameters"]:
            key = param["name"]
            if param["in"] == "path":
                url = url.replace("{" + key + "}", "{{" + key + "}}")
            elif param["in"] == "query":
                query.append(key + "={{" + key + "}}")
            elif param["in"] == "header":
                headers[key] = _leaf(param.get("schema", {}), key, baked_variables)
            else:
                raise BindingError("Cookie tool parameters are unsupported")
        if query:
            url += ("&" if "?" in url else "?") + "&".join(query)
        for requirement in op["security"][:1]:
            for scheme_name in requirement:
                scheme = op["security_schemes"].get(scheme_name, {})
                placeholder = "{{auth_" + scheme_name + "}}"
                if scheme.get("type") == "apiKey" and scheme.get("in") == "header":
                    headers[scheme["name"]] = {"type": "string", "default": placeholder}
                elif scheme.get("type") == "http" and scheme.get("scheme", "").lower() == "bearer":
                    headers["Authorization"] = {"type": "string", "default": "Bearer " + placeholder}
                else:
                    raise BindingError("Only header API-key and bearer tool authentication are supported")
        tools.append({"type": "function", "config": {"timeoutMs": 10000, "maxRetries": 0},
                      "function": {"name": op["name"], "handler": "__function__:api_call",
                                   "description": op["description"], "parameters": {"type": "object", "properties": {
                                       "url": {"type": "string", "default": url},
                                       "method": {"type": "string", "default": op["method"]},
                                       "connect_retries": {"type": "number", "default": 0},
                                       "headers": {"type": "object", "properties": headers},
                                       "body": {"type": "object", "properties": {
                                           k: _leaf(v, k, baked_variables) for k, v in body.get("properties", {}).items()},
                                           "required": body.get("required", [])}}}}})
    return tools


def bind_controller_auth(workflow: dict, openapi: dict, environ: dict[str, str] | None = None) -> dict:
    """Fill only declared auth placeholders after generation, outside model context."""
    environ = os.environ if environ is None else environ
    schemes = resolve_refs(openapi, openapi).get("components", {}).get("securitySchemes", {})
    bindings = {}
    for name in schemes:
        env_name = "FREYA_TOOL_AUTH_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        value = environ.get(env_name)
        if value:
            bindings["{{auth_" + name + "}}"] = value

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            for placeholder, replacement in bindings.items():
                value = value.replace(placeholder, replacement)
            if re.search(r"\{\{auth_[^}]+\}\}", value):
                raise BindingError("A tool authentication placeholder lacks its controller credential")
        return value
    return walk(workflow)
