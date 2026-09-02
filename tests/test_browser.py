from __future__ import annotations

import inspect
import unittest

from china_apps_mcp.adapters.browser import (
    BrowserRuntime,
    _host_allowed,
    _validated_cdp_url,
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


class BrowserCdpTests(unittest.TestCase):
    def test_cdp_endpoint_must_be_loopback_http_origin(self) -> None:
        self.assertEqual(
            _validated_cdp_url("http://127.0.0.1:9222/"),
            "http://127.0.0.1:9222",
        )
        self.assertEqual(
            _validated_cdp_url("http://localhost:9222"),
            "http://localhost:9222",
        )

        for invalid in (
            "https://127.0.0.1:9222",
            "http://192.168.1.10:9222",
            "http://example.com:9222",
            "http://127.0.0.1",
            "http://127.0.0.1:9222/json/version",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    _validated_cdp_url(invalid)

    def test_runtime_is_attach_only(self) -> None:
        source = inspect.getsource(BrowserRuntime._ensure_attached)
        self.assertIn("connect_over_cdp", source)
        self.assertNotIn("launch_persistent_context", source)
        self.assertNotIn(".launch(", source)


if __name__ == "__main__":
    unittest.main()
