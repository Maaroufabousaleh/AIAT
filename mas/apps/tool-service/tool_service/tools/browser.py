"""BROWSER group tools: browser_navigate, browser_click, browser_type, browser_screenshot, browser_evaluate."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
import uuid
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}
_BLOCKED_SUFFIXES = (".local", ".internal", ".corp", ".lan", ".home", ".intranet")
_PRIVATE_PREFIXES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Reject URLs pointing to internal/private addresses."""
    if not url:
        raise ValueError("url is required")

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if hostname.lower() in _BLOCKED_HOSTS:
        raise ValueError(f"URL blocked: {hostname} is not allowed")

    if any(hostname.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise ValueError(f"URL blocked: {hostname} matches a blocked internal domain suffix")

    try:
        ip = ipaddress.ip_address(hostname)
        for prefix in _PRIVATE_PREFIXES:
            if ip in prefix:
                raise ValueError(f"URL blocked: {hostname} is a private/internal address")
    except ValueError:
        pass

    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in resolved_ips:
            ip = ipaddress.ip_address(info[4][0])
            for prefix in _PRIVATE_PREFIXES:
                if ip in prefix:
                    raise ValueError(
                        f"URL blocked: {hostname} resolves to a private address ({info[4][0]})"
                    )
    except socket.gaierror:
        pass
    except ValueError:
        raise


class BrowserPage:
    """Tracks a page within a session."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.id = str(uuid.uuid4())


class BrowserSession:
    """Manages browser contexts and pages."""

    def __init__(self, playwright: Playwright) -> None:
        self._playwright = playwright
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[str, BrowserPage] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the browser (launch if not already running)."""
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._context = await self._browser.new_context()
            logger.info("Browser launched")

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
        self._in_use: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize Playwright."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.info("Playwright started")

    async def acquire(self, session_id: str | None = None) -> tuple[str, BrowserSession]:
        """Acquire a browser session. Returns (session_id, session).

        If session_id is provided and exists, returns that session.
        Otherwise returns the least-recently-used free session, or creates a new one.
        """
        async with self._lock:
            if session_id is not None and session_id in self._sessions:
                self._in_use.add(session_id)
                return session_id, self._sessions[session_id]

            free_sessions = [sid for sid in self._sessions if sid not in self._in_use]
            if free_sessions:
                chosen = free_sessions[0]
                self._in_use.add(chosen)
                return chosen, self._sessions[chosen]

            if len(self._sessions) < self._max_sessions:
                session = BrowserSession(self._playwright)
                await session.start()
                new_id = str(uuid.uuid4())
                self._sessions[new_id] = session
                self._in_use.add(new_id)
                return new_id, session

            raise RuntimeError("Browser pool exhausted")

    async def release(self, session_id: str) -> None:
        """Release a session back to the pool."""
        async with self._lock:
            self._in_use.discard(session_id)

    async def get_session(self, session_id: str) -> BrowserSession | None:
        """Get a session by ID without marking it as in-use."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove_session(self, session_id: str) -> None:
        """Remove and close a session from the pool."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            self._in_use.discard(session_id)
        if session:
            await session.close()

    async def close_all(self) -> None:
        """Close all sessions."""
        async with self._lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()
            self._in_use.clear()

            if self._playwright:
                await self._playwright.stop()
                self._playwright = None


_browser_pool: BrowserPool | None = None


async def get_browser_pool() -> BrowserPool:
    """Get or create the global browser pool."""
    global _browser_pool
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

        if session_id:
            session = await pool.get_session(session_id)
            if session is None:
                raise ValueError(f"Invalid session_id: {session_id}")
        else:
            session_id, session = await pool.acquire()

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
        session = await pool.get_session(session_id)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

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
        session = await pool.get_session(session_id)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

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
        session = await pool.get_session(session_id)
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
        session = await pool.get_session(session_id)
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
        session = await pool.get_session(session_id)
        if session is None:
            raise ValueError(f"Invalid session_id: {session_id}")

        if page_id:
            closed = await session.close_page(page_id)
            return {"session_id": session_id, "page_id": page_id, "closed": closed}

        await session.close()
        await pool.remove_session(session_id)
        return {"session_id": session_id, "closed": True}
