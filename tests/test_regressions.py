from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from starlette.requests import Request

from china_apps_mcp.oauth import LocalOAuthProvider, PendingAuthorization
from china_apps_mcp.server import _constant_time_text_equal


def _post_request(fields: dict[str, str]) -> Request:
    body = urlencode(fields).encode("utf-8")
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/oauth/consent",
            "raw_path": b"/oauth/consent",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8765),
        },
        receive,
    )


class OAuthProviderRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.provider = LocalOAuthProvider(
            public_base_url="https://gateway.example",
            approval_secret="correct-secret-1234",
            state_file=Path(self.tmp.name) / "oauth-state.json",
            scopes=["mcp"],
        )

    def _pending(self) -> PendingAuthorization:
        params = SimpleNamespace(
            redirect_uri="https://client.example/callback",
            scopes=["mcp"],
            state="state-123",
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            resource="https://gateway.example/mcp",
        )
        return PendingAuthorization(
            client_id="client-1",
            params=params,
            created_at=time.time(),
        )

    async def test_provider_keeps_full_oauth_contract(self) -> None:
        required_methods = {
            "load_authorization_code",
            "exchange_authorization_code",
            "load_access_token",
            "load_refresh_token",
            "exchange_refresh_token",
            "revoke_token",
            "consent_get",
            "consent_post",
        }
        for method in required_methods:
            self.assertTrue(callable(getattr(self.provider, method, None)), method)

    async def test_consent_get_renders_security_headers(self) -> None:
        self.provider.pending["request-1"] = self._pending()
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/oauth/consent",
                "raw_path": b"/oauth/consent",
                "query_string": b"request=request-1",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 8765),
            }
        )

        response = await self.provider.consent_get(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn("default-src 'none'", response.headers.get("content-security-policy", ""))
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")

    async def test_unicode_wrong_secret_is_normal_mismatch(self) -> None:
        self.provider.pending["request-2"] = self._pending()
        response = await self.provider.consent_post(
            _post_request(
                {
                    "request": "request-2",
                    "secret": "错误",
                    "action": "approve",
                }
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("request-2", self.provider.pending)
        self.assertEqual(self.provider.pending["request-2"].failed_attempts, 1)


class BearerRegressionTests(unittest.TestCase):
    def test_unicode_bearer_input_does_not_raise(self) -> None:
        self.assertFalse(_constant_time_text_equal("é错误", "ascii-token"))
        self.assertTrue(_constant_time_text_equal("same-令牌", "same-令牌"))


class LauncherRegressionTests(unittest.TestCase):
    def test_start_script_uses_configured_port_and_preflight_listener_check(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "start.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-McpPort", text)
        self.assertIn("-LocalPort $port", text)
        self.assertIn("Port $port is already in use", text)
        self.assertIn('Set-Content -LiteralPath $pidFile -Value ([string]$listenerPid)', text)


if __name__ == "__main__":
    unittest.main()
