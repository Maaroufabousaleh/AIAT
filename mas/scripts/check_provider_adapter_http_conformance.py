"""Run deterministic mocked-HTTP conformance for the built-in adapters.

The fixture calls the real YouTrack and GitHub adapter methods with a local
response queue.  It covers configuration/health, projection, read-back,
pagination, deactivation, comments/links, GitHub source-control operations,
webhook normalization/signatures, and provider failure propagation.  No
provider URL, credential resolver, external account, or live state is used.
Licence/restriction metadata is recorded elsewhere and is not a predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
from collections import deque
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from mas_core.integrations.contracts import (
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    ProviderConnection,
)
from mas_core.integrations.providers.base import (
    ProviderRequestError,
    provider_failure_disposition,
)
from mas_core.integrations.providers.github import GitHubProvider
from mas_core.integrations.providers.youtrack import YouTrackProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CHECK_SCHEMA = "aiat.provider-adapter-http-conformance.v1"
GITHUB_CONNECTION_ID = UUID("00000000-0000-4000-a000-000000000811")
YOUTRACK_CONNECTION_ID = UUID("00000000-0000-4000-a000-000000000812")
PROJECT_ID = UUID("00000000-0000-4000-a000-000000000813")
WORK_ITEM_ID = UUID("00000000-0000-4000-a000-000000000814")


def _response(
    method: str,
    path: str,
    payload: Any,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=headers or {},
        request=httpx.Request(method, f"https://provider.fixture.invalid{path}"),
    )


class MockProviderHTTP:
    """A local response queue that mirrors the ProviderHTTP request surface."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        connection: ProviderConnection,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected mocked provider request: {method} {path}")
        return self.responses.popleft()


class FailingProviderHTTP:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def request(
        self,
        connection: ProviderConnection,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        self.calls += 1
        raise ProviderRequestError(method, path, self.status_code, "fixture failure")


def _github_connection() -> ProviderConnection:
    return ProviderConnection(
        id=GITHUB_CONNECTION_ID,
        provider_kind="github",
        display_name="AIAT mocked GitHub",
        base_url="https://github.fixture.invalid",
        credential_ref="fixture-github-ref",
        capability_profile="delivery",
        config={
            "repository": "acme/app",
            "webhook_secret_test_only": "fixture-github-secret",
        },
    )


def _youtrack_connection() -> ProviderConnection:
    return ProviderConnection(
        id=YOUTRACK_CONNECTION_ID,
        provider_kind="youtrack",
        display_name="AIAT mocked YouTrack",
        base_url="https://youtrack.fixture.invalid",
        credential_ref="fixture-youtrack-ref",
        config={
            "project_id": "0-1",
            "project_short_name": "AIAT",
            "integration_user_id": "u-1",
            "agile_board_id": "board-1",
            "webhook_header": "X-YouTrack-Token",
            "webhook_token_test_only": "fixture-youtrack-token",
        },
    )


def _work_item() -> CanonicalWorkItem:
    return CanonicalWorkItem(
        id=WORK_ITEM_ID,
        project_id=PROJECT_ID,
        title="Mocked adapter task",
        description="Provider-specific HTTP fixture",
        revision=2,
    )


def _call_pairs(mock: MockProviderHTTP) -> list[tuple[str, str]]:
    return [(str(call["method"]), str(call["path"])) for call in mock.calls]


def _assert_calls(mock: MockProviderHTTP, expected: list[tuple[str, str]]) -> None:
    actual = _call_pairs(mock)
    if actual != expected:
        raise AssertionError(f"mocked request sequence mismatch: expected {expected}, got {actual}")
    if mock.responses:
        raise AssertionError(f"{len(mock.responses)} mocked responses were not consumed")


async def _run_case(
    provider: str,
    case_id: str,
    operation: Callable[[], Awaitable[dict[str, Any] | None]],
) -> dict[str, Any]:
    try:
        detail = await operation()
    except Exception as exc:  # pragma: no cover - fixture report safety net
        return {
            "provider": provider,
            "case": case_id,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "provider": provider,
        "case": case_id,
        "passed": True,
        **(detail or {}),
    }


async def _github_cases() -> list[dict[str, Any]]:
    connection = _github_connection()
    cases: list[dict[str, Any]] = []

    async def health_and_configuration() -> dict[str, Any]:
        provider = GitHubProvider()
        mock = MockProviderHTTP(
            [
                _response("GET", "/app", {"id": 7, "name": "AIAT fixture"}),
                _response("GET", "/installation/repositories", [{"full_name": "acme/app"}]),
            ]
        )
        provider.http.request = mock.request
        health = await provider.health(connection)
        configuration = await provider.verify_configuration(connection)
        if health.get("ok") is not True or configuration.get("ok") is not True:
            raise AssertionError("GitHub health/configuration did not return ok=true")
        _assert_calls(
            mock,
            [("GET", "/app"), ("GET", "/installation/repositories")],
        )
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)]}

    cases.append(await _run_case("github", "health_and_configuration", health_and_configuration))

    async def work_projection_changes_and_archive() -> dict[str, Any]:
        provider = GitHubProvider()
        mock = MockProviderHTTP(
            [
                _response("POST", "/repos/acme/app/issues", {"number": 42, "html_url": "https://github.invalid/i/42", "updated_at": "v1"}),
                _response("PATCH", "/repos/acme/app/issues/42", {"number": 42, "updated_at": "v2"}),
                _response("GET", "/repos/acme/app/issues", [{"number": 42, "title": "fixture", "body": "body", "state": "open", "updated_at": "v2"}]),
                _response("GET", "/repos/acme/app/issues/42", {"number": 42, "title": "fixture", "body": "body", "state": "open", "updated_at": "v2"}),
                _response("PATCH", "/repos/acme/app/issues/42", {"number": 42, "state": "closed", "updated_at": "v3"}),
                _response("GET", "/repos/acme/app/issues/42", {"number": 42, "title": "fixture", "body": "body", "state": "closed", "updated_at": "v3"}),
                _response("POST", "/repos/acme/app/issues/42/comments", {"id": 90}),
            ]
        )
        provider.http.request = mock.request
        first = await provider.project_work_item(connection, _work_item(), idempotency_key="gh-create")
        replay = await provider.project_work_item(connection, _work_item(), external_id="42", idempotency_key="gh-replay")
        objects, cursor = await provider.list_changes(connection)
        before = await provider.read_work_item(connection, "42")
        archived = await provider.archive_work_item(connection, "42", idempotency_key="gh-archive")
        after = await provider.read_work_item(connection, "42")
        comment = await provider.project_comment(connection, external_id="42", body="review", idempotency_key="gh-comment")
        if str(first.external_id) != "42" or str(replay.external_id) != "42" or len(objects) != 1 or cursor != "v2":
            raise AssertionError("GitHub work-item projection/list cursor contract failed")
        if before.status != "open" or after.status != "closed" or str(archived.status) not in {"synced", "ProjectionStatus.SYNCED"}:
            raise AssertionError("GitHub archive did not preserve and close the issue")
        if str(comment.external_id) != "90":
            raise AssertionError("GitHub comment projection did not return its ID")
        _assert_calls(
            mock,
            [
                ("POST", "/repos/acme/app/issues"),
                ("PATCH", "/repos/acme/app/issues/42"),
                ("GET", "/repos/acme/app/issues"),
                ("GET", "/repos/acme/app/issues/42"),
                ("PATCH", "/repos/acme/app/issues/42"),
                ("GET", "/repos/acme/app/issues/42"),
                ("POST", "/repos/acme/app/issues/42/comments"),
            ],
        )
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)]}

    cases.append(await _run_case("github", "work_projection_changes_archive", work_projection_changes_and_archive))

    async def source_control_and_run_credential() -> dict[str, Any]:
        broker_calls: list[tuple[str, dict[str, str]]] = []

        async def broker(_connection: ProviderConnection, repository: str, permissions: dict[str, str]) -> dict[str, object]:
            broker_calls.append((repository, dict(permissions)))
            return {"token": "fixture-short-lived", "expires_at": "2026-08-10T01:00:00Z"}

        provider = GitHubProvider(run_credential_broker=broker)
        mock = MockProviderHTTP(
            [
                _response("POST", "/repos/acme/app/pulls", {"number": 9, "html_url": "https://github.invalid/pr/9", "updated_at": "pr-v1"}),
                _response("POST", "/repos/acme/app/check-runs", {"id": 77}),
                _response("GET", "/repos/acme/app/git/ref/heads/main", {"object": {"sha": "abc123"}}),
                _response("POST", "/repos/acme/app/git/refs", {"object": {"sha": "abc123"}}),
                _response("POST", "/repos/acme/app/pulls/9/comments", {"id": 88}),
                _response("GET", "/repos/acme/app/commits/deadbeef", {"sha": "deadbeef"}),
            ]
        )
        provider.http.request = mock.request
        pull_request = await provider.project_pull_request(
            connection,
            {"title": "fixture PR", "head": "aiat/fixture", "base": "main", "idempotency_key": "gh-pr"},
        )
        check = await provider.publish_check(connection, {"name": "fixture", "head_sha": "abc123"})
        branch = await provider.create_branch(connection, {"branch": "aiat/fixture", "from_ref": "main", "idempotency_key": "gh-branch"})
        review = await provider.publish_review_comment(connection, {"pull_request_number": 9, "body": "looks good", "idempotency_key": "gh-review"})
        commit = await provider.capture_commit_evidence(connection, {"sha": "deadbeef"})
        token = await provider.mint_run_credential(connection, "acme/app", {"contents": "write"})
        body = b"{}"
        signature = hmac.new(b"fixture-github-secret", body, hashlib.sha256).hexdigest()
        if not provider.verify_webhook(connection, body, {"X-Hub-Signature-256": f"sha256={signature}"}):
            raise AssertionError("GitHub webhook signature failed")
        if str(pull_request.external_id) != "9" or str(check.external_id) != "77" or str(branch.external_id) != "aiat/fixture" or str(review.external_id) != "88" or str(commit.external_id) != "deadbeef":
            raise AssertionError("GitHub source-control projection contract failed")
        if token.get("expires_at") is None or broker_calls != [("acme/app", {"contents": "write"})]:
            raise AssertionError("GitHub run-credential broker contract failed")
        _assert_calls(
            mock,
            [
                ("POST", "/repos/acme/app/pulls"),
                ("POST", "/repos/acme/app/check-runs"),
                ("GET", "/repos/acme/app/git/ref/heads/main"),
                ("POST", "/repos/acme/app/git/refs"),
                ("POST", "/repos/acme/app/pulls/9/comments"),
                ("GET", "/repos/acme/app/commits/deadbeef"),
            ],
        )
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)], "credential_broker_calls": len(broker_calls)}

    cases.append(await _run_case("github", "source_control_and_run_credential", source_control_and_run_credential))

    async def webhook_and_failure_contract() -> dict[str, Any]:
        provider = GitHubProvider()
        event = ExternalEvent(
            connection_id=connection.id,
            provider_delivery_id="github-fixture-delivery",
            event_type="issues",
            payload={"issue": {"id": 42, "number": 42, "title": "renamed", "body": "body", "state": "open", "updated_at": "v4", "user": {"login": "human"}}},
            verified=True,
        )
        command = provider.normalize_webhook(event)
        if command is None or command.fields.get("title") != "renamed" or command.expected_provider_version != "v4":
            raise AssertionError("GitHub webhook normalization lost renamed field/version")
        mock = FailingProviderHTTP(429)
        provider.http.request = mock.request
        try:
            await provider.project_work_item(connection, _work_item(), idempotency_key="gh-rate")
        except ProviderRequestError as exc:
            if provider_failure_disposition(exc.status_code) != "retryable":
                raise AssertionError("GitHub rate-limit failure was not retryable") from None
        else:
            raise AssertionError("GitHub mocked rate-limit failure was swallowed")
        return {"provider_failure_cases": {"429": "retryable"}, "mock_call_count": mock.calls}

    cases.append(await _run_case("github", "webhook_and_failure_contract", webhook_and_failure_contract))
    return cases


async def _youtrack_cases() -> list[dict[str, Any]]:
    connection = _youtrack_connection()
    cases: list[dict[str, Any]] = []

    async def health_and_configuration() -> dict[str, Any]:
        provider = YouTrackProvider()
        mock = MockProviderHTTP(
            [
                _response("GET", "/api/users/me?fields=id,login", {"id": "u-1", "login": "aiat"}),
                _response("GET", "/api/admin/projects", [{"id": "0-1", "shortName": "AIAT", "name": "AIAT"}]),
            ]
        )
        provider.http.request = mock.request
        health = await provider.health(connection)
        configuration = await provider.verify_configuration(connection)
        if health.get("ok") is not True or configuration.get("ok") is not True or configuration.get("discovered_projects") != 1:
            raise AssertionError("YouTrack health/configuration did not return the expected fixture")
        _assert_calls(mock, [("GET", "/api/users/me?fields=id,login"), ("GET", "/api/admin/projects")])
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)]}

    cases.append(await _run_case("youtrack", "health_and_configuration", health_and_configuration))

    async def work_projection_comments_and_links() -> dict[str, Any]:
        provider = YouTrackProvider()
        mock = MockProviderHTTP(
            [
                _response("POST", "/api/admin/projects/0-1", {"id": "0-1", "updated": 1}),
                _response("POST", "/api/agiles/board-1/sprints", {"id": "s-1", "version": 2}),
                _response("POST", "/api/issues", {"id": "YT-42", "idReadable": "AIAT-42", "updated": 3}),
                _response("GET", "/api/issues/YT-42", {"id": "YT-42", "idReadable": "AIAT-42", "summary": "fixture", "description": "body", "updated": 3, "project": {"id": "0-1"}, "customFields": [{"name": "Priority", "value": {"name": "Normal"}}]}),
                _response("POST", "/api/issues/YT-42/comments", {"id": "comment-1"}),
                _response("POST", "/api/issues/YT-42/links", {}, headers={"ETag": "link-v1"}),
            ]
        )
        provider.http.request = mock.request
        project = CanonicalProject(id=PROJECT_ID, name="Mocked AIAT project")
        iteration = CanonicalIteration(id=UUID("00000000-0000-4000-a000-000000000815"), project_id=PROJECT_ID, number=1, name="Fixture sprint")
        projected_project = await provider.project_project(connection, project, external_id="0-1", idempotency_key="yt-project")
        projected_iteration = await provider.project_iteration(connection, iteration, idempotency_key="yt-iteration")
        projected_work = await provider.project_work_item(connection, _work_item(), idempotency_key="yt-work")
        read = await provider.read_work_item(connection, "YT-42")
        comment = await provider.project_comment(connection, external_id="YT-42", body="comment", idempotency_key="yt-comment")
        link = await provider.project_link(connection, external_id="YT-42", link={"target_id": "YT-43"}, idempotency_key="yt-link")
        if str(projected_project.external_id) != "0-1" or str(projected_iteration.external_id) != "s-1" or str(projected_work.external_id) != "YT-42":
            raise AssertionError("YouTrack projection did not return stable external IDs")
        if read.priority != "Normal" or str(comment.external_id) != "comment-1" or link.external_id != "YT-42":
            raise AssertionError("YouTrack read/comment/link contract failed")
        _assert_calls(
            mock,
            [
                ("POST", "/api/admin/projects/0-1"),
                ("POST", "/api/agiles/board-1/sprints"),
                ("POST", "/api/issues"),
                ("GET", "/api/issues/YT-42"),
                ("POST", "/api/issues/YT-42/comments"),
                ("POST", "/api/issues/YT-42/links"),
            ],
        )
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)]}

    cases.append(await _run_case("youtrack", "work_projection_comments_links", work_projection_comments_and_links))

    async def incremental_and_actor_contract() -> dict[str, Any]:
        provider = YouTrackProvider()
        mock = MockProviderHTTP(
            [
                _response("GET", "/api/agiles/board-1/sprints", [{"id": "s-1", "name": "Sprint", "version": 2}]),
                _response("GET", "/api/issues", [{"id": "YT-42", "idReadable": "AIAT-42", "summary": "fixture", "updated": 20, "project": {"id": "0-1"}}]),
                _response("GET", "/api/admin/projects", [{"id": "0-1", "shortName": "AIAT", "name": "AIAT"}]),
                _response("GET", "/api/users", [{"id": "u-2", "login": "human", "email": "human@example.invalid", "fullName": "Human"}]),
            ]
        )
        provider.http.request = mock.request
        iterations, iteration_cursor = await provider.list_iterations(connection)
        changes, change_cursor = await provider.list_changes(connection)
        projects, project_cursor = await provider.list_projects(connection)
        actor = await provider.resolve_external_actor(connection, login="human", email="human@example.invalid")
        body = b"{\"event\":\"issue\"}"
        if not provider.verify_webhook(connection, body, {"X-YouTrack-Token": "fixture-youtrack-token"}):
            raise AssertionError("YouTrack webhook token failed")
        event = ExternalEvent(
            connection_id=connection.id,
            provider_delivery_id="youtrack-fixture-delivery",
            event_type="issueUpdated",
            payload={"issue": {"id": "YT-42", "updated": 21, "project": {"id": "0-1"}, "changedFields": [{"name": "Summary", "value": "renamed"}], "updatedBy": {"id": "u-2", "login": "human"}}},
            verified=True,
        )
        command = provider.normalize_webhook(event)
        if len(iterations) != 1 or iteration_cursor != "1" or len(changes) != 1 or change_cursor != "20" or len(projects) != 1 or project_cursor != "1":
            raise AssertionError("YouTrack incremental cursors were not stable")
        if actor["id"] != "u-2" or command is None or command.fields.get("title") != "renamed" or command.expected_provider_version != "21":
            raise AssertionError("YouTrack actor/webhook normalization failed")
        _assert_calls(
            mock,
            [
                ("GET", "/api/agiles/board-1/sprints"),
                ("GET", "/api/issues"),
                ("GET", "/api/admin/projects"),
                ("GET", "/api/users"),
            ],
        )
        return {"mock_call_count": len(mock.calls), "request_paths": [path for _, path in _call_pairs(mock)]}

    cases.append(await _run_case("youtrack", "incremental_actor_webhook", incremental_and_actor_contract))

    async def failure_contract() -> dict[str, Any]:
        provider = YouTrackProvider()
        mock = FailingProviderHTTP(403)
        provider.http.request = mock.request
        try:
            await provider.project_work_item(connection, _work_item(), idempotency_key="yt-permission")
        except ProviderRequestError as exc:
            if provider_failure_disposition(exc.status_code) != "permanent":
                raise AssertionError("YouTrack permission-loss failure was not permanent") from None
        else:
            raise AssertionError("YouTrack mocked permission failure was swallowed")
        return {"provider_failure_cases": {"403": "permanent"}, "mock_call_count": mock.calls}

    cases.append(await _run_case("youtrack", "failure_contract", failure_contract))
    return cases


async def _build_report() -> dict[str, Any]:
    cases = [*_awaitable_cases(await _github_cases()), *_awaitable_cases(await _youtrack_cases())]
    errors = [case for case in cases if not case.get("passed")]
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "provider_count": 2,
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(errors),
        "cases": cases,
        "network_access_performed": False,
        "mutation_performed": False,
        "live_provider_status": "not_checked",
        "licence_metadata_is_gate": False,
        "errors": errors,
        "scope": "real YouTrack/GitHub adapters over local mocked HTTP responses only",
    }


def _awaitable_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the report assembly explicit and deterministic for type checkers."""
    return list(cases)


def build_report() -> dict[str, Any]:
    return asyncio.run(_build_report())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require live provider certification")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "live provider HTTP, account, outage, and restore certification requires a selected sandbox",
            "network_access_performed": False,
            "mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
        exit_code = 2
    else:
        report = {"mode": "mocked_fixture", **build_report()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"provider adapter HTTP conformance: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
