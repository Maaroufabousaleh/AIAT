from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_worker_host_execution_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_worker_host_execution_postgres", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_blocks_without_database_dsn() -> None:
    module = _module()
    report = module._blocked("missing-dsn")
    assert report["schema_version"] == "aiat.worker-host-execution-postgres-certification.v1"
    assert report["execution_schema"] == "aiat.worker-host-execution.v1"
    assert report["binding_schema"] == "aiat.worker-run-host-binding.v1"
    assert report["status"] == "blocked"
    assert report["worker_dispatch_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_checker_dsn_normalization_is_explicit() -> None:
    module = _module()
    assert (
        module._normalize_dsn("postgresql://user:pass@db/mas")
        == "postgresql+asyncpg://user:pass@db/mas"
    )
    assert (
        module._normalize_dsn("postgres://user:pass@db/mas")
        == "postgresql+asyncpg://user:pass@db/mas"
    )
    assert module._normalize_dsn("${POSTGRES_DSN}") is None
