from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from redis.exceptions import ResponseError

SCRIPT = Path(__file__).resolve().parents[1] / "check_live_redis_restart_recovery.py"


def _module():
    spec = importlib.util.spec_from_file_location("live_redis_restart_recovery", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_missing_consumer_group_is_treated_as_zero_pending() -> None:
    module = _module()

    class MissingGroupRedis:
        async def xpending(self, _key, _group):
            raise ResponseError("NOGROUP No such key or consumer group")

    assert asyncio.run(module._pending_count(MissingGroupRedis(), "stream:fixture", "group:fixture")) == 0


def test_redis_acl_error_is_not_suppressed_as_an_empty_pending_list() -> None:
    module = _module()

    class DeniedRedis:
        async def xpending(self, _key, _group):
            raise ResponseError("NOPERM user cannot run XPENDING")

    with pytest.raises(ResponseError, match="NOPERM"):
        asyncio.run(module._pending_count(DeniedRedis(), "stream:fixture", "group:fixture"))


def test_live_restart_guard_counts_only_team_runner_services(monkeypatch) -> None:
    module = _module()

    def fake_docker(_context, *arguments, **_kwargs):
        if arguments[0] == "ps":
            return "runner-a\napi\nrunner-b"
        labels = {
            "runner-a": {"com.docker.compose.service": "team-exec-ceo"},
            "api": {"com.docker.compose.service": "orchestrator-api"},
            "runner-b": {"com.docker.compose.service": "team-dept-qa"},
        }
        return json.dumps(labels[arguments[-1]])

    monkeypatch.setattr(module, "_docker", fake_docker)

    assert module._active_team_runner_count("aiat-wsl", "mas") == 2
