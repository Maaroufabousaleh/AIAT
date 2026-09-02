"""Deterministic provider-contract fixtures for safe adapter certification.

The runner is intentionally an adapter test harness, not a production sync
job.  It exercises a disposable provider connection with canonical project and
work-item fixtures, records PASS/SKIP/FAIL evidence, and keeps provider HTTP
failure classification in one shared vocabulary.  A real provider must still
run its own mocked HTTP and live outage/restore tests before activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid5

from .contracts import CanonicalProject, CanonicalWorkItem, ExternalEvent, ProviderConnection
from .providers.base import provider_failure_disposition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CONFORMANCE_FIXTURE_VERSION = "aiat.provider-conformance.v1"
ConformanceStatus = Literal["PASS", "FAIL", "SKIP"]


@dataclass(frozen=True)
class ConformanceCaseResult:
    case_id: str
    status: ConformanceStatus
    detail: str


@dataclass(frozen=True)
class ProviderConformanceReport:
    fixture_version: str
    provider_kind: str
    adapter_version: str
    cases: tuple[ConformanceCaseResult, ...]

    @property
    def passed(self) -> bool:
        return not any(case.status == "FAIL" for case in self.cases)

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(1 for case in self.cases if case.status == status)
            for status in ("PASS", "FAIL", "SKIP")
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture_version": self.fixture_version,
            "provider_kind": self.provider_kind,
            "adapter_version": self.adapter_version,
            "passed": self.passed,
            "counts": self.counts,
            "cases": [
                {
                    "case_id": case.case_id,
                    "status": case.status,
                    "detail": case.detail,
                }
                for case in self.cases
            ],
        }


async def run_work_management_conformance(
    provider: Any,
    connection: ProviderConnection,
) -> ProviderConformanceReport:
    """Run the common work-management fixture against a disposable adapter.

    Unsupported capabilities are recorded as ``SKIP`` rather than being
    mistaken for failures.  Any supported operation that raises is a failure.
    The caller owns the connection and must provide a fake/sandbox credential;
    this helper never resolves credentials or calls the orchestrator storage.
    """

    provider_kind = str(getattr(provider, "kind", connection.provider_kind))
    cases: list[ConformanceCaseResult] = []

    async def case(
        case_id: str,
        operation: Callable[[], Awaitable[str | None]],
    ) -> None:
        try:
            detail = await operation()
        except NotImplementedError as exc:
            cases.append(ConformanceCaseResult(case_id, "SKIP", str(exc) or "not implemented"))
        except Exception as exc:  # pragma: no cover - exercised by provider adapters
            cases.append(ConformanceCaseResult(case_id, "FAIL", f"{type(exc).__name__}: {exc}"))
        else:
            cases.append(ConformanceCaseResult(case_id, "PASS", detail or "fixture passed"))

    try:
        capabilities = await provider.capabilities(connection)
    except Exception as exc:
        cases.append(
            ConformanceCaseResult(
                "capabilities", "FAIL", f"{type(exc).__name__}: {exc}"
            )
        )
        return ProviderConformanceReport(
            CONFORMANCE_FIXTURE_VERSION,
            provider_kind,
            "unknown",
            tuple(cases),
        )

    adapter_version = str(getattr(capabilities, "adapter_version", "unknown"))
    if not adapter_version.strip():
        cases.append(ConformanceCaseResult("capabilities", "FAIL", "adapter_version is blank"))
    else:
        cases.append(
            ConformanceCaseResult(
                "capabilities",
                "PASS",
                f"{provider_kind} adapter version {adapter_version}",
            )
        )

    await case(
        "health",
        lambda: _health_case(provider, connection),
    )
    await case(
        "configuration",
        lambda: _configuration_case(provider, connection),
    )

    if not bool(getattr(capabilities, "work_management", False)):
        for case_id in (
            "project_projection",
            "iteration_projection",
            "work_item_idempotency",
            "pagination_cursor",
            "deletion_archive",
            "renamed_field_webhook",
        ):
            cases.append(ConformanceCaseResult(case_id, "SKIP", "work management is not supported"))
    else:
        project_id = uuid5(connection.id, "aiat-conformance-project")
        work_item_id = uuid5(connection.id, "aiat-conformance-work-item")
        project = CanonicalProject(id=project_id, name="AIAT provider fixture")
        work_item = CanonicalWorkItem(
            id=work_item_id,
            project_id=project_id,
            title="Provider fixture work item",
            description="Disposable conformance data",
            revision=2,
        )
        external_work_id: str | None = None

        if bool(getattr(capabilities, "projects", False)):
            async def project_projection() -> str:
                result = await provider.project_project(
                    connection, project, idempotency_key="conformance-project-v1"
                )
                if not result.external_id:
                    raise AssertionError("project projection returned no external_id")
                return "canonical project projected"

            await case("project_projection", project_projection)
        else:
            cases.append(ConformanceCaseResult("project_projection", "SKIP", "projects are not supported"))

        if bool(getattr(capabilities, "iterations", False)):
            async def iteration_projection() -> str:
                from .contracts import CanonicalIteration

                result = await provider.project_iteration(
                    connection,
                    CanonicalIteration(
                        id=uuid5(connection.id, "aiat-conformance-iteration"),
                        project_id=project_id,
                        number=1,
                        name="Fixture sprint",
                    ),
                    idempotency_key="conformance-iteration-v1",
                )
                if not result.external_id:
                    raise AssertionError("iteration projection returned no external_id")
                return "canonical iteration projected"

            await case("iteration_projection", iteration_projection)
        else:
            cases.append(ConformanceCaseResult("iteration_projection", "SKIP", "iterations are not supported"))

        async def work_item_idempotency() -> str:
            nonlocal external_work_id
            first = await provider.project_work_item(
                connection, work_item, idempotency_key="conformance-work-v1"
            )
            if not first.external_id:
                raise AssertionError("work-item projection returned no external_id")
            external_work_id = str(first.external_id)
            second = await provider.project_work_item(
                connection,
                work_item,
                external_id=external_work_id,
                idempotency_key="conformance-work-v1",
            )
            if str(second.external_id) != external_work_id:
                raise AssertionError("replayed projection changed external_id")
            return "replayed projection retained external identity"

        await case("work_item_idempotency", work_item_idempotency)

        async def pagination_cursor() -> str:
            objects, cursor = await provider.list_changes(connection)
            ids = [str(item.external_id) for item in objects]
            if len(ids) != len(set(ids)):
                raise AssertionError("provider returned duplicate external IDs in one page")
            if cursor is not None and not str(cursor).strip():
                raise AssertionError("provider returned a blank incremental cursor")
            return f"listed {len(ids)} objects with an opaque cursor"

        await case("pagination_cursor", pagination_cursor)

        async def deletion_archive() -> str:
            if not external_work_id:
                raise AssertionError("work item was not projected")
            before = await provider.read_work_item(connection, external_work_id)
            result = await provider.archive_work_item(
                connection,
                external_work_id,
                idempotency_key="conformance-archive-v1",
            )
            after = await provider.read_work_item(connection, external_work_id)
            if str(result.status) not in {"ProjectionStatus.SYNCED", "synced"}:
                raise AssertionError("archive did not return a synced projection result")
            if before.status is not None and after.status == before.status:
                raise AssertionError("archive did not change provider work-item status")
            return "archive/deactivation preserved the object and changed state"

        await case("deletion_archive", deletion_archive)

        if bool(getattr(capabilities, "webhooks", False)):
            async def renamed_field_webhook() -> str:
                event = ExternalEvent(
                    connection_id=connection.id,
                    provider_delivery_id="conformance-delivery-v1",
                    event_type="work_item.updated",
                    payload={
                        "operation": "update",
                        "object": {
                            "object_type": "work_item",
                            "external_id": external_work_id or "fixture-work-item",
                            "provider_version": "fixture-revision-2",
                            "fields": {"title": "renamed fixture title", "status": "done"},
                        },
                    },
                    verified=True,
                )
                command = provider.normalize_webhook(event)
                if command is None:
                    raise AssertionError("provider did not normalize the fixture webhook")
                if command.expected_provider_version != "fixture-revision-2":
                    raise AssertionError("provider lost the stale-revision marker")
                if command.fields.get("title") != "renamed fixture title":
                    raise AssertionError("provider lost the canonical renamed title field")
                return "renamed canonical field and provider revision retained"

            await case("renamed_field_webhook", renamed_field_webhook)
        else:
            cases.append(ConformanceCaseResult("renamed_field_webhook", "SKIP", "webhooks are not supported"))

    async def failure_classification() -> str:
        expected = {
            401: "permanent",  # credential/permission loss: surface for operator action
            403: "permanent",
            404: "permanent",
            409: "retryable",  # stale revision after a refresh/reconciliation
            412: "retryable",
            429: "retryable",  # rate limit
            503: "retryable",  # partial provider outage
        }
        for status_code, disposition in expected.items():
            actual = provider_failure_disposition(status_code)
            if actual != disposition:
                raise AssertionError(f"HTTP {status_code}: expected {disposition}, got {actual}")
        return "rate-limit, stale-revision, partial-outage, and permission-loss statuses classified"

    await case("failure_classification", failure_classification)
    return ProviderConformanceReport(
        CONFORMANCE_FIXTURE_VERSION,
        provider_kind,
        adapter_version,
        tuple(cases),
    )


async def _health_case(provider: Any, connection: ProviderConnection) -> str:
    value = await provider.health(connection)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise AssertionError("health did not return ok=true")
    return "health returned ok=true"


async def _configuration_case(provider: Any, connection: ProviderConnection) -> str:
    value = await provider.verify_configuration(connection)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise AssertionError("configuration verification did not return ok=true")
    return "configuration verification returned ok=true"
