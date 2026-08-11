"""Generate deterministic TypeScript models and operation metadata from OpenAPI.

This intentionally small generator keeps the dashboard contract local and
reviewable without adding a runtime dependency on a code-generation service.
The generated file is a type-only surface: request execution and authority
remain in the existing dashboard/orchestrator adapters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPENAPI = MAS_ROOT / "schemas" / "http" / "orchestrator.openapi.json"
DEFAULT_OUTPUT = MAS_ROOT / "apps" / "mas-dashboard" / "lib" / "generated" / "orchestrator-api.ts"
HTTP_METHODS = ("delete", "get", "head", "options", "patch", "post", "put", "trace")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _property_key(name: str) -> str:
    return name if IDENTIFIER_RE.match(name) else json.dumps(name, ensure_ascii=True)


def _schema_type(schema: Any, *, inline: bool = False) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return _ref_name(str(schema["$ref"]))
    if "const" in schema:
        return _literal(schema["const"])
    for key, separator in (("allOf", " & "), ("oneOf", " | "), ("anyOf", " | ")):
        if key in schema:
            values = [_schema_type(item) for item in schema.get(key) or []]
            result = separator.join(values) or "unknown"
            return f"({result})" if len(values) > 1 else result
    if "enum" in schema:
        values = [_literal(item) for item in schema.get("enum") or []]
        result = " | ".join(values) or "string"
        return f"({result})" if len(values) > 1 else result

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        values = [_schema_type({**schema, "type": item}) for item in schema_type]
        result = " | ".join(dict.fromkeys(values)) or "unknown"
    elif schema_type == "null":
        result = "null"
    elif schema_type == "array" or "items" in schema:
        item_type = _schema_type(schema.get("items"))
        result = f"Array<{item_type}>"
    elif schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        rows: list[str] = []
        for name in sorted(properties):
            marker = "" if name in required else "?"
            rows.append(f"  {_property_key(str(name))}{marker}: {_schema_type(properties[name])};")
        additional = schema.get("additionalProperties")
        if additional is not None and additional is not False:
            rows.append(f"  [key: string]: {_schema_type(additional)};")
        if rows:
            result = "{\n" + "\n".join(rows) + "\n}"
        else:
            result = "Record<string, unknown>" if additional is not False else "Record<string, never>"
    elif schema_type == "integer" or schema_type == "number":
        result = "number"
    elif schema_type == "boolean":
        result = "boolean"
    elif schema_type == "string" or schema_type is None:
        result = "string" if schema_type == "string" or "format" in schema else "unknown"
    else:
        result = "unknown"

    if schema.get("nullable") is True:
        result = f"{result} | null"
    return result


def _content_type(content: Any) -> str:
    if not isinstance(content, dict) or not content:
        return "unknown"
    schemas = [
        _schema_type(media.get("schema"))
        for media in content.values()
        if isinstance(media, dict) and media.get("schema") is not None
    ]
    return " | ".join(dict.fromkeys(schemas)) or "unknown"


def _operation_type(operation: dict[str, Any], path: str, method: str) -> list[str]:
    operation_id = str(operation.get("operationId") or f"{method}_{path}")
    rows = [
        f"  {json.dumps(operation_id)}: {{",
        f"    method: {json.dumps(method.upper())};",
        f"    path: {json.dumps(path)};",
    ]
    parameters = operation.get("parameters") or []
    parameter_rows: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict) or "name" not in parameter:
            continue
        schema = parameter.get("schema") or {}
        marker = "" if parameter.get("required") else "?"
        key = f"{parameter.get('in', 'query')}:{parameter['name']}"
        parameter_rows.append(f"      {_property_key(key)}{marker}: {_schema_type(schema)};")
    if parameter_rows:
        rows.append("    parameters: {")
        rows.extend(parameter_rows)
        rows.append("    };")
    request_body = operation.get("requestBody") or {}
    if isinstance(request_body, dict) and request_body.get("content"):
        rows.append(f"    requestBody: {_content_type(request_body['content'])};")
    rows.append("    responses: {")
    for status, response in sorted((operation.get("responses") or {}).items(), key=lambda item: str(item[0])):
        content = response.get("content") if isinstance(response, dict) else None
        rows.append(f"      {_property_key(str(status))}: {_content_type(content)};")
    rows.extend(("    };", "  };") )
    return rows


def generate(openapi: dict[str, Any]) -> str:
    components = ((openapi.get("components") or {}).get("schemas") or {})
    lines = [
        "/**",
        " * GENERATED FILE — do not edit by hand.",
        " * Source: schemas/http/orchestrator.openapi.json",
        " * Regenerate: uv run python scripts/generate_typescript_api.py --write",
        " */",
        "",
    ]
    for name in sorted(components):
        lines.append(f"export type {name} = {_schema_type(components[name])};")
        lines.append("")

    lines.extend(["export type OrchestratorApiOperations = {",])
    for path in sorted(openapi.get("paths") or {}):
        path_item = openapi["paths"][path]
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                lines.extend(_operation_type(operation, path, method))
    lines.extend(
        [
            "};",
            "",
            "export type OrchestratorOperationId = keyof OrchestratorApiOperations;",
            "export type OrchestratorApiPath = OrchestratorApiOperations[OrchestratorOperationId][\"path\"];",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="write the generated TypeScript file")
    parser.add_argument("--check", action="store_true", help="fail when output differs from generated content")
    args = parser.parse_args(argv)

    try:
        openapi = json.loads(args.openapi.read_text(encoding="utf-8"))
        generated = generate(openapi)
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"typescript-api: unable to generate models: {exc}", file=sys.stderr)
        return 1

    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generated, encoding="utf-8")
        existing = generated
    if args.check and existing != generated:
        print(f"typescript-api: generated output is stale: {args.output}", file=sys.stderr)
        return 1
    print(
        f"typescript-api: {'pass' if existing == generated else 'generated'}; "
        f"{len((openapi.get('components') or {}).get('schemas') or {})} models, "
        f"{sum(1 for item in (openapi.get('paths') or {}).values() for method in HTTP_METHODS if isinstance(item, dict) and isinstance(item.get(method), dict))} operations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
