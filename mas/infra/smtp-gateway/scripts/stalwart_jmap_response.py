#!/usr/bin/env python3
"""Strict, secret-safe validation for JMAP request/response envelopes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


DIAGNOSTIC_FIELDS = (
    "ACTION",
    "JMAP_METHOD",
    "INVOCATION_TAG",
    "ERROR_TYPE",
    "DESCRIPTION",
    "INVALID_PROPERTIES",
    "NOT_CREATED_IDS",
    "NOT_UPDATED_IDS",
    "NOT_DESTROYED_IDS",
    "HTTP_STATUS",
    "ENDPOINT_PATH",
)


class JmapResponseError(ValueError):
    """A JMAP response failed the expected request/response contract."""

    def __init__(self, diagnostics: Mapping[str, str]):
        self.diagnostics = {field: diagnostics.get(field, "") for field in DIAGNOSTIC_FIELDS}
        super().__init__(self.diagnostics["DESCRIPTION"] or "invalid JMAP response")


def _safe_text(value: Any, *, fallback: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)\b(?:basic|bearer|oauth)\s+[^\s,;]+", "<redacted>", text)
    text = re.sub(
        r"(?i)\b(?:api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,;]+",
        "<redacted>",
        text,
    )
    text = "".join(character for character in text if character.isprintable())
    return (text or fallback)[:limit]


def _safe_identifier(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.:@/-]", "_", str(value))[:96] or "<empty>"


def _safe_identifiers(values: Any) -> str:
    if not isinstance(values, list):
        return "<malformed>"
    return ",".join(_safe_identifier(value) for value in values) or "NONE"


def _diagnostics(
    *,
    action: str,
    method: str,
    tag: str,
    error_type: str,
    description: str,
    invalid_properties: str = "NONE",
    not_created_ids: str = "NONE",
    not_updated_ids: str = "NONE",
    not_destroyed_ids: str = "NONE",
    http_status: str = "UNKNOWN",
    endpoint_path: str = "/jmap/",
) -> dict[str, str]:
    return {
        "ACTION": _safe_text(action, fallback="unknown-action", limit=64),
        "JMAP_METHOD": _safe_text(method, fallback="unknown-method", limit=96),
        "INVOCATION_TAG": _safe_identifier(tag),
        "ERROR_TYPE": _safe_text(error_type, fallback="invalidResponse", limit=96),
        "DESCRIPTION": _safe_text(description, fallback="invalid JMAP response"),
        "INVALID_PROPERTIES": invalid_properties,
        "NOT_CREATED_IDS": not_created_ids,
        "NOT_UPDATED_IDS": not_updated_ids,
        "NOT_DESTROYED_IDS": not_destroyed_ids,
        "HTTP_STATUS": _safe_identifier(http_status),
        "ENDPOINT_PATH": "/jmap/" if endpoint_path == "/jmap/" else "<redacted-path>",
    }


def _raise(
    *,
    request_method: str,
    tag: str,
    action: str,
    description: str,
    error_type: str = "invalidResponse",
    invalid_properties: str = "NONE",
    not_created_ids: str = "NONE",
    not_updated_ids: str = "NONE",
    not_destroyed_ids: str = "NONE",
    http_status: str = "UNKNOWN",
    endpoint_path: str = "/jmap/",
) -> None:
    raise JmapResponseError(
        _diagnostics(
            action=action,
            method=request_method,
            tag=tag,
            error_type=error_type,
            description=description,
            invalid_properties=invalid_properties,
            not_created_ids=not_created_ids,
            not_updated_ids=not_updated_ids,
            not_destroyed_ids=not_destroyed_ids,
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    )


def _request_calls(request_body: Any, *, action: str, http_status: str, endpoint_path: str) -> list[dict[str, Any]]:
    if not isinstance(request_body, dict):
        _raise(
            request_method="unknown-method",
            tag="unknown-tag",
            action=action,
            description="JMAP request is not an object",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    calls = request_body.get("methodCalls")
    if not isinstance(calls, list) or not calls:
        _raise(
            request_method="unknown-method",
            tag="unknown-tag",
            action=action,
            description="JMAP request methodCalls is malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    if "createdIds" in request_body and (
        not isinstance(request_body["createdIds"], dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in request_body["createdIds"].items())
    ):
        _raise(
            request_method="unknown-method",
            tag="unknown-tag",
            action=action,
            description="JMAP request createdIds is malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    expected: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, list) or len(call) != 3:
            _raise(
                request_method="unknown-method",
                tag="unknown-tag",
                action=action,
                description="JMAP request method call is malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        method, arguments, tag = call
        if not isinstance(method, str) or not isinstance(arguments, dict) or not isinstance(tag, str):
            _raise(
                request_method=method if isinstance(method, str) else "unknown-method",
                tag=tag if isinstance(tag, str) else "unknown-tag",
                action=action,
                description="JMAP request method call fields are malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if any(item["tag"] == tag for item in expected):
            _raise(
                request_method=method,
                tag=tag,
                action=action,
                description="JMAP request invocation tags are not unique",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        create = arguments.get("create", {})
        update = arguments.get("update", {})
        destroy = arguments.get("destroy", [])
        if create is not None and not isinstance(create, dict):
            _raise(
                request_method=method,
                tag=tag,
                action=action,
                description="JMAP create arguments are malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if update is not None and not isinstance(update, dict):
            _raise(
                request_method=method,
                tag=tag,
                action=action,
                description="JMAP update arguments are malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if destroy is not None and not isinstance(destroy, list):
            _raise(
                request_method=method,
                tag=tag,
                action=action,
                description="JMAP destroy arguments are malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        expected.append(
            {
                "method": method,
                "arguments": arguments,
                "tag": tag,
                "create": create or {},
                "update": update or {},
                "destroy": destroy or [],
            }
        )
    return expected


def _properties(value: Any) -> str:
    if not isinstance(value, dict):
        return "<malformed>"
    names: list[str] = []
    for item in value.values():
        if isinstance(item, dict) and isinstance(item.get("properties"), list):
            names.extend(_safe_identifier(name) for name in item["properties"])
    return ",".join(sorted(set(names))) or "NONE"


def _error_type(value: Any) -> str:
    if not isinstance(value, dict):
        return "invalidResponse"
    return _safe_text(value.get("type"), fallback="invalidResponse", limit=96)


def _nonempty_ids(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, list):
        return value
    return ["<malformed>"]


def _check_set_response(
    *,
    expected: dict[str, Any],
    arguments: dict[str, Any],
    action: str,
    http_status: str,
    endpoint_path: str,
) -> None:
    not_created = _nonempty_ids(arguments.get("notCreated"))
    not_updated = _nonempty_ids(arguments.get("notUpdated"))
    not_destroyed = _nonempty_ids(arguments.get("notDestroyed"))
    if not_created or not_updated or not_destroyed:
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            error_type=_error_type(
                (arguments.get("notCreated") or arguments.get("notUpdated") or arguments.get("notDestroyed") or {}).get(
                    not_created[0] if not_created else not_updated[0] if not_updated else not_destroyed[0],
                    {},
                )
                if isinstance(arguments.get("notCreated") or arguments.get("notUpdated") or arguments.get("notDestroyed"), dict)
                else {},
            ),
            description="JMAP set response contains a method-level failure",
            invalid_properties=_properties(
                arguments.get("notCreated") or arguments.get("notUpdated") or arguments.get("notDestroyed")
            ),
            not_created_ids=_safe_identifiers(not_created),
            not_updated_ids=_safe_identifiers(not_updated),
            not_destroyed_ids=_safe_identifiers(not_destroyed),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )

    allowed = {
        "accountId",
        "oldState",
        "newState",
        "created",
        "updated",
        "destroyed",
        "notCreated",
        "notUpdated",
        "notDestroyed",
        "updatedProperties",
    }
    unknown = set(arguments) - allowed
    if unknown:
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP set response contains unknown fields",
            invalid_properties=",".join(sorted(_safe_identifier(item) for item in unknown)),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )

    created = arguments.get("created", {})
    updated = arguments.get("updated", {})
    destroyed = arguments.get("destroyed", [])
    if not isinstance(created, dict) or not isinstance(updated, dict) or not isinstance(destroyed, list):
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP set response mutation fields are malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    expected_created = set(expected["create"])
    expected_updated = set(expected["update"])
    expected_destroyed = set(expected["destroy"])
    if set(created) != expected_created:
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP created IDs do not match the request",
            not_created_ids=_safe_identifiers(sorted(expected_created ^ set(created))),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    if set(updated) != expected_updated:
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP updated IDs do not match the request",
            not_updated_ids=_safe_identifiers(sorted(expected_updated ^ set(updated))),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    if set(destroyed) != expected_destroyed:
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP destroyed IDs do not match the request",
            not_destroyed_ids=_safe_identifiers(sorted(expected_destroyed ^ set(destroyed))),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    for identifier, value in created.items():
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            _raise(
                request_method=expected["method"],
                tag=expected["tag"],
                action=action,
                description="JMAP created object is malformed",
                not_created_ids=_safe_identifiers([identifier]),
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
    if any(not isinstance(value, dict) for value in updated.values()):
        _raise(
            request_method=expected["method"],
            tag=expected["tag"],
            action=action,
            description="JMAP updated object is malformed",
            not_updated_ids=_safe_identifiers(list(updated)),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )


def validate_jmap_response(
    request_body: Any,
    response_body: Any,
    *,
    action: str = "route-lifecycle",
    http_status: str = "200",
    endpoint_path: str = "/jmap/",
) -> None:
    """Validate every response in a single JMAP request envelope."""
    expected = _request_calls(
        request_body,
        action=action,
        http_status=http_status,
        endpoint_path=endpoint_path,
    )
    if not isinstance(response_body, dict):
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP response is not an object",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    allowed_fields = {"methodResponses", "sessionState"}
    if "createdIds" in request_body:
        allowed_fields.add("createdIds")
    unknown_fields = set(response_body) - allowed_fields
    if unknown_fields:
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP response envelope contains unknown fields",
            invalid_properties=",".join(sorted(_safe_identifier(field) for field in unknown_fields)),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    missing_fields = {"methodResponses", "sessionState"} - set(response_body)
    if missing_fields:
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP response envelope is missing required fields",
            invalid_properties=",".join(sorted(missing_fields)),
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    if not isinstance(response_body["sessionState"], str) or not response_body["sessionState"]:
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP response sessionState is malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    if "createdIds" in response_body and (
        "createdIds" not in request_body
        or not isinstance(response_body["createdIds"], dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in response_body["createdIds"].items()
        )
    ):
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP response createdIds is unsupported or malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    responses = response_body.get("methodResponses")
    if not isinstance(responses, list) or len(responses) != len(expected):
        _raise(
            request_method=expected[0]["method"],
            tag=expected[0]["tag"],
            action=action,
            description="JMAP methodResponses is malformed",
            http_status=http_status,
            endpoint_path=endpoint_path,
        )
    by_tag: dict[str, list[Any]] = {}
    for response in responses:
        if not isinstance(response, list) or len(response) != 3 or not isinstance(response[2], str):
            _raise(
                request_method=expected[0]["method"],
                tag=expected[0]["tag"],
                action=action,
                description="JMAP method response shape is malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if response[2] in by_tag:
            _raise(
                request_method=response[0] if isinstance(response[0], str) else "unknown-method",
                tag=response[2],
                action=action,
                description="JMAP response invocation tag is duplicated",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        by_tag[response[2]] = response
    for expected_call in expected:
        response = by_tag.get(expected_call["tag"])
        if response is None:
            _raise(
                request_method=expected_call["method"],
                tag=expected_call["tag"],
                action=action,
                description="JMAP response invocation tag is missing",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        method, arguments, _tag = response
        if method == "error":
            _raise(
                request_method=expected_call["method"],
                tag=expected_call["tag"],
                action=action,
                error_type=_error_type(arguments),
                description="JMAP method-level error response",
                invalid_properties=(
                    ",".join(_safe_identifier(value) for value in arguments.get("properties", []))
                    if isinstance(arguments, dict) and isinstance(arguments.get("properties"), list)
                    else "NONE"
                ),
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if method != expected_call["method"]:
            _raise(
                request_method=expected_call["method"],
                tag=expected_call["tag"],
                action=action,
                description="JMAP response method does not match the request",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if not isinstance(arguments, dict):
            _raise(
                request_method=method,
                tag=expected_call["tag"],
                action=action,
                description="JMAP method response arguments are malformed",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        if method.endswith("/get"):
            if set(arguments) - {"accountId", "state", "list", "notFound"} or not isinstance(arguments.get("list"), list):
                _raise(
                    request_method=method,
                    tag=expected_call["tag"],
                    action=action,
                    description="JMAP get response shape is invalid",
                    http_status=http_status,
                    endpoint_path=endpoint_path,
                )
        elif method.endswith("/set"):
            _check_set_response(
                expected=expected_call,
                arguments=arguments,
                action=action,
                http_status=http_status,
                endpoint_path=endpoint_path,
            )
        elif method.endswith("/query"):
            allowed = {
                "accountId",
                "queryState",
                "canCalculateChanges",
                "position",
                "ids",
                "total",
                "limit",
                "collapseProperties",
                "notFound",
            }
            unknown = set(arguments) - allowed
            if unknown:
                _raise(
                    request_method=method,
                    tag=expected_call["tag"],
                    action=action,
                    description="JMAP query response contains unknown fields",
                    invalid_properties=",".join(
                        sorted(_safe_identifier(item) for item in unknown)
                    ),
                    http_status=http_status,
                    endpoint_path=endpoint_path,
                )
            if (
                not isinstance(arguments.get("queryState"), str)
                or not arguments["queryState"]
                or not isinstance(arguments.get("canCalculateChanges"), bool)
                or not isinstance(arguments.get("position"), int)
                or isinstance(arguments.get("position"), bool)
                or not isinstance(arguments.get("ids"), list)
                or any(not isinstance(identifier, str) for identifier in arguments["ids"])
                or (
                    "total" in arguments
                    and (
                        not isinstance(arguments["total"], int)
                        or isinstance(arguments["total"], bool)
                    )
                )
                or (
                    "limit" in arguments
                    and (
                        not isinstance(arguments["limit"], int)
                        or isinstance(arguments["limit"], bool)
                    )
                )
                or (
                    "notFound" in arguments
                    and (
                        not isinstance(arguments["notFound"], list)
                        or any(
                            not isinstance(identifier, str)
                            for identifier in arguments["notFound"]
                        )
                    )
                )
            ):
                _raise(
                    request_method=method,
                    tag=expected_call["tag"],
                    action=action,
                    description="JMAP query response shape is invalid",
                    http_status=http_status,
                    endpoint_path=endpoint_path,
                )
        else:
            _raise(
                request_method=method,
                tag=expected_call["tag"],
                action=action,
                description="JMAP method is outside the validated lifecycle set",
                http_status=http_status,
                endpoint_path=endpoint_path,
            )


def format_diagnostics(error: JmapResponseError) -> str:
    return "\n".join(f"{field}={error.diagnostics[field]}" for field in DIAGNOSTIC_FIELDS)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--action", default="route-lifecycle")
    parser.add_argument("--http-status", default="200")
    parser.add_argument("--endpoint-path", default="/jmap/")
    args = parser.parse_args(argv)
    try:
        with open(args.request_file, encoding="utf-8") as handle:
            request_body = json.load(handle)
        response_body = json.load(sys.stdin)
        validate_jmap_response(
            request_body,
            response_body,
            action=args.action,
            http_status=args.http_status,
            endpoint_path=args.endpoint_path,
        )
    except JmapResponseError as error:
        print(format_diagnostics(error), file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        error = JmapResponseError(
            _diagnostics(
                action=args.action,
                method="unknown-method",
                tag="unknown-tag",
                error_type="malformedJson",
                description="JMAP request or response JSON is malformed",
                http_status=args.http_status,
                endpoint_path=args.endpoint_path,
            )
        )
        print(format_diagnostics(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
