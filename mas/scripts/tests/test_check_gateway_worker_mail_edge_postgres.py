from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_worker_mail_edge_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_gateway_worker_mail_edge_postgres", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_blocks_without_both_database_dsns() -> None:
    module = _module()
    report = asyncio.run(module._run(None, None))

    assert report["schema_version"] == (
        "aiat.gateway-worker-mail-edge-postgres-certification.v1"
    )
    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_fixture_observations_are_correlated_and_payload_free() -> None:
    module = _module()
    observations = module._fixture_observations()

    assert len(observations) == 3
    assert {item.trace_id for item in observations} == {module.TRACE_ID}
    assert {item.worker_id for item in observations} == {module.WORKER_ID_TEXT}
    assert {item.event_type for item in observations} == {"queued", "delivered", "bounced"}
    assert all(module.PAYLOAD_MARKER not in item.model_dump_json() for item in observations)


def test_storage_projection_ignores_storage_only_columns() -> None:
    module = _module()
    observation = module._fixture_observations()[1]
    row = {
        **observation.model_dump(mode="python"),
        "metadata_json": observation.metadata,
        "received_at": observation.occurred_at,
    }

    projected = module._safe_mail_rows([row])

    assert projected == [observation]
    assert module.PAYLOAD_MARKER not in projected[0].model_dump_json()
