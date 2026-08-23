"""Offline tests for the pinned OmniRoute readiness contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "check_openhands_omniroute_readiness.py"
    spec = importlib.util.spec_from_file_location("check_openhands_omniroute_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _opener_for(statuses):
    values = iter(statuses)

    def opener(_request, timeout):
        del timeout
        value = next(values)
        if isinstance(value, type):
            raise value("cold start")
        return _Response(value)

    return opener


def _sleep(_seconds):
    return None


def test_public_monitoring_health_2xx_passes():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/monitoring/health",
        attempts=2,
        opener=_opener_for([200]),
        sleep=_sleep,
    )
    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["application_health_endpoint"].endswith("/api/monitoring/health")
    assert report["last_http_status"] == 200
    assert report["raw_response_retained"] is False


def test_persistent_401_is_auth_contract_failure_not_startup_failure():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/monitoring/health",
        attempts=10,
        opener=_opener_for([401, 401, 401]),
        sleep=_sleep,
    )
    assert exit_code != 0
    assert report["failure_class"] == "OMNIROUTE_HEALTH_AUTH_CONTRACT_FAILURE"
    assert report["last_http_status"] == 401


def test_persistent_5xx_is_application_health_failure():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/monitoring/health",
        attempts=2,
        opener=_opener_for([503, 503]),
        sleep=_sleep,
    )
    assert exit_code != 0
    assert report["failure_class"] == "OMNIROUTE_APPLICATION_HEALTH_FAILURE"
    assert report["application_health_status"] == "APPLICATION_FAILURE"


def test_cold_connection_exhaustion_remains_starting_and_times_out():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/monitoring/health",
        attempts=2,
        opener=_opener_for([ConnectionRefusedError, ConnectionRefusedError]),
        sleep=_sleep,
    )
    assert exit_code != 0
    assert report["failure_class"] == "OMNIROUTE_HEALTH_TIMEOUT"
    assert report["application_health_status"] == "STARTING"
    assert report["last_observation"] == "STARTING"


def test_container_exit_is_startup_failure_even_if_endpoint_was_ready():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/monitoring/health",
        attempts=1,
        run_exit_code=1,
        container_running=False,
        opener=_opener_for([200]),
        sleep=_sleep,
    )
    assert exit_code != 0
    assert report["failure_class"] == "OMNIROUTE_STARTUP_FAILURE"
    assert report["container_start_status"] == "BLOCKED"


def test_wrong_health_endpoint_is_rejected():
    module = _load()
    report, exit_code = module.probe(
        url="http://127.0.0.1:20128/api/health/ping",
        attempts=1,
        opener=_opener_for([200]),
        sleep=_sleep,
    )
    assert exit_code != 0
    assert report["failure_class"] == "OMNIROUTE_HEALTH_ENDPOINT_INVALID"
