from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from websockets.asyncio.server import Server, ServerConnection, serve

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

_BRIDGE_HOST = "127.0.0.1"
_BRIDGE_PORT = 8766
_MAX_BRIDGE_MESSAGE = 2 * 1024 * 1024


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


def _safe_page_result(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
    url = _validated_url(str(result.get("url", "")))
    title = str(result.get("title", ""))[:500]
    text = str(result.get("text", ""))
    links_out: list[dict[str, str]] = []
    raw_links = result.get("links", [])
    if isinstance(raw_links, list):
        for item in raw_links[:150]:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href", ""))
            try:
                href = _validated_url(href)
            except ValueError:
                continue
            links_out.append(
                {
                    "text": str(item.get("text", ""))[:200],
                    "href": href,
                }
            )

    return {
        "title": title,
        "url": url,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "text_chars": len(text),
        "links": links_out,
    }


class BrowserBridge:
    """Local-only WebSocket bridge to the Edge extension."""

    def __init__(self) -> None:
        self._server: Server | None = None
        self._connection: ServerConnection | None = None
        self._connection_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._metadata: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return _env_flag("BROWSER_ENABLED", False)

    @property
    def connected(self) -> bool:
        return self._connection is not None

    async def start(self) -> None:
        if not self.enabled or self._server is not None:
            return
        self._server = await serve(
            self._handle_connection,
            _BRIDGE_HOST,
            _BRIDGE_PORT,
            max_size=_MAX_BRIDGE_MESSAGE,
            ping_interval=20,
            ping_timeout=20,
        )

    async def stop(self) -> None:
        async with self._connection_lock:
            connection = self._connection
            self._connection = None
            self._metadata = {}
            self._fail_pending(RuntimeError("Browser bridge stopped"))

        if connection is not None:
            try:
                await connection.close(code=1001, reason="MCP gateway stopping")
            except Exception:
                pass

        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        origin = websocket.request.headers.get("Origin")
        if origin and not origin.startswith(
            ("chrome-extension://", "extension://", "ms-browser-extension://")
        ):
            await websocket.close(code=1008, reason="Extension origin required")
            return

        async with self._connection_lock:
            previous = self._connection
            self._connection = websocket
            self._metadata = {}
            self._fail_pending(RuntimeError("Browser extension reconnected"))

        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1000, reason="Replaced by newer extension connection")
            except Exception:
                pass

        try:
            async for raw in websocket:
                if not isinstance(raw, str):
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue

                message_type = message.get("type")
                if message_type == "hello":
                    self._metadata = {
                        "extension_version": str(message.get("version", ""))[:50],
                        "browser": str(message.get("browser", ""))[:50],
                    }
                    continue
                if message_type != "response":
                    continue

                request_id = str(message.get("id", ""))
                future = self._pending.pop(request_id, None)
                if future is None or future.done():
                    continue

                if message.get("ok") is True:
                    result = message.get("result")
                    future.set_result(result if isinstance(result, dict) else {"value": result})
                else:
                    error = str(message.get("error", "Browser extension request failed"))
                    future.set_exception(RuntimeError(error))
        finally:
            async with self._connection_lock:
                if self._connection is websocket:
                    self._connection = None
                    self._metadata = {}
                    self._fail_pending(RuntimeError("Browser extension disconnected"))

    async def request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(
                "Browser bridge is disabled. Set BROWSER_ENABLED=1 in .env and restart the MCP gateway."
            )
        connection = self._connection
        if connection is None:
            raise RuntimeError(
                "Edge extension is not connected. Open Edge normally and ensure "
                "the China Apps Browser Bridge extension is enabled."
            )

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        message = {
            "type": "request",
            "id": request_id,
            "action": action,
            "payload": payload or {},
        }
        try:
            async with self._send_lock:
                await connection.send(json.dumps(message, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=timeout)
        except Exception:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    async def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": "edge_extension",
            "bridge_host": _BRIDGE_HOST,
            "bridge_port": _BRIDGE_PORT,
            "extension_connected": self.connected,
            "extension": dict(self._metadata),
            "allowed_hosts": list(_allowed_hosts()),
        }


_bridge = BrowserBridge()


async def start_browser_bridge() -> None:
    await _bridge.start()


async def stop_browser_bridge() -> None:
    await _bridge.stop()


def register_browser_tools(mcp: Any) -> None:
    @mcp.tool()
    async def browser_status() -> dict[str, Any]:
        """Report Edge extension bridge status and the browser host allowlist."""
        return await _bridge.status()

    @mcp.tool()
    async def browser_start() -> dict[str, Any]:
        """Compatibility check: verify the normal Edge extension is connected to the local bridge."""
        status = await _bridge.status()
        if not status["enabled"]:
            raise RuntimeError("Browser bridge is disabled. Set BROWSER_ENABLED=1 and restart the gateway.")
        if not status["extension_connected"]:
            raise RuntimeError(
                "Edge extension is not connected. Open Edge normally and enable the China Apps Browser Bridge extension."
            )
        return status

    @mcp.tool()
    async def browser_open(url: str) -> dict[str, Any]:
        """Open one allowed URL in a normal Edge tab through the local extension."""
        target = _validated_url(url)
        result = await _bridge.request("open_tab", {"url": target, "active": True, "wait_ms": 750})
        final_url = _validated_url(str(result.get("url", "")))
        return {
            "tab_id": int(result.get("tab_id", 0)),
            "title": str(result.get("title", ""))[:500],
            "url": final_url,
        }

    @mcp.tool()
    async def browser_read_page(
        url: str = "",
        tab_id: int = 0,
        max_chars: int = 30_000,
    ) -> dict[str, Any]:
        """Read visible text and links from an allowed URL or an already-open Edge tab."""
        if max_chars < 1_000 or max_chars > 60_000:
            raise ValueError("max_chars must be between 1000 and 60000")
        if url.strip() and tab_id:
            raise ValueError("Provide either url or tab_id, not both")

        if url.strip():
            target = _validated_url(url)
            opened = await _bridge.request(
                "open_tab",
                {"url": target, "active": False, "wait_ms": 1_200},
            )
            temporary_tab_id = int(opened.get("tab_id", 0))
            if temporary_tab_id <= 0:
                raise RuntimeError("Edge extension did not return a valid tab id")
            try:
                snapshot = await _bridge.request("snapshot", {"tab_id": temporary_tab_id})
                return _safe_page_result(snapshot, max_chars)
            finally:
                try:
                    await _bridge.request("close_tab", {"tab_id": temporary_tab_id}, timeout=5.0)
                except Exception:
                    pass

        snapshot_payload: dict[str, Any] = {}
        if tab_id:
            snapshot_payload["tab_id"] = tab_id
        snapshot = await _bridge.request("snapshot", snapshot_payload)
        return _safe_page_result(snapshot, max_chars)

    @mcp.tool()
    async def browser_list_pages() -> dict[str, Any]:
        """List Edge tabs; details are returned only for tabs on MCP-allowed hosts."""
        result = await _bridge.request("list_tabs")
        raw_tabs = result.get("tabs", [])
        pages: list[dict[str, Any]] = []
        if not isinstance(raw_tabs, list):
            return {"pages": pages}

        for item in raw_tabs[:200]:
            if not isinstance(item, dict):
                continue
            tab_id = int(item.get("tab_id", 0))
            url = str(item.get("url", ""))
            try:
                url = _validated_url(url)
            except ValueError:
                pages.append({"tab_id": tab_id, "allowed": False})
                continue
            pages.append(
                {
                    "tab_id": tab_id,
                    "title": str(item.get("title", ""))[:500],
                    "url": url,
                    "active": bool(item.get("active", False)),
                    "allowed": True,
                }
            )
        return {"pages": pages}

    @mcp.tool()
    async def browser_stop() -> dict[str, Any]:
        """Compatibility no-op: the extension remains connected because Edge is user-owned."""
        status = await _bridge.status()
        status["note"] = "Edge remains open; disable the extension to disconnect the browser bridge."
        return status
