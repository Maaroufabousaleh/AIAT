from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from mas_core.worker_contract.controller import WorkerRunController, WorkerRunError
from mas_core.worker_contract.models import (
    ModelProfileReference,
    WorkerResult,
    WorkerRunRequest,
    WorkerUsage,
)

SNAPSHOT_ID = UUID("00000000-0000-4000-a000-000000000901")


class _SnapshotStorage:
    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.snapshot = snapshot

    async def get_model_resolution_snapshot(self, snapshot_id: UUID) -> dict[str, object] | None:
        if snapshot_id != SNAPSHOT_ID:
            return None
        return self.snapshot


def _request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key="model-attribution-test",
        worker_id="worker-attribution-test",
        task_type="model-attribution",
        resolved_model_profile=ModelProfileReference(
            profile_id="profile-v1",
            version="v1",
            exact_model_id="model-v1",
            resolution_snapshot_id=SNAPSHOT_ID,
        ),
    )


def _result(*, provider: str | None, exact_model_id: str | None) -> WorkerResult:
    request = _request()
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"ok": True},
        usage=WorkerUsage(
            prompt_tokens=1,
            completion_tokens=1,
            provider=provider,
            exact_model_id=exact_model_id,
        ),
    )


def _controller(snapshot: dict[str, object] | None) -> WorkerRunController:
    return WorkerRunController(storage=_SnapshotStorage(snapshot))


@pytest.mark.asyncio
async def test_model_usage_must_match_snapshot() -> None:
    request = _request()
    result = _result(provider="provider-v1", exact_model_id="model-v1")

    await _controller(
        {
            "provider_id": "provider-v1",
            "exact_model_id": "model-v1",
        }
    )._validate_result_model_attribution(request, result, SNAPSHOT_ID)


@pytest.mark.asyncio
async def test_model_usage_mismatch_is_rejected_before_persistence() -> None:
    request = _request()
    result = _result(provider="wrong-provider", exact_model_id="model-v1")

    with pytest.raises(WorkerRunError) as caught:
        await _controller(
            {
                "provider_id": "provider-v1",
                "exact_model_id": "model-v1",
            }
        )._validate_result_model_attribution(request, result, SNAPSHOT_ID)

    assert caught.value.code == "MODEL_USAGE_ATTRIBUTION_MISMATCH"
    assert caught.value.details == {
        "expected_provider_id": "provider-v1",
        "expected_exact_model_id": "model-v1",
        "observed_provider_id": "wrong-provider",
        "observed_exact_model_id": "model-v1",
    }


@pytest.mark.asyncio
async def test_missing_model_usage_is_rejected_closed() -> None:
    request = _request()
    result = _result(provider=None, exact_model_id=None)

    with pytest.raises(WorkerRunError, match="does not match") as caught:
        await _controller(
            {
                "provider_id": "provider-v1",
                "exact_model_id": "model-v1",
            }
        )._validate_result_model_attribution(request, result, SNAPSHOT_ID)

    assert caught.value.code == "MODEL_USAGE_ATTRIBUTION_MISMATCH"
