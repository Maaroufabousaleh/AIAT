from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from tool_service.tools.browser import BrowserPool, BrowserSession


@pytest.mark.anyio
async def test_browser_pool_never_reuses_a_worker_service_context(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Persistent profile selection is worker/service-scoped, not caller supplied."""
    monkeypatch.setenv("AIAT_BROWSER_PROFILE_ROOT", str(tmp_path))
    monkeypatch.setattr(BrowserSession, "start", AsyncMock())

    pool = BrowserPool(max_sessions=3)
    pool._playwright = object()  # BrowserSession.start is mocked: no browser process is launched.

    worker_a = ("worker-a", "github")
    worker_b = ("worker-b", "github")
    first_id, _ = await pool.acquire(
        worker_a, persistent=True, identity_session_id="identity-session-a"
    )
    second_id, _ = await pool.acquire(
        worker_b, persistent=True, identity_session_id="identity-session-b"
    )

    assert first_id != second_id
    assert pool._profile_dir(worker_a) != pool._profile_dir(worker_b)
    assert pool._profile_dir(worker_a).parent.parent == tmp_path.resolve()
    with pytest.raises(PermissionError, match="another worker"):
        await pool.get_session(first_id, worker_b)
    with pytest.raises(PermissionError, match="another worker"):
        await pool.acquire(
            worker_b, session_id=first_id, persistent=True,
            identity_session_id="identity-session-b",
        )

    assert await pool.get_identity_session_id(first_id) == "identity-session-a"
    assert await pool.remove_worker("worker-a") == 1
    assert await pool.get_session(first_id) is None

    await pool.remove_session(second_id)
