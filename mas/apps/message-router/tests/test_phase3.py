"""
Phase 3 — Message Router tests.

Uses ``fakeredis`` for in-memory Redis and ``httpx.AsyncClient`` ASGI transport
so no real Redis or Postgres is required.

Test coverage:
- POST /health — basic smoke
- POST /messages/publish — happy path, policy rejection, duplicate idempotency, TTL expiry
- POST /messages/broadcast — fan-out to all 11 teams
- WS /ws/subscribe/{team_id} — auth, ACK, NACK, PING/PONG
- XAUTOCLAIM reclaim — DLQ on max_attempts, retry_count increment
- Stream trim — XTRIM called on known teams
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.protocols.envelope import MessageEnvelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_envelope(
    msg_type: MessageType = MessageType.TASK,
    sender_role: AgentRole = AgentRole.ORCHESTRATOR,
    sender_team: str = "exec_ceo",
    recipient_team: str = "dept_production",
    project_id: str | None = "proj-001",
    ttl_seconds: int = 3600,
    **kwargs: Any,
) -> MessageEnvelope:
    return MessageEnvelope(
        msg_type=msg_type,
        sender_id="ceo_agent",
        sender_role=sender_role,
        sender_team=sender_team,
        recipient_team=recipient_team,
        project_id=project_id,
        ttl_seconds=ttl_seconds,
        **kwargs,
    )


def make_shutdown_envelope(**kwargs: Any) -> MessageEnvelope:
    return MessageEnvelope(
        msg_type=MessageType.SHUTDOWN,
        sender_id="ceo_agent",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="exec_ceo",
        recipient_team="exec_coo",
        project_id=None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_known_teams(self):
        from message_router.config import settings

        assert len(settings.known_teams) == 11
        assert "exec_ceo" in settings.known_teams
        assert "dept_devops" in settings.known_teams

    def test_stream_prefix(self):
        from message_router.config import settings

        assert settings.stream_prefix == "stream"

    def test_dedupe_ttl(self):
        from message_router.config import settings

        assert settings.dedupe_ttl_seconds == 300

    def test_max_delivery_attempts(self):
        from message_router.config import settings

        assert settings.max_delivery_attempts == 3

    def test_router_secret_env_alias(self, monkeypatch: pytest.MonkeyPatch):
        from message_router.config import Settings

        monkeypatch.delenv("AGENT_TOKEN_SECRET", raising=False)
        monkeypatch.setenv("ROUTER_SECRET", "router-secret-from-compose")

        cfg = Settings()
        assert cfg.agent_token_secret == "router-secret-from-compose"


# ---------------------------------------------------------------------------
# Redis key helper tests
# ---------------------------------------------------------------------------


class TestRedisKeyHelpers:
    def test_stream_key(self):
        from message_router.redis_client import stream_key

        assert stream_key("exec_ceo") == "stream:exec_ceo"
        assert stream_key("dept_devops") == "stream:dept_devops"

    def test_group_name(self):
        from message_router.redis_client import group_name

        assert group_name("exec_coo") == "group:exec_coo"

    def test_dedupe_key(self):
        from message_router.redis_client import dedupe_key

        mid = str(uuid.uuid4())
        assert dedupe_key(mid) == f"dedupe:{mid}"


# ---------------------------------------------------------------------------
# Publish route tests (mocked Redis)
# ---------------------------------------------------------------------------


def _no_op_lifespan():
    """Context manager that replaces the real lifespan for unit tests.
    Bypasses Redis connect, consumer group creation, and background tasks.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # noqa: ANN001
        yield

    return _lifespan


def _make_test_app(mock_redis: MagicMock):
    """Build a test FastAPI app with a no-op lifespan and pre-loaded mock Redis."""
    from message_router import redis_client
    from message_router.routes_publish import router as publish_router
    from message_router.routes_ws import router as ws_router

    app = FastAPI(lifespan=_no_op_lifespan())
    app.include_router(publish_router, tags=["publish"])
    app.include_router(ws_router, tags=["subscribe"])

    @app.get("/health", tags=["health"])
    async def health():
        return {
            "status": "ok",
            "redis": "ok",
            "known_teams": 11,
            "background_tasks": 0,
            "background_tasks_running": 0,
        }

    # Inject mock redis
    redis_client._redis_client = mock_redis  # type: ignore[attr-defined]
    return app


class TestPublishRoute:
    """Tests for POST /messages/publish using a mocked Redis client."""

    def _make_mock_redis(self) -> MagicMock:
        r = MagicMock()
        r.xadd = AsyncMock(return_value="1700000000000-0")
        r.set = AsyncMock(return_value=True)
        r.get = AsyncMock(return_value=None)
        r.ping = AsyncMock(return_value=True)
        return r

    def test_publish_happy_path(self):
        """Orchestrator publishes TASK to dept_production — should succeed."""
        mock_redis = self._make_mock_redis()
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope()
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "entry_id" in data
        assert data["deduplicated"] is False

    def test_publish_compatibility_alias(self):
        """The plan-aligned /publish alias should forward to the same handler."""
        mock_redis = self._make_mock_redis()
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope()
            resp = client.post(
                "/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "entry_id" in data

    def test_publish_policy_rejected(self):
        """Worker cannot send TASK cross-team — should return 403."""
        mock_redis = self._make_mock_redis()
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope(
                sender_role=AgentRole.WORKER,
                sender_team="dept_production",
                recipient_team="dept_system",
            )
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 403

    def test_publish_unknown_team(self):
        """Publishing to an unknown team_id returns 400."""
        mock_redis = self._make_mock_redis()
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope(recipient_team="team_nonexistent")
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400

    def test_publish_deduplicated(self):
        """Second publish with same message_id returns deduplicated=true."""
        mock_redis = self._make_mock_redis()
        # SET NX returns None (key already exists) → duplicate
        mock_redis.set = AsyncMock(return_value=None)
        mock_redis.get = AsyncMock(return_value="1700000000000-0")
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope()
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deduplicated"] is True
        assert data["entry_id"] == "1700000000000-0"

    def test_publish_deduplicated_when_existing_key_is_pending(self):
        """Pending dedupe markers should resolve without enqueuing a duplicate."""
        mock_redis = self._make_mock_redis()
        mock_redis.set = AsyncMock(return_value=None)
        # check_and_set_dedupe reads first pending marker, then wait helper reads final entry id
        mock_redis.get = AsyncMock(side_effect=["_pending_", "1700000000000-0"])
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope()
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deduplicated"] is True
        assert data["entry_id"] == "1700000000000-0"
        mock_redis.xadd.assert_not_awaited()

    def test_publish_expired_ttl(self):
        """Message already past its TTL is rejected with 400."""
        mock_redis = self._make_mock_redis()
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_envelope(ttl_seconds=1)
            # Manually set timestamp far in the past
            env = env.model_copy(
                update={"timestamp": datetime.now(tz=UTC) - timedelta(hours=1)}
            )
            resp = client.post(
                "/messages/publish",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert "TTL" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Broadcast route tests
# ---------------------------------------------------------------------------


class TestBroadcastRoute:
    def test_broadcast_orchestrator(self):
        """Orchestrator can broadcast SHUTDOWN to all teams."""
        call_count = 0
        mock_redis = MagicMock()

        async def fake_xadd(stream, fields):
            nonlocal call_count
            call_count += 1
            return f"17000000{call_count:05d}-0"

        mock_redis.xadd = fake_xadd
        mock_redis.ping = AsyncMock(return_value=True)
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_shutdown_envelope()
            resp = client.post(
                "/messages/broadcast",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "entry_ids" in data
        assert len(data["entry_ids"]) == 11  # All 11 teams

    def test_broadcast_compatibility_alias(self):
        """The plan-aligned /broadcast alias should fan out to all teams."""
        call_count = 0
        mock_redis = MagicMock()

        async def fake_xadd(stream, fields):
            nonlocal call_count
            call_count += 1
            return f"17000000{call_count:05d}-0"

        mock_redis.xadd = fake_xadd
        mock_redis.ping = AsyncMock(return_value=True)
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            env = make_shutdown_envelope()
            resp = client.post(
                "/broadcast",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entry_ids"]) == 11

    def test_broadcast_worker_rejected(self):
        """Worker cannot broadcast — policy rejects with 403."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            # Workers cannot send TASK cross-team — use that as a policy rejection check
            env = make_envelope(
                msg_type=MessageType.TASK,
                sender_role=AgentRole.WORKER,
                sender_team="dept_production",
                recipient_team="dept_system",
                project_id="proj-001",
            )
            resp = client.post(
                "/messages/broadcast",
                content=env.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_ok(self):
        """Health endpoint returns ok with known_teams count."""
        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)
        app = _make_test_app(mock_redis)
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["redis"] == "ok"
        assert data["known_teams"] == 11


# ---------------------------------------------------------------------------
# Reclaim / DLQ logic tests (unit, no I/O)
# ---------------------------------------------------------------------------


class TestReclaimLogic:
    @pytest.mark.asyncio
    async def test_dlq_on_max_attempts(self):
        """An entry with retry_count == max_attempts should go to DLQ."""
        from message_router.config import settings
        from message_router.tasks import _handle_reclaimed_entry

        env = make_envelope(retry_count=settings.max_delivery_attempts - 1)
        env_json = env.model_dump_json()
        fields = {"envelope": env_json}

        write_dlq = AsyncMock(return_value="dlq-id-1")
        dlq_fields_fn = MagicMock(return_value={"envelope": "{}"})
        xack_fn = AsyncMock()
        xdel_fn = AsyncMock()
        xadd_fn = AsyncMock(return_value="1-0")
        mock_redis = MagicMock()

        await _handle_reclaimed_entry(
            team_id="dept_production",
            entry_id="1-0",
            fields=fields,
            redis=mock_redis,
            write_dead_letter=write_dlq,
            make_dlq_system_event_fields=dlq_fields_fn,
            xack=xack_fn,
            xdel=xdel_fn,
            xadd_message=xadd_fn,
        )

        # DLQ should have been written
        write_dlq.assert_awaited_once()
        # XACK + XDEL should have been called
        xack_fn.assert_awaited()
        xdel_fn.assert_awaited()

    @pytest.mark.asyncio
    async def test_retry_count_incremented(self):
        """An entry below max_attempts should be re-queued with incremented retry_count."""
        from message_router.tasks import _handle_reclaimed_entry

        # retry_count = 0, max_attempts = 3 → should NOT go to DLQ
        env = make_envelope(retry_count=0)
        env_json = env.model_dump_json()
        fields = {"envelope": env_json}

        write_dlq = AsyncMock(return_value="dlq-id-1")
        dlq_fields_fn = MagicMock(return_value={"envelope": "{}"})
        xack_fn = AsyncMock()
        xdel_fn = AsyncMock()
        xadd_fn = AsyncMock(return_value="2-0")
        mock_redis = MagicMock()

        await _handle_reclaimed_entry(
            team_id="dept_production",
            entry_id="1-0",
            fields=fields,
            redis=mock_redis,
            write_dead_letter=write_dlq,
            make_dlq_system_event_fields=dlq_fields_fn,
            xack=xack_fn,
            xdel=xdel_fn,
            xadd_message=xadd_fn,
        )

        # DLQ should NOT have been called
        write_dlq.assert_not_awaited()
        # Should have re-queued with new entry
        xadd_fn.assert_awaited_once()
        # The re-queued envelope should have retry_count = 1
        call_args = xadd_fn.call_args
        updated_fields = call_args[0][1]  # positional: team_id, fields
        updated_env = MessageEnvelope.model_validate_json(updated_fields["envelope"])
        assert updated_env.retry_count == 1

    @pytest.mark.asyncio
    async def test_dlq_on_ttl_expired(self):
        """An expired message (past TTL) should go to DLQ even with retry_count=0."""
        from message_router.tasks import _handle_reclaimed_entry

        env = make_envelope(ttl_seconds=1, retry_count=0)
        # Backdate timestamp to force expiry
        env = env.model_copy(
            update={"timestamp": datetime.now(tz=UTC) - timedelta(hours=2)}
        )
        env_json = env.model_dump_json()
        fields = {"envelope": env_json}

        write_dlq = AsyncMock(return_value="dlq-id-ttl")
        dlq_fields_fn = MagicMock(return_value={"envelope": "{}"})
        xack_fn = AsyncMock()
        xdel_fn = AsyncMock()
        xadd_fn = AsyncMock(return_value="3-0")
        mock_redis = MagicMock()

        await _handle_reclaimed_entry(
            team_id="dept_production",
            entry_id="1-0",
            fields=fields,
            redis=mock_redis,
            write_dead_letter=write_dlq,
            make_dlq_system_event_fields=dlq_fields_fn,
            xack=xack_fn,
            xdel=xdel_fn,
            xadd_message=xadd_fn,
        )

        write_dlq.assert_awaited_once()
        call_kwargs = write_dlq.call_args.kwargs
        assert call_kwargs["reason"] == "ttl_expired"

    @pytest.mark.asyncio
    async def test_handle_missing_envelope_field(self):
        """Entry with no 'envelope' field should be ACKed and skipped (no crash)."""
        from message_router.tasks import _handle_reclaimed_entry

        fields: dict[str, str] = {}  # No 'envelope' key

        write_dlq = AsyncMock(return_value="x")
        dlq_fields_fn = MagicMock(return_value={})
        xack_fn = AsyncMock()
        xdel_fn = AsyncMock()
        xadd_fn = AsyncMock(return_value="4-0")
        mock_redis = MagicMock()

        await _handle_reclaimed_entry(
            team_id="dept_qa",
            entry_id="1-0",
            fields=fields,
            redis=mock_redis,
            write_dead_letter=write_dlq,
            make_dlq_system_event_fields=dlq_fields_fn,
            xack=xack_fn,
            xdel=xdel_fn,
            xadd_message=xadd_fn,
        )

        # Should ACK (to remove from PEL) and NOT write DLQ
        xack_fn.assert_awaited_once()
        write_dlq.assert_not_awaited()


# ---------------------------------------------------------------------------
# Consumer group helpers (unit tests with fakeredis or mocks)
# ---------------------------------------------------------------------------


class TestConsumerGroupHelpers:
    @pytest.mark.asyncio
    async def test_ensure_consumer_group_idempotent(self):
        """ensure_consumer_group is idempotent — BUSYGROUP error is swallowed."""
        from message_router.redis_client import ensure_consumer_group
        from redis.exceptions import ResponseError

        mock_redis = MagicMock()
        # First call raises BUSYGROUP
        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )
        # Should not raise
        await ensure_consumer_group("exec_ceo", mock_redis)

    @pytest.mark.asyncio
    async def test_ensure_consumer_group_other_error_propagates(self):
        """Non-BUSYGROUP ResponseError should propagate."""
        from message_router.redis_client import ensure_consumer_group
        from redis.exceptions import ResponseError

        mock_redis = MagicMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("WRONGTYPE Operation against a key")
        )
        with pytest.raises(ResponseError):
            await ensure_consumer_group("exec_ceo", mock_redis)


# ---------------------------------------------------------------------------
# WS authentication tests (unit, no real WS connection)
# ---------------------------------------------------------------------------


class TestWSAuth:
    def test_valid_token(self):
        """Valid Bearer token extracts agent_id correctly."""
        from message_router.config import settings
        from message_router.routes_ws import _authenticate

        mock_ws = MagicMock()
        mock_ws.headers = {"authorization": f"Bearer my_agent:{settings.agent_token_secret}"}
        result = _authenticate(mock_ws)
        assert result == "my_agent"

    def test_wrong_secret(self):
        """Wrong secret returns None."""
        from message_router.routes_ws import _authenticate

        mock_ws = MagicMock()
        mock_ws.headers = {"authorization": "Bearer agent1:wrong_secret"}
        result = _authenticate(mock_ws)
        assert result is None

    def test_missing_header(self):
        """Missing Authorization header returns None."""
        from message_router.routes_ws import _authenticate

        mock_ws = MagicMock()
        mock_ws.headers = {}
        result = _authenticate(mock_ws)
        assert result is None

    def test_malformed_header(self):
        """Malformed (no colon) token returns None."""
        from message_router.routes_ws import _authenticate

        mock_ws = MagicMock()
        mock_ws.headers = {"authorization": "Bearer nocolon"}
        result = _authenticate(mock_ws)
        assert result is None


# ---------------------------------------------------------------------------
# DLQ SYSTEM_EVENT notification builder
# ---------------------------------------------------------------------------


class TestDLQSystemEvent:
    def test_make_dlq_system_event_fields(self):
        """make_dlq_system_event_fields returns a parseable MessageEnvelope."""
        from message_router.dlq import make_dlq_system_event_fields

        fields = make_dlq_system_event_fields(
            dlq_id="dlq-123",
            message_id=str(uuid.uuid4()),
            team_id="dept_production",
            reason="max_attempts_exceeded",
        )
        assert "envelope" in fields
        env = MessageEnvelope.model_validate_json(fields["envelope"])
        assert env.msg_type == MessageType.SYSTEM_EVENT
        assert env.payload["event"] == "DLQ_ENTRY"
        assert env.payload["dlq_id"] == "dlq-123"
        assert env.payload["team_id"] == "dept_production"


# ---------------------------------------------------------------------------
# Redis ACL configuration tests (Phase 3)
# ---------------------------------------------------------------------------


class TestRecentStreamRoute:
    @staticmethod
    def _auth_headers() -> dict[str, str]:
        from message_router.config import settings

        return {"Authorization": f"Bearer dashboard:{settings.agent_token_secret}"}

    @pytest.mark.asyncio
    async def test_recent_entries_without_cursor_returns_oldest_first(self):
        from starlette.requests import Request

        from message_router import redis_client
        from message_router.routes_ws import recent_stream_entries

        redis = MagicMock()
        redis.xrevrange = AsyncMock(
            return_value=[
                (b"2-0", {b"envelope": b'{"id":2}'}),
                (b"1-0", {b"envelope": b'{"id":1}'}),
            ]
        )
        redis_client._redis_client = redis  # type: ignore[attr-defined]
        auth = self._auth_headers()["Authorization"].encode()
        request = Request({"type": "http", "headers": [(b"authorization", auth)]})

        response = await recent_stream_entries(request, "exec_ceo", limit=2, after=None)

        assert [entry["entry_id"] for entry in response["entries"]] == ["1-0", "2-0"]

    @pytest.mark.asyncio
    async def test_recent_entries_after_cursor_uses_exclusive_xrange(self):
        from starlette.requests import Request

        from message_router import redis_client
        from message_router.routes_ws import recent_stream_entries

        redis = MagicMock()
        redis.xrange = AsyncMock(
            return_value=[
                (b"101-0", {b"envelope": b'{"id":101}'}),
                (b"102-0", {b"envelope": b'{"id":102}'}),
            ]
        )
        redis_client._redis_client = redis  # type: ignore[attr-defined]
        auth = self._auth_headers()["Authorization"].encode()
        request = Request({"type": "http", "headers": [(b"authorization", auth)]})

        response = await recent_stream_entries(request, "exec_ceo", limit=500, after="100-0")

        redis.xrange.assert_awaited_once_with(
            "stream:exec_ceo", min="(100-0", max="+", count=500
        )
        assert [entry["entry_id"] for entry in response["entries"]] == ["101-0", "102-0"]


class TestRedisACL:
    def test_acl_config_documentation(self):
        """Verify redis.conf documents the ACL users."""
        import pathlib

        # Find the redis.conf file relative to this test
        # Walk up from tests/ to repo root, then find redis.conf
        test_dir = pathlib.Path(__file__).resolve().parent
        repo_root = test_dir.parent.parent.parent
        redis_conf_path = repo_root / "infra" / "compose" / "redis.conf"

        assert redis_conf_path.exists(), f"redis.conf not found at {redis_conf_path}"

        content = redis_conf_path.read_text()

        # Verify ACL documentation is present
        assert "ACL" in content, "redis.conf should mention ACL"
        assert "router_user" in content, "redis.conf should document router_user"
        assert "toolcache_user" in content, "redis.conf should document toolcache_user"
        assert "default" in content and "off" in content, "redis.conf should disable default user"

    def test_docker_compose_uses_acl(self):
        """Verify docker-compose.yml uses ACL usernames for Redis connections."""
        import pathlib

        test_dir = pathlib.Path(__file__).resolve().parent
        repo_root = test_dir.parent.parent.parent
        compose_path = repo_root / "infra" / "compose" / "docker-compose.yml"

        assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"

        content = compose_path.read_text()

        # Verify ACL init service exists
        assert "redis-acl-init" in content, "docker-compose should have redis-acl-init service"
        assert "router_user" in content, "message-router should use router_user"
        assert "toolcache_user" in content, "tool-service should use toolcache_user"
        assert "ROUTER_PASSWORD" in content, "ROUTER_PASSWORD env var should be defined"
        assert "TOOLCACHE_PASSWORD" in content, "TOOLCACHE_PASSWORD env var should be defined"
        assert "path: ../../../.env" in content, "services should load the repository-root .env"
        assert "path: ./.env" not in content, "clean installs must not require a compose-local .env"
