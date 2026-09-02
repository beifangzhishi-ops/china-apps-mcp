from __future__ import annotations

import inspect
import unittest

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


if __name__ == "__main__":
    unittest.main()
