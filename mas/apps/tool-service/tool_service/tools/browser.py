"""BROWSER group tools: browser_navigate, browser_click, browser_type, browser_screenshot, browser_evaluate."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".corp", ".lan", ".home", ".intranet")


def _chromium_args() -> list[str]:
    """Keep Chromium's sandbox enabled except for explicit local development."""
    args = ["--disable-dev-shm-usage"]
    disable_sandbox = os.getenv(
        "AIAT_BROWSER_DISABLE_CHROMIUM_SANDBOX", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    production = os.getenv("MAS_ENVIRONMENT", "development").strip().lower() in {
        "production", "prod", "staging",
    }
    if disable_sandbox and production:
        raise PermissionError("Chromium sandbox cannot be disabled in production")
    if disable_sandbox:
        args.append("--no-sandbox")
    return args


def _validate_url(url: str) -> None:
    """Reject non-web URLs and every non-public literal/resolved address."""
    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL blocked: only HTTP and HTTPS are allowed")
    if not hostname:
        raise ValueError("URL blocked: hostname is required")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL blocked: embedded credentials are not allowed")

    if hostname in _BLOCKED_HOSTS:
        raise ValueError(f"URL blocked: {hostname} is not allowed")

    if any(hostname.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise ValueError(f"URL blocked: {hostname} matches a blocked internal domain suffix")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise ValueError(f"URL blocked: {hostname} is not a public address")

    try:
        resolved_ips = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        for info in resolved_ips:
            resolved = ipaddress.ip_address(info[4][0])
            if not resolved.is_global:
                raise ValueError(
                    f"URL blocked: {hostname} resolves to a non-public address"
                )
    except socket.gaierror:
        # The browser cannot navigate a name that does not resolve now. Treat
        # resolution failure as denial rather than allowing a later rebinding.
        raise ValueError(f"URL blocked: {hostname} could not be resolved") from None
    except ValueError:
        raise


class BrowserPage:
    """Tracks a page within a session."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.id = str(uuid.uuid4())


class BrowserSession:
    """Manages browser contexts and pages."""

    def __init__(self, playwright: Playwright, profile_dir: Path | None = None) -> None:
        self._playwright = playwright
        self._profile_dir = profile_dir
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[str, BrowserPage] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the browser (launch if not already running)."""
        if self._context is None:
            args = _chromium_args()
            if self._profile_dir is not None:
                self._profile_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._profile_dir.mkdir(mode=0o700, exist_ok=True)
                self._profile_dir.parent.chmod(0o700)
                self._profile_dir.chmod(0o700)
                downloads = self._profile_dir / "downloads"
                downloads.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(self._profile_dir), headless=True, args=args,
                    accept_downloads=True, downloads_path=str(downloads),
                )
            else:
                self._browser = await self._playwright.chromium.launch(headless=True, args=args)
                self._context = await self._browser.new_context()
            # Validate every subresource and redirect at dispatch time, not
            # merely the initial worker-supplied URL. This closes the common
            # public-host-to-private-address DNS rebinding/redirect path.
            await self._context.route("**/*", self._guard_request)
            logger.info("Browser launched")

    @staticmethod
    async def _guard_request(route: Route) -> None:
        try:
            _validate_url(route.request.url)
        except ValueError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def get_context(self) -> BrowserContext | None:
        """Get the session's browser context."""
        return self._context

    async def create_page(self, url: str | None = None) -> BrowserPage:
        """Create a new page and optionally navigate to a URL."""
        if self._context is None:
            raise RuntimeError("Browser not started")

        page = await self._context.new_page()
        browser_page = BrowserPage(page)
        self._pages[browser_page.id] = browser_page

        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        return browser_page

    def get_page(self, page_id: str) -> Page | None:
        """Get a page by ID."""
        bp = self._pages.get(page_id)
        return bp.page if bp else None

    async def close_page(self, page_id: str) -> bool:
        """Close a specific page."""
        async with self._lock:
            bp = self._pages.pop(page_id, None)
            if bp:
                try:
                    await bp.page.close()
                except Exception:
                    pass
                return True
            return False

    async def close(self) -> None:
        """Close all pages, context, and browser."""
        async with self._lock:
            for bp in self._pages.values():
                try:
                    await bp.page.close()
                except Exception:
                    pass
            self._pages.clear()

            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None

            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
                logger.info("Browser closed")


class BrowserPool:
    """Pool manager for browser sessions."""

    def __init__(self, max_sessions: int = 3) -> None:
        self._max_sessions = max_sessions
        self._playwright: Playwright | None = None
        self._sessions: dict[str, BrowserSession] = {}
        self._session_owner: dict[str, tuple[str, str]] = {}
        self._identity_sessions: dict[str, str | None] = {}
        self._owner_session: dict[tuple[str, str, bool], str] = {}
        self._in_use: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize Playwright."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.info("Playwright started")

    def _profile_dir(self, owner: tuple[str, str]) -> Path:
        root = Path(os.getenv("AIAT_BROWSER_PROFILE_ROOT", "/tmp/aiat-browser-profiles")).resolve()
        worker_namespace = "worker-" + uuid.uuid5(
            uuid.NAMESPACE_URL, owner[0]
        ).hex
        service_profile = "service-" + uuid.uuid5(
            uuid.NAMESPACE_URL, owner[1]
        ).hex
        profile = (root / worker_namespace / service_profile).resolve()
        if root not in profile.parents:
            raise ValueError("browser profile escaped configured root")
        return profile

    async def acquire(self, owner: tuple[str, str], session_id: str | None = None, *, persistent: bool = False, identity_session_id: str | None = None) -> tuple[str, BrowserSession]:
        """Acquire a browser session. Returns (session_id, session).

        If session_id is provided and exists, returns that session.
        Otherwise returns the least-recently-used free session, or creates a new one.
        """
        async with self._lock:
            if not owner[0] or not owner[1]:
                raise ValueError("a worker and external service are required for a new browser context")
            if persistent and not identity_session_id:
                raise PermissionError("persistent browser context requires a governed identity session")
            if session_id is not None and session_id in self._sessions:
                if self._session_owner.get(session_id) != owner:
                    raise PermissionError("browser session belongs to another worker or service")
                if self._identity_sessions.get(session_id) != identity_session_id:
                    raise PermissionError("browser identity session binding does not match")
                self._in_use.add(session_id)
                return session_id, self._sessions[session_id]

            owner_key = (*owner, persistent)
            existing = self._owner_session.get(owner_key)
            if existing and existing in self._sessions:
                if self._identity_sessions.get(existing) != identity_session_id:
                    raise PermissionError("browser identity session binding does not match")
                self._in_use.add(existing)
                return existing, self._sessions[existing]

            if len(self._sessions) < self._max_sessions:
                # Generic research sessions are memory-only. A disk profile is
                # permitted only after identity-service validates an opaque,
                # worker-owned external-account session.
                session = BrowserSession(self._playwright, self._profile_dir(owner) if persistent else None)
                await session.start()
                new_id = str(uuid.uuid4())
                self._sessions[new_id] = session
                self._session_owner[new_id] = owner
                self._identity_sessions[new_id] = identity_session_id
                self._owner_session[owner_key] = new_id
                self._in_use.add(new_id)
                return new_id, session

            raise RuntimeError("Browser pool exhausted")

    async def release(self, session_id: str) -> None:
        """Release a session back to the pool."""
        async with self._lock:
            self._in_use.discard(session_id)

    async def get_session(self, session_id: str, owner: tuple[str, str] | None = None) -> BrowserSession | None:
        """Get a session by ID without marking it as in-use."""
        async with self._lock:
            actual_owner = self._session_owner.get(session_id)
            if owner is not None and (
                actual_owner is None
                or actual_owner[0] != owner[0]
                or (owner[1] and actual_owner[1] != owner[1])
            ):
                raise PermissionError("browser session belongs to another worker or service")
            return self._sessions.get(session_id)

    async def get_identity_session_id(self, session_id: str) -> str | None:
        async with self._lock:
            return self._identity_sessions.get(session_id)

    async def remove_session(self, session_id: str) -> None:
        """Remove and close a session from the pool."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            owner = self._session_owner.pop(session_id, None)
            self._identity_sessions.pop(session_id, None)
            if owner:
                for owner_key, mapped_session in list(self._owner_session.items()):
                    if mapped_session == session_id:
                        self._owner_session.pop(owner_key, None)
            self._in_use.discard(session_id)
        if session:
            await session.close()

    async def remove_worker(self, worker_id: str) -> int:
        """Close every live local context for a suspended/retired worker."""
        async with self._lock:
            session_ids = [
                session_id for session_id, owner in self._session_owner.items()
                if owner[0] == worker_id
            ]
        for session_id in session_ids:
            await self.remove_session(session_id)
        return len(session_ids)

    async def close_all(self) -> None:
        """Close all sessions."""
        async with self._lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()
            self._session_owner.clear()
            self._identity_sessions.clear()
            self._owner_session.clear()
            self._in_use.clear()

            if self._playwright:
                await self._playwright.stop()
                self._playwright = None


_browser_pool: BrowserPool | None = None


async def get_browser_pool() -> BrowserPool:
    """Get or create the global browser pool."""
    global _browser_pool
    if os.getenv("MAS_ENVIRONMENT", "development").lower() in {"production", "prod", "staging"} and os.getenv("AIAT_BROWSER_RUNTIME_LOCATION", "") != "operator_laptop":
        raise PermissionError("persistent browser automation is restricted to the operator laptop")
    if _browser_pool is None:
        _browser_pool = BrowserPool(max_sessions=3)
        await _browser_pool.start()
    return _browser_pool


async def close_browser_pool() -> None:
    """Close the global browser pool."""
    global _browser_pool
    if _browser_pool:
        await _browser_pool.close_all()
        _browser_pool = None


async def revoke_worker_browser_sessions(worker_id: str) -> int:
    """Close live contexts without starting Playwright solely for revocation."""
    if _browser_pool is None:
        return 0
    return await _browser_pool.remove_worker(worker_id)


def _browser_owner(kwargs: dict[str, Any], *, url: str | None = None) -> tuple[str, str]:
    """Derive an isolated worker/service ownership key from trusted context."""
    context = kwargs.get("_aiat_context")
    if not isinstance(context, dict):
        if os.getenv("MAS_ENVIRONMENT", "development").lower() in {"production", "prod", "staging"}:
            raise PermissionError("trusted browser caller context is required")
        return ("development-anonymous", str(kwargs.get("external_service") or "generic"))
    caller = str(context.get("caller_id") or "")
    role = str(context.get("caller_role") or "").lower()
    worker_id = str(kwargs.get("worker_id") or caller)
    if not caller:
        raise PermissionError("browser caller identity is required")
    if role in {"worker", "sub_agent"} and worker_id != caller:
        raise PermissionError("cross-worker browser access is denied")
    service = str(kwargs.get("external_service") or "").strip().lower()
    if not service and url:
        service = (urlparse(url).hostname or "").lower()
    if any(char.isspace() for char in service):
        raise ValueError("external_service must not contain whitespace")
    # Follow-up calls bind only the worker id and let the opaque session id
    # select its already-established service context. New sessions require a
    # service/hostname and are checked in BrowserPool.acquire.
    return worker_id, service


async def _authorize_identity_session(
    kwargs: dict[str, Any], owner: tuple[str, str], identity_session_id: str
) -> None:
    """Validate an opaque identity-session handle before using a disk profile."""
    context = kwargs.get("_aiat_context")
    if not isinstance(context, dict) or not context.get("caller_id"):
        raise PermissionError("trusted browser caller context is required for a persistent profile")
    from ..identity_client import IdentityGatewayClient

    actor = {
        "actor_id": context["caller_id"],
        "project_id": context.get("project_id"),
        "worker_run_id": context.get("worker_run_id"),
        "purpose": "governed external-account browser session",
    }
    await IdentityGatewayClient().use_browser_session(
        worker_id=owner[0], actor=actor, session_id=str(identity_session_id)
    )


async def _persistent_profile_authorized(
    kwargs: dict[str, Any], owner: tuple[str, str]
) -> str | None:
    """Return the validated identity-session binding for a new context."""
    identity_session_id = kwargs.get("identity_session_id")
    if not identity_session_id:
        return None
    value = str(identity_session_id)
    await _authorize_identity_session(kwargs, owner, value)
    return value


async def _get_authorized_session(
    pool: BrowserPool,
    session_id: str,
    owner: tuple[str, str],
    kwargs: dict[str, Any],
) -> BrowserSession | None:
    """Revalidate persistent authorization on every browser operation."""
    session = await pool.get_session(session_id, owner)
    if session is None:
        return None
    identity_session_id = await pool.get_identity_session_id(session_id)
    if identity_session_id:
        try:
            await _authorize_identity_session(kwargs, owner, identity_session_id)
        except Exception:
            # Revocation closes the live context immediately; cached cookies
            # cannot remain usable through a previously returned local handle.
            await pool.remove_session(session_id)
            raise
    return session


async def _require_governed_browser_write(
    pool: BrowserPool, session_id: str
) -> None:
    """Block production form/click automation outside an identity session."""
    if os.getenv("MAS_ENVIRONMENT", "development").lower() not in {
        "production", "prod", "staging",
    }:
        return
    if not await pool.get_identity_session_id(session_id):
        raise PermissionError(
            "production browser writes require a governed external-account session"
        )


class BrowserNavigateTool(BaseTool):
    name = "browser_navigate"
    group = ToolGroup.KPI_UTILITY
    description = "Navigate to a URL in a browser session."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url", "")
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")

        _validate_url(url)

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs, url=url)

        if session_id:
            session = await _get_authorized_session(pool, session_id, owner, kwargs)
            if session is None:
                raise ValueError(f"Invalid session_id: {session_id}")
        else:
            identity_session_id = await _persistent_profile_authorized(kwargs, owner)
            session_id, session = await pool.acquire(
                owner, persistent=identity_session_id is not None,
                identity_session_id=identity_session_id,
            )

        if page_id:
            page = session.get_page(page_id)
            if page:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                title = await page.title()
                final_url = page.url
                return {
                    "session_id": session_id,
                    "page_id": page_id,
                    "url": final_url,
                    "title": title,
                    "status": response.status if response else 200,
                    "success": True,
                }

        bp = await session.create_page(url)
        title = await bp.page.title()
        final_url = bp.page.url

        return {
            "session_id": session_id,
            "page_id": bp.id,
            "url": final_url,
            "title": title,
            "success": True,
        }


class BrowserClickTool(BaseTool):
    name = "browser_click"
    group = ToolGroup.KPI_UTILITY
    description = "Click an element on the page by selector."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")
        selector = kwargs.get("selector", "")

        if not session_id:
            raise ValueError("session_id is required")
        if not page_id:
            raise ValueError("page_id is required (call browser_navigate first)")
        if not selector:
            raise ValueError("selector is required")

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs)
        session = await _get_authorized_session(pool, session_id, owner, kwargs)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")
        await _require_governed_browser_write(pool, session_id)

        page = session.get_page(page_id)
        if not page:
            raise ValueError(f"Invalid page_id: {page_id}")

        await page.click(selector, timeout=10000)
        return {"session_id": session_id, "page_id": page_id, "selector": selector, "success": True}


class BrowserTypeTool(BaseTool):
    name = "browser_type"
    group = ToolGroup.KPI_UTILITY
    description = "Type text into an input element."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")
        selector = kwargs.get("selector", "")
        text = kwargs.get("text", "")

        if not session_id:
            raise ValueError("session_id is required")
        if not page_id:
            raise ValueError("page_id is required (call browser_navigate first)")
        if not selector:
            raise ValueError("selector is required")

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs)
        session = await _get_authorized_session(pool, session_id, owner, kwargs)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")
        await _require_governed_browser_write(pool, session_id)

        page = session.get_page(page_id)
        if not page:
            raise ValueError(f"Invalid page_id: {page_id}")

        await page.fill(selector, text)
        return {"session_id": session_id, "page_id": page_id, "selector": selector, "success": True}


class BrowserScreenshotTool(BaseTool):
    name = "browser_screenshot"
    group = ToolGroup.KPI_UTILITY
    description = "Take a screenshot of the current page."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")
        full_page = kwargs.get("full_page", True)

        if not session_id:
            raise ValueError("session_id is required")
        if not page_id:
            raise ValueError("page_id is required (call browser_navigate first)")

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs)
        session = await _get_authorized_session(pool, session_id, owner, kwargs)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

        page = session.get_page(page_id)
        if not page:
            raise ValueError(f"Invalid page_id: {page_id}")

        screenshot_bytes = await page.screenshot(full_page=full_page)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        return {
            "session_id": session_id,
            "page_id": page_id,
            "screenshot": screenshot_b64,
            "format": "png",
            "success": True,
        }


class BrowserEvaluateTool(BaseTool):
    name = "browser_evaluate"
    group = ToolGroup.KPI_UTILITY
    description = (
        "Execute JavaScript in the browser context. Restricted to orchestrator and executive roles."
    )
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    _DANGEROUS_PATTERNS = [
        "window.location",
        "document.cookie",
        "document.write",
        "eval(",
        "Function(",
        "fetch(",
        "XMLHttpRequest",
        "navigator.credentials",
        "localStorage",
        "sessionStorage",
    ]

    async def execute(self, **kwargs: Any) -> Any:
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")
        script = kwargs.get("script", "")

        if not session_id:
            raise ValueError("session_id is required")
        if not page_id:
            raise ValueError("page_id is required (call browser_navigate first)")
        if not script:
            raise ValueError("script is required")

        self._validate_script(script)

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs)
        session = await _get_authorized_session(pool, session_id, owner, kwargs)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

        page = session.get_page(page_id)
        if not page:
            raise ValueError(f"Invalid page_id: {page_id}")

        result = await page.evaluate(script)
        return {"session_id": session_id, "page_id": page_id, "result": result, "success": True}

    def _validate_script(self, script: str) -> None:
        script_lower = script.lower()
        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.lower() in script_lower:
                raise ValueError(
                    f"Script contains restricted pattern: '{pattern}'. "
                    f"Browser evaluate is limited to read-only DOM queries and computations."
                )


class BrowserCloseTool(BaseTool):
    name = "browser_close"
    group = ToolGroup.KPI_UTILITY
    description = "Close a browser page or entire session."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        session_id = kwargs.get("session_id")
        page_id = kwargs.get("page_id")

        if not session_id:
            raise ValueError("session_id is required")

        pool = await get_browser_pool()
        owner = _browser_owner(kwargs)
        session = await _get_authorized_session(pool, session_id, owner, kwargs)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

        if page_id:
            closed = await session.close_page(page_id)
            return {"session_id": session_id, "page_id": page_id, "closed": closed}

        await session.close()
        await pool.remove_session(session_id)
        return {"session_id": session_id, "closed": True}
