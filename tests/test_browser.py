from __future__ import annotations

import asyncio
import inspect
import json
import unittest
from types import SimpleNamespace

from china_apps_mcp.adapters.browser import (
    BrowserBridge,
    _BRIDGE_HOST,
    _BRIDGE_PORT,
    _host_allowed,
    _safe_page_result,
    _validated_url,
)


class BrowserAllowlistTests(unittest.TestCase):
    def test_known_china_app_domains_are_allowed(self) -> None:
        self.assertTrue(_host_allowed("www.taobao.com"))
        self.assertTrue(_host_allowed("hotels.ctrip.com"))
        self.assertTrue(_host_allowed("mp.weixin.qq.com"))
        self.assertTrue(_host_allowed("www.dianping.com"))
        self.assertTrue(_host_allowed("passport.meituan.com"))

    def test_similar_but_unrelated_domains_are_rejected(self) -> None:
        self.assertFalse(_host_allowed("taobao.com.evil.example"))
        self.assertFalse(_host_allowed("notjd.com"))

    def test_url_validation_requires_http_and_allowed_host(self) -> None:
        self.assertEqual(
            _validated_url("https://mp.weixin.qq.com/s/example"),
            "https://mp.weixin.qq.com/s/example",
        )
        with self.assertRaises(ValueError):
            _validated_url("file:///C:/Windows/win.ini")
        with self.assertRaises(ValueError):
            _validated_url("http://127.0.0.1:8765/health")

    def test_page_result_filters_links_again_on_mcp_side(self) -> None:
        result = _safe_page_result(
            {
                "title": "Example",
                "url": "https://www.zhihu.com/question/1",
                "text": "hello",
                "links": [
                    {"text": "allowed", "href": "https://www.zhihu.com/question/2"},
                    {"text": "blocked", "href": "https://example.com/secret"},
                ],
            },
            30000,
        )
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(result["links"][0]["text"], "allowed")


class EdgeBridgeArchitectureTests(unittest.TestCase):
    def test_bridge_is_loopback_only(self) -> None:
        self.assertEqual(_BRIDGE_HOST, "127.0.0.1")
        self.assertEqual(_BRIDGE_PORT, 8766)

    def test_runtime_contains_no_playwright_or_cdp(self) -> None:
        source = inspect.getsource(BrowserBridge)
        self.assertNotIn("playwright", source.lower())
        self.assertNotIn("connect_over_cdp", source)
        self.assertNotIn("remote-debugging", source)


class _FakeWebSocket:
    def __init__(self, first_message: dict[str, object]) -> None:
        self.request = SimpleNamespace(headers={"Origin": "chrome-extension://test-id"})
        self.first_message = json.dumps(first_message)
        self.closed: tuple[int, str] | None = None
        self.release = asyncio.Event()

    async def recv(self) -> str:
        return self.first_message

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.release.wait()
        raise StopAsyncIteration

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        self.release.set()


class EdgeBridgeHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_hello_socket_never_becomes_extension_connection(self) -> None:
        bridge = BrowserBridge()
        websocket = _FakeWebSocket({"type": "heartbeat"})

        await bridge._handle_connection(websocket)  # type: ignore[arg-type]

        self.assertFalse(bridge.connected)
        self.assertEqual(websocket.closed, (1008, "Initial extension hello required"))

    async def test_hello_is_required_before_connection_is_published(self) -> None:
        bridge = BrowserBridge()
        websocket = _FakeWebSocket(
            {"type": "hello", "version": "0.3.1", "browser": "edge"}
        )

        task = asyncio.create_task(
            bridge._handle_connection(websocket)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0)

        self.assertTrue(bridge.connected)
        self.assertEqual(
            (await bridge.status())["extension"],
            {"extension_version": "0.3.1", "browser": "edge"},
        )

        websocket.release.set()
        await task
        self.assertFalse(bridge.connected)


if __name__ == "__main__":
    unittest.main()
