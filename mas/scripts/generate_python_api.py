"""Generate the deterministic Python SDK contract from OpenAPI.

The generated module is intentionally type-first: request/response models are
``TypedDict``/type-alias declarations and operation metadata is immutable.  A
small hand-written async client consumes that metadata so Python integrations
use the same paths, parameters, and API operation IDs as the dashboard.
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
DEFAULT_OUTPUT = MAS_ROOT / "packages" / "mas-api-sdk" / "mas_api_sdk" / "generated.py"
HTTP_METHODS = ("delete", "get", "head", "options", "patch", "post", "put", "trace")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _literal(value: Any) -> str:
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    return repr(value)


def _schema_type(schema: Any) -> str:
    """Map the JSON Schema subset emitted by FastAPI to Python typing."""
    if not isinstance(schema, dict):
        return "Any"
    if "$ref" in schema:
        return _ref_name(str(schema["$ref"]))
    if "const" in schema:
        return f"Literal[{_literal(schema['const'])}]"
    for key, separator in (("oneOf", " | "), ("anyOf", " | ")):
        if key in schema:
            values = [_schema_type(item) for item in schema.get(key) or []]
            values = list(dict.fromkeys(values))
            if not values:
                return "Any"
            return separator.join(values)
    if "allOf" in schema:
        values = [_schema_type(item) for item in schema.get("allOf") or []]
        return values[0] if len(values) == 1 else "Any"
    if "enum" in schema:
        values = [_literal(item) for item in schema.get("enum") or []]
        return f"Literal[{', '.join(values)}]" if values else "str"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        values = [_schema_type({**schema, "type": item}) for item in schema_type]
        return " | ".join(dict.fromkeys(values)) or "Any"
    if schema_type == "array" or "items" in schema:
        return f"list[{_schema_type(schema.get('items'))}]"
    if schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"dict[str, {_schema_type(additional)}]"
        return "dict[str, Any]"
    if schema_type == "integer":
        return "int"
    if schema_type == "number":
        return "float"
    if schema_type == "boolean":
        return "bool"
    if schema_type == "string" or "format" in schema:
        return "str"
    return "Any"


def _content_schema(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    for media_type in sorted(content):
        media = content[media_type]
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return None


def _schema_name(schema: Any) -> str | None:
    if isinstance(schema, dict) and "$ref" in schema:
        return _ref_name(str(schema["$ref"]))
    return None


def _operation_rows(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(openapi.get("paths") or {}):
        path_item = openapi["paths"][path]
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters") or []
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            parameters = [*path_parameters, *(operation.get("parameters") or [])]
            path_params = sorted(
                str(item["name"])
                for item in parameters
                if isinstance(item, dict) and item.get("in") == "path" and item.get("name")
            )
            query_params = sorted(
                str(item["name"])
                for item in parameters
                if isinstance(item, dict) and item.get("in") == "query" and item.get("name")
            )
            body = operation.get("requestBody") or {}
            body_schema = _content_schema(body.get("content")) if isinstance(body, dict) else None
            response_types: list[str] = []
            for _status, response in sorted(
                (operation.get("responses") or {}).items(), key=lambda item: str(item[0])
            ):
                if not isinstance(response, dict):
                    continue
                name = _schema_name(_content_schema(response.get("content")))
                if name and name not in response_types:
                    response_types.append(name)
            rows.append(
                {
                    "operation_id": str(operation.get("operationId") or f"{method}_{path}"),
                    "method": method.upper(),
                    "path": path,
                    "path_params": path_params,
                    "query_params": query_params,
                    "request_body_type": _schema_name(body_schema),
                    "response_types": response_types,
                }
            )
    return rows


def _model_lines(name: str, schema: dict[str, Any]) -> list[str]:
    if schema.get("enum") is not None or schema.get("type") not in {"object", None}:
        return [f"{name}: TypeAlias = {_schema_type(schema)}", ""]
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return [f"{name} = dict[str, Any]", ""]
    required = set(schema.get("required") or [])
    lines = [f"class {name}(TypedDict):"]
    if not properties:
        lines.append("    pass")
    else:
        for field in sorted(properties):
            marker = "Required" if field in required else "NotRequired"
            lines.append(f"    {field}: {marker}[{_schema_type(properties[field])}]")
    lines.append("")
    return lines


def generate(openapi: dict[str, Any]) -> str:
    components = ((openapi.get("components") or {}).get("schemas") or {})
    rows = _operation_rows(openapi)
    lines = [
        "# ruff: noqa",
        "\"\"\"GENERATED FILE — do not edit by hand.\"\"\"",
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Any, Literal, NotRequired, Required, TypeAlias, TypedDict",
        "",
    ]
    for name in sorted(components):
        lines.extend(_model_lines(str(name), components[name]))

    lines.extend(
        [
            "@dataclass(frozen=True, slots=True)",
            "class ApiOperation:",
            "    operation_id: str",
            "    method: str",
            "    path: str",
            "    path_params: tuple[str, ...]",
            "    query_params: tuple[str, ...]",
            "    request_body_type: str | None",
            "    response_types: tuple[str, ...]",
            "",
            "",
            "OPERATIONS: dict[str, ApiOperation] = {",
        ]
    )
    for row in rows:
        path_params = tuple(row["path_params"])
        query_params = tuple(row["query_params"])
        response_types = tuple(row["response_types"])
        lines.extend(
            [
                f"    {_literal(row['operation_id'])}: ApiOperation(",
                f"        operation_id={_literal(row['operation_id'])},",
                f"        method={_literal(row['method'])},",
                f"        path={_literal(row['path'])},",
                f"        path_params={_literal(path_params)},",
                f"        query_params={_literal(query_params)},",
                f"        request_body_type={_literal(row['request_body_type'])},",
                f"        response_types={_literal(response_types)},",
                "    ),",
            ]
        )
    lines.extend(
        [
            "}",
            "",
            f"MODEL_COUNT = {len(components)}",
            f"OPERATION_COUNT = {len(rows)}",
            "",
            "__all__ = [",
            "    \"ApiOperation\",",
            "    \"MODEL_COUNT\",",
            "    \"OPERATION_COUNT\",",
            "    \"OPERATIONS\",",
        ]
    )
    lines.extend(f"    {_literal(name)}," for name in sorted(components))
    lines.append("]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write", action="store_true", help="write the generated SDK")
    parser.add_argument("--check", action="store_true", help="fail when the SDK is stale")
    args = parser.parse_args(argv)
    try:
        openapi = json.loads(args.openapi.read_text(encoding="utf-8"))
        generated = generate(openapi)
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"python-api: unable to generate SDK: {exc}", file=sys.stderr)
        return 1
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generated, encoding="utf-8")
        existing = generated
    if (args.check or not args.write) and existing != generated:
        print(f"python-api: generated SDK is stale: {args.output}", file=sys.stderr)
        return 1
    print(f"python-api: pass; {len(openapi.get('components', {}).get('schemas', {}))} models, {len(_operation_rows(openapi))} operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
