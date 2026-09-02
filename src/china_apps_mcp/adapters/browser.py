from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Playwright, async_playwright

_DEFAULT_ALLOWED_HOSTS = (
    "taobao.com",
    "tmall.com",
    "jd.com",
    "ctrip.com",
    "dianping.com",
    "zhihu.com",
    "douyin.com",
    "qq.com",
    "weixin.qq.com",
    "mp.weixin.qq.com",
    "bilibili.com",
)


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
    host = parsed.hostname or ""
    if not host or not _host_allowed(host):
        raise ValueError(
            f"Host {host or '<missing>'!r} is not allowed. "
            "Add it to BROWSER_ALLOWED_HOSTS if this is intentional."
        )
    return candidate


def _profile_dir() -> Path:
    configured = os.getenv("BROWSER_PROFILE_DIR", "profiles/chrome").strip() or "profiles/chrome"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _resolve_chrome_executable() -> Path | None:
    override = os.getenv("BROWSER_CHROME_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
        return path.resolve() if path.is_file() else None

    candidates: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.getenv(env_name, "").strip()
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    else:
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/google-chrome-stable"),
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
        )

    for path in candidates:
        if path.is_file():
            return path
    return None


class BrowserRuntime:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    @property
    def enabled(self) -> bool:
        return _env_flag("BROWSER_ENABLED", False)

    async def _ensure_started(self) -> BrowserContext:
        if not self.enabled:
            raise RuntimeError(
                "Browser runtime is disabled. Set BROWSER_ENABLED=1 in .env and restart the MCP gateway."
            )

        async with self._lock:
            if self._context is not None:
                try:
                    _ = self._context.pages
                    return self._context
                except Exception:
                    self._context = None

            chrome = _resolve_chrome_executable()
            if chrome is None:
                raise RuntimeError(
                    "Google Chrome was not found. Set BROWSER_CHROME_PATH to chrome.exe in .env."
                )

            profile = _profile_dir()
            profile.mkdir(parents=True, exist_ok=True)

            self._playwright = await async_playwright().start()
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    executable_path=str(chrome),
                    headless=False,
                    viewport=None,
                    accept_downloads=False,
                    args=["--start-maximized"],
                )
            except Exception:
                await self._playwright.stop()
                self._playwright = None
                raise

            return self._context

    async def status(self) -> dict[str, Any]:
        chrome = _resolve_chrome_executable()
        running = False
        page_count = 0
        if self._context is not None:
            try:
                page_count = len(self._context.pages)
                running = True
            except Exception:
                self._context = None

        return {
            "enabled": self.enabled,
            "running": running,
            "page_count": page_count,
            "chrome_path": str(chrome) if chrome else None,
            "profile_dir": str(_profile_dir()),
            "allowed_hosts": list(_allowed_hosts()),
            "persistent_profile": True,
        }

    async def start(self) -> dict[str, Any]:
        context = await self._ensure_started()
        if not context.pages:
            await context.new_page()
        return await self.status()

    async def _active_page(self):
        context = await self._ensure_started()
        pages = context.pages
        return pages[-1] if pages else await context.new_page()

    async def open(self, url: str) -> dict[str, Any]:
        page = await self._active_page()
        target = _validated_url(url)
        await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(500)
        return {
            "title": await page.title(),
            "url": page.url,
        }

    async def read_page(self, url: str = "", max_chars: int = 30_000) -> dict[str, Any]:
        if max_chars < 1_000 or max_chars > 60_000:
            raise ValueError("max_chars must be between 1000 and 60000")

        page = await self._active_page()
        if url.strip():
            target = _validated_url(url)
            await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(750)

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

        truncated = len(best_text) > max_chars
        text = best_text[:max_chars]
        return {
            "title": await page.title(),
            "url": page.url,
            "selector": best_selector or None,
            "text": text,
            "truncated": truncated,
            "text_chars": len(best_text),
        }

    async def list_pages(self) -> dict[str, Any]:
        context = await self._ensure_started()
        pages: list[dict[str, Any]] = []
        for index, page in enumerate(context.pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            pages.append({"index": index, "title": title, "url": page.url})
        return {"pages": pages}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            context = self._context
            playwright = self._playwright
            self._context = None
            self._playwright = None

        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

        return await self.status()


_runtime = BrowserRuntime()


def register_browser_tools(mcp: Any) -> None:
    @mcp.tool()
    async def browser_status() -> dict[str, Any]:
        """Report BrowserRuntime configuration and whether the persistent Chrome session is running."""
        return await _runtime.status()

    @mcp.tool()
    async def browser_start() -> dict[str, Any]:
        """Start or reuse the dedicated visible Chrome profile used for persistent account login state."""
        return await _runtime.start()

    @mcp.tool()
    async def browser_open(url: str) -> dict[str, Any]:
        """Open one allowed URL in the dedicated Chrome profile. Intended for read-only information gathering."""
        return await _runtime.open(url)

    @mcp.tool()
    async def browser_read_page(url: str = "", max_chars: int = 30_000) -> dict[str, Any]:
        """Read visible text from the active page or an allowed URL, including WeChat public-account articles."""
        return await _runtime.read_page(url=url, max_chars=max_chars)

    @mcp.tool()
    async def browser_list_pages() -> dict[str, Any]:
        """List tabs in the dedicated Chrome profile without exposing cookies or local storage."""
        return await _runtime.list_pages()

    @mcp.tool()
    async def browser_stop() -> dict[str, Any]:
        """Close the dedicated Chrome profile while preserving its on-disk login state for the next start."""
        return await _runtime.stop()
