from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from mcp.server.auth.provider import AccessToken, AuthorizeError
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

    def _params(self, *, resource: str = "https://gateway.example/mcp") -> SimpleNamespace:
        return SimpleNamespace(
            redirect_uri="https://client.example/callback",
            scopes=["mcp"],
            state="state-123",
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            resource=resource,
        )

    def _pending(self) -> PendingAuthorization:
        return PendingAuthorization(
            client_id="client-1",
            params=self._params(),
            created_at=time.time(),
        )

    def _client(self) -> SimpleNamespace:
        return SimpleNamespace(client_id="client-1")

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

    async def test_authorize_rejects_resource_for_another_server(self) -> None:
        with self.assertRaises(AuthorizeError) as ctx:
            await self.provider.authorize(
                self._client(),
                self._params(resource="https://other.example/mcp"),
            )
        self.assertEqual(ctx.exception.error, "invalid_target")
        self.assertFalse(self.provider.pending)

    async def test_access_token_must_match_the_mcp_resource(self) -> None:
        token = AccessToken(
            token="mcp_at_wrong_resource",
            client_id="client-1",
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
            resource="https://other.example/mcp",
        )
        self.provider.access_tokens[token.token] = token

        loaded = await self.provider.load_access_token(token.token)

        self.assertIsNone(loaded)
        self.assertNotIn(token.token, self.provider.access_tokens)

    async def test_refresh_rotation_preserves_resource_and_family(self) -> None:
        issued = self.provider._mint_token_pair(
            client_id="client-1",
            scopes=["mcp"],
            resource="https://gateway.example/mcp",
        )
        self.assertIsNotNone(issued.refresh_token)
        old_refresh = str(issued.refresh_token)
        current = self.provider.refresh_tokens[old_refresh]
        original_family = self.provider.token_grants[old_refresh].family_id

        refreshed = await self.provider.exchange_refresh_token(
            self._client(),
            current,
            ["mcp"],
        )

        self.assertNotIn(old_refresh, self.provider.refresh_tokens)
        self.assertNotIn(old_refresh, self.provider.token_grants)
        self.assertIsNotNone(refreshed.refresh_token)
        new_refresh = str(refreshed.refresh_token)
        new_access = self.provider.access_tokens[refreshed.access_token]
        self.assertEqual(new_access.resource, "https://gateway.example/mcp")
        self.assertEqual(self.provider.token_grants[new_refresh].resource, "https://gateway.example/mcp")
        self.assertEqual(self.provider.token_grants[new_refresh].family_id, original_family)
        self.assertEqual(self.provider.token_grants[refreshed.access_token].family_id, original_family)

    async def test_revoking_one_token_revokes_its_whole_family(self) -> None:
        issued = self.provider._mint_token_pair(
            client_id="client-1",
            scopes=["mcp"],
            resource="https://gateway.example/mcp",
        )
        self.assertIsNotNone(issued.refresh_token)
        refresh_value = str(issued.refresh_token)
        access = self.provider.access_tokens[issued.access_token]

        await self.provider.revoke_token(access)

        self.assertNotIn(issued.access_token, self.provider.access_tokens)
        self.assertNotIn(refresh_value, self.provider.refresh_tokens)
        self.assertNotIn(issued.access_token, self.provider.token_grants)
        self.assertNotIn(refresh_value, self.provider.token_grants)


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
