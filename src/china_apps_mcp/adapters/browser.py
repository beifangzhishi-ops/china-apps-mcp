from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

_DEFAULT_ALLOWED_HOSTS = (
    "taobao.com",
    "tmall.com",
    "jd.com",
    "ctrip.com",
    "dianping.com",
    "meituan.com",
    "zhihu.com",
    "douyin.com",
    "qq.com",
    "weixin.qq.com",
    "mp.weixin.qq.com",
    "bilibili.com",
)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _allowed_hosts() -> tuple[str, ...]:
    extra = tuple(
        item.strip().lower().lstrip(".")
        for item in os.getenv("BROWSER_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    )
    return tuple(dict.fromkeys((*_DEFAULT_ALLOWED_HOSTS, *extra)))


def _host_allowed(host: str, allowed_hosts: tuple[str, ...] | None = None) -> bool:
    host = host.strip().lower().rstrip(".")
    allowed_hosts = allowed_hosts or _allowed_hosts()
    if "*" in allowed_hosts:
        return True
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _validated_url(url: str) -> str:
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are allowed")
    host = (parsed.hostname or "").lower()
    if not host or not _host_allowed(host):
        raise ValueError(
            f"Host {host or '<missing>'!r} is not allowed. "
            "Add it to BROWSER_ALLOWED_HOSTS if this is intentional."
        )
    return candidate


def _validated_cdp_url(url: str) -> str:
    candidate = url.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme != "http":
        raise ValueError("BROWSER_CDP_URL must use http:// on loopback")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("BROWSER_CDP_URL must point to 127.0.0.1, localhost, or ::1")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("BROWSER_CDP_URL must be a loopback origin such as http://127.0.0.1:9222")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("BROWSER_CDP_URL has an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("BROWSER_CDP_URL must include a valid TCP port")
    return candidate


def _cdp_url() -> str:
    return _validated_cdp_url(os.getenv("BROWSER_CDP_URL", "http://127.0.0.1:9222"))


def _profile_dir() -> Path:
    configured = os.getenv("BROWSER_PROFILE_DIR", "profiles/chrome").strip() or "profiles/chrome"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


async def _wait_for_text_settle(page: Page, timeout_ms: int = 5_000) -> None:
    """Wait briefly for SPA text to settle without depending on networkidle."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    stable_count = 0
    previous = -1
    while asyncio.get_running_loop().time() < deadline:
        try:
            current = len(await page.locator("body").inner_text(timeout=1_000))
        except Exception:
            current = -1
        if current >= 0 and current == previous:
            stable_count += 1
            if stable_count >= 3:
                return
        else:
            stable_count = 0
            previous = current
        await page.wait_for_timeout(300)


class BrowserRuntime:
    """Attach-only runtime for a user-started Chrome with remote debugging enabled."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def enabled(self) -> bool:
        return _env_flag("BROWSER_ENABLED", False)

    def _attached(self) -> bool:
        return self._browser is not None and self._browser.is_connected() and self._context is not None

    async def _probe_cdp(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(f"{_cdp_url()}/json/version")
                return response.status_code == 200
        except Exception:
            return False

    async def _ensure_attached(self) -> BrowserContext:
        if not self.enabled:
            raise RuntimeError(
                "Browser runtime is disabled. Set BROWSER_ENABLED=1 in .env and restart the MCP gateway."
            )

        async with self._lock:
            if self._attached():
                assert self._context is not None
                return self._context

            await self._detach_locked()
            endpoint = _cdp_url()
            if not await self._probe_cdp():
                raise RuntimeError(
                    f"No debuggable Chrome is available at {endpoint}. "
                    "Start the dedicated Chrome first (for example with 启动浏览器.cmd), then retry."
                )

            self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
                contexts = self._browser.contexts
                if not contexts:
                    raise RuntimeError("Chrome connected over CDP but exposed no browser context")
                self._context = contexts[0]
            except Exception:
                await self._detach_locked()
                raise

            return self._context

    async def _detach_locked(self) -> None:
        # Do not call Browser.close(): Chrome belongs to the user, not to this MCP process.
        playwright = self._playwright
        self._context = None
        self._browser = None
        self._playwright = None
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def status(self) -> dict[str, Any]:
        attached = self._attached()
        page_count = 0
        if attached and self._context is not None:
            try:
                page_count = len(self._context.pages)
            except Exception:
                attached = False

        return {
            "enabled": self.enabled,
            "mode": "attach",
            "attached": attached,
            "chrome_debug_endpoint_ready": await self._probe_cdp() if self.enabled else False,
            "page_count": page_count,
            "cdp_url": _cdp_url(),
            "profile_dir": str(_profile_dir()),
            "allowed_hosts": list(_allowed_hosts()),
            "persistent_profile": True,
            "chrome_owned_by_mcp": False,
        }

    async def start(self) -> dict[str, Any]:
        """Attach to the already-running dedicated Chrome; never launch Chrome itself."""
        await self._ensure_attached()
        return await self.status()

    async def _last_allowed_page(self) -> Page:
        context = await self._ensure_attached()
        for page in reversed(context.pages):
            try:
                _validated_url(page.url)
                return page
            except ValueError:
                continue
        raise RuntimeError("No currently open tab is on an allowed browser host")

    async def _navigate_checked(self, page: Page, url: str) -> None:
        target = _validated_url(url)
        await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        await _wait_for_text_settle(page)
        _validated_url(page.url)

    async def open(self, url: str) -> dict[str, Any]:
        context = await self._ensure_attached()
        page = await context.new_page()
        try:
            await self._navigate_checked(page, url)
        except Exception:
            await page.close()
            raise
        return {
            "title": await page.title(),
            "url": page.url,
            "page_index": context.pages.index(page),
        }

    async def _extract_page(self, page: Page, max_chars: int) -> dict[str, Any]:
        _validated_url(page.url)
        best_text = ""
        best_selector = ""
        for selector in ("#js_content", "article", "main", "[role='main']", "body"):
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = (await locator.inner_text(timeout=5_000)).strip()
                if len(text) > len(best_text):
                    best_text = text
                    best_selector = selector
                if selector == "#js_content" and text:
                    break
            except Exception:
                continue

        links: list[dict[str, str]] = []
        try:
            raw_links = await page.locator("a[href]").evaluate_all(
                """els => els.slice(0, 150).map(a => ({
                    text: (a.innerText || a.textContent || '').trim().slice(0, 200),
                    href: a.href || ''
                }))"""
            )
            for item in raw_links:
                href = str(item.get("href", ""))
                text = str(item.get("text", ""))
                try:
                    _validated_url(href)
                except ValueError:
                    continue
                links.append({"text": text, "href": href})
        except Exception:
            pass

        truncated = len(best_text) > max_chars
        return {
            "title": await page.title(),
            "url": page.url,
            "selector": best_selector or None,
            "text": best_text[:max_chars],
            "truncated": truncated,
            "text_chars": len(best_text),
            "links": links,
        }

    async def read_page(self, url: str = "", max_chars: int = 30_000) -> dict[str, Any]:
        if max_chars < 1_000 or max_chars > 60_000:
            raise ValueError("max_chars must be between 1000 and 60000")

        if not url.strip():
            page = await self._last_allowed_page()
            await _wait_for_text_settle(page, timeout_ms=2_000)
            return await self._extract_page(page, max_chars)

        # URL reads use a dedicated temporary tab so concurrent GPT calls cannot
        # navigate or overwrite a human login tab (or each other's tab).
        context = await self._ensure_attached()
        page = await context.new_page()
        try:
            await self._navigate_checked(page, url)
            return await self._extract_page(page, max_chars)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def list_pages(self) -> dict[str, Any]:
        context = await self._ensure_attached()
        pages: list[dict[str, Any]] = []
        for index, page in enumerate(context.pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            allowed = False
            try:
                _validated_url(page.url)
                allowed = True
            except ValueError:
                pass
            pages.append({"index": index, "title": title, "url": page.url, "allowed": allowed})
        return {"pages": pages}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            await self._detach_locked()
        return await self.status()


_runtime = BrowserRuntime()


def register_browser_tools(mcp: Any) -> None:
    @mcp.tool()
    async def browser_status() -> dict[str, Any]:
        """Report whether the user-started Chrome debug endpoint is ready and whether MCP is attached."""
        return await _runtime.status()

    @mcp.tool()
    async def browser_start() -> dict[str, Any]:
        """Attach to the dedicated Chrome already started by the user; this tool never launches Chrome."""
        return await _runtime.start()

    @mcp.tool()
    async def browser_open(url: str) -> dict[str, Any]:
        """Open one allowed URL in a new tab of the attached Chrome. Intended for read-only information gathering."""
        return await _runtime.open(url)

    @mcp.tool()
    async def browser_read_page(url: str = "", max_chars: int = 30_000) -> dict[str, Any]:
        """Read visible text and links from an allowed URL or the latest already-open allowed tab."""
        return await _runtime.read_page(url=url, max_chars=max_chars)

    @mcp.tool()
    async def browser_list_pages() -> dict[str, Any]:
        """List Chrome tabs and mark whether each tab is on an MCP-allowed host."""
        return await _runtime.list_pages()

    @mcp.tool()
    async def browser_stop() -> dict[str, Any]:
        """Detach MCP from Chrome without closing Chrome or deleting its persistent login state."""
        return await _runtime.stop()
