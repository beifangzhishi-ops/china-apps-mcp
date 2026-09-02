from __future__ import annotations

import unittest

from china_apps_mcp.adapters.browser import _host_allowed, _validated_url


class BrowserAllowlistTests(unittest.TestCase):
    def test_known_china_app_domains_are_allowed(self) -> None:
        self.assertTrue(_host_allowed("www.taobao.com"))
        self.assertTrue(_host_allowed("hotels.ctrip.com"))
        self.assertTrue(_host_allowed("mp.weixin.qq.com"))

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


if __name__ == "__main__":
    unittest.main()
