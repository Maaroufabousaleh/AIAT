from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1]
MODULE_PATH = GATEWAY / "scripts" / "stalwart_jmap_response.py"
SPEC = importlib.util.spec_from_file_location("stalwart_jmap_response", MODULE_PATH)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def _request(method: str = "x:MtaRoute/set", arguments: dict | None = None, tag: str = "call") -> dict:
    return {"methodCalls": [[method, arguments or {}, tag]]}


def _set_response(
    method: str = "x:MtaRoute/set",
    arguments: dict | None = None,
    tag: str = "call",
) -> dict:
    return {"methodResponses": [[method, arguments or {}, tag]]}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("notCreated", {"relay": {"type": "invalidProperties", "properties": ["name"]}}),
        ("notUpdated", {"relay": {"type": "invalidProperties", "properties": ["route"]}}),
        ("notDestroyed", {"remote": {"type": "notFound"}}),
    ],
)
def test_http_200_method_level_set_failures_are_rejected(field: str, value: dict) -> None:
    request_body = _request(arguments={"create": {"relay": {}}})
    response = _set_response(arguments={field: value})
    with pytest.raises(validator.JmapResponseError) as exc_info:
        validator.validate_jmap_response(request_body, response)
    diagnostics = exc_info.value.diagnostics
    assert diagnostics["HTTP_STATUS"] == "200"
    assert diagnostics[field.upper().replace("NOT", "NOT_") + "_IDS"] != "NONE"


def test_http_200_jmap_error_and_invalid_properties_are_safe() -> None:
    secret = "S" * 48
    response = _set_response(
        method="error",
        arguments={"type": "invalidProperties", "description": secret, "properties": ["name"]},
    )
    with pytest.raises(validator.JmapResponseError) as exc_info:
        validator.validate_jmap_response(_request(arguments={"create": {"relay": {}}}), response)
    rendered = validator.format_diagnostics(exc_info.value)
    assert "ERROR_TYPE=invalidProperties" in rendered
    assert "INVALID_PROPERTIES=name" in rendered
    assert secret not in rendered
    assert "methodResponses" not in rendered


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"methodResponses": "malformed"},
        {"methodResponses": [["x:MtaRoute/set", {}, "wrong-tag"]]},
        {"methodResponses": [["x:MtaRoute/get", {"list": []}, "call"]]},
        {"methodResponses": [["x:MtaRoute/set", {"created": {}}, "call"]]},
    ],
)
def test_malformed_wrong_tag_and_missing_success_method_fail(response: dict) -> None:
    with pytest.raises(validator.JmapResponseError):
        validator.validate_jmap_response(_request(arguments={"create": {"relay": {}}}), response)


def test_successful_route_create_update_and_destroy_are_accepted() -> None:
    create_request = _request(arguments={"create": {"relay": {"name": "resend-relay"}}})
    validator.validate_jmap_response(
        create_request,
        _set_response(arguments={"created": {"relay": {"id": "route-1"}}}),
    )

    update_request = _request(arguments={"update": {"singleton": {"route": {}}}})
    validator.validate_jmap_response(
        update_request,
        _set_response(arguments={"updated": {"singleton": {}}}),
    )

    destroy_request = _request(arguments={"destroy": ["route-1"]})
    validator.validate_jmap_response(
        destroy_request,
        _set_response(arguments={"destroyed": ["route-1"]}),
    )


def test_get_requires_a_list_and_no_unknown_response_shape() -> None:
    request_body = _request("x:MtaRoute/get", {}, "routes")
    with pytest.raises(validator.JmapResponseError):
        validator.validate_jmap_response(
            request_body,
            {"methodResponses": [["x:MtaRoute/get", {"list": []}, "routes"], ["extra", {}, "extra"]]},
        )
    with pytest.raises(validator.JmapResponseError):
        validator.validate_jmap_response(
            request_body,
            {"methodResponses": [["x:MtaRoute/get", {"list": "bad"}, "routes"]]},
        )


def test_validator_diagnostics_contain_only_safe_fields() -> None:
    request_body = _request(arguments={"create": {"relay": {}}})
    with pytest.raises(validator.JmapResponseError) as exc_info:
        validator.validate_jmap_response(
            request_body,
            _set_response(arguments={"notCreated": {"relay": {"type": "invalidProperties"}}}),
            action="apply",
            http_status="422",
            endpoint_path="/jmap/",
        )
    lines = validator.format_diagnostics(exc_info.value).splitlines()
    assert [line.split("=", 1)[0] for line in lines] == list(validator.DIAGNOSTIC_FIELDS)
    assert all("Authorization" not in line for line in lines)
    assert all("Bearer" not in line for line in lines)
