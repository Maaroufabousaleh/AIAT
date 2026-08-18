from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_worker_independent_process_execution_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_worker_independent_process_execution_postgres", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_blocks_without_database_dsn() -> None:
    module = _module()
    report = asyncio.run(module._run(None))

    assert report["schema_version"] == (
        "aiat.worker-independent-process-execution-postgres-certification.v1"
    )
    assert report["status"] == "blocked"
    assert report["process_dispatch_performed"] is False
    assert report["external_network_access_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_fixture_worker_keeps_private_marker_out_of_usage_metadata() -> None:
    module = _module()
    request = module._request(
        run_id=module.RUN_A,
        idempotency_key=module.IDEMPOTENCY_A,
        trace_id=module.TRACE_A,
        span_id=module.SPAN_A,
    )

    result = asyncio.run(module._fixture_worker(request, object()))

    assert result.success is True
    assert result.usage is not None
    assert result.usage.provider == "fixture-process"
    assert result.usage.exact_model_id == "fixture-process-model-v1"
    assert module.PAYLOAD_MARKER in str(result.output)


def test_child_parser_requires_a_valid_run_id() -> None:
    module = _module()

    assert module.main(["--child", "--run-id", "not-a-uuid"]) == 2
