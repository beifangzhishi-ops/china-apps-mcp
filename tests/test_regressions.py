from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from mcp.server.auth.provider import AccessToken, AuthorizeError
from starlette.requests import Request

from china_apps_mcp.oauth import LocalOAuthProvider, PendingAuthorization
from china_apps_mcp.server import _consent_debug_fields, _constant_time_text_equal


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
        self.assertIn('action="/oauth/consent"', response.body.decode("utf-8"))

    async def test_consent_form_stays_under_public_path_prefix(self) -> None:
        provider = LocalOAuthProvider(
            public_base_url="https://gateway.example/cam",
            approval_secret="correct-secret-1234",
            state_file=Path(self.tmp.name) / "oauth-state-path.json",
            scopes=["mcp"],
        )
        provider.pending["request-path"] = PendingAuthorization(
            client_id="client-1",
            params=self._params(resource="https://gateway.example/cam/mcp"),
            created_at=time.time(),
        )
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/oauth/consent",
                "raw_path": b"/oauth/consent",
                "query_string": b"request=request-path",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 8765),
            }
        )

        response = await provider.consent_get(request)
        body = response.body.decode("utf-8")
        self.assertIn('action="/cam/oauth/consent"', body)
        self.assertNotIn('action="/oauth/consent"', body)

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


class OAuthDebugRegressionTests(unittest.TestCase):
    def test_consent_debug_fields_never_include_secret_values_or_raw_body(self) -> None:
        secret = "diagnostic-secret-value-1234"
        body = urlencode(
            {
                "request": "request-debug-1",
                "secret": secret,
                "action": "approve",
            }
        ).encode("utf-8")

        record = _consent_debug_fields(body, secret)
        serialized = json.dumps(record, ensure_ascii=False)

        self.assertTrue(record["secret_match"])
        self.assertEqual(record["submitted_secret_length"], len(secret))
        self.assertEqual(record["expected_secret_length"], len(secret))
        self.assertNotIn(secret, serialized)
        self.assertNotIn("raw_body", record)
        self.assertNotIn("submitted_secret", record)
        self.assertNotIn("expected_secret", record)


class BearerRegressionTests(unittest.TestCase):
    def test_unicode_bearer_input_does_not_raise(self) -> None:
        self.assertFalse(_constant_time_text_equal("é错误", "ascii-token"))
        self.assertTrue(_constant_time_text_equal("same-令牌", "same-令牌"))


class FunnelOwnershipRegressionTests(unittest.TestCase):
    CAM_PATHS = {
        "/cam/mcp",
        "/cam/authorize",
        "/cam/token",
        "/cam/register",
        "/cam/revoke",
        "/cam/oauth/consent",
        "/cam/health",
        "/.well-known/oauth-authorization-server/cam",
        "/cam/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/cam/mcp",
        "/cam/mcp/.well-known/oauth-protected-resource",
    }

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def _script(self, name: str) -> str:
        return (self.repo_root / "scripts" / name).read_text(encoding="utf-8")

    def test_production_configure_owns_only_exact_cam_paths(self) -> None:
        text = self._script("configure-funnel.ps1")
        configured = set(re.findall(r'Public\s*=\s*"([^"]+)"', text))
        self.assertEqual(configured, self.CAM_PATHS)
        self.assertIn('AbsolutePath.TrimEnd(\'/\') -ne "/cam"', text)
        self.assertNotIn("funnel reset", text.lower())
        self.assertNotIn("--set-path=/v1", text)
        self.assertNotIn("--https=443 off", text)

    def test_production_disable_removes_only_exact_cam_paths(self) -> None:
        text = self._script("disable-funnel.ps1")
        disabled = set(
            re.findall(r'^\s*"(/[^\"]+)"[,]?\s*$', text, flags=re.MULTILINE)
        )
        self.assertEqual(disabled, self.CAM_PATHS)
        self.assertNotIn("funnel reset", text.lower())
        self.assertNotIn("--set-path=/v1", text)
        self.assertNotIn("--https=443 off", text)

    def test_legacy_cleanup_is_explicit_and_limited_to_historical_cam_routes(self) -> None:
        text = self._script("remove-legacy-cam-funnel.ps1")
        self.assertIn("ConfirmLegacyCamRemoval", text)
        routes = {
            (int(port), path)
            for port, path in re.findall(
                r'@\{\s*Https\s*=\s*(443|8443);\s*Path\s*=\s*"(/?mcp|/)"\s*\}',
                text,
            )
        }
        self.assertEqual(
            routes,
            {(443, "/mcp"), (443, "/"), (8443, "/mcp"), (8443, "/")},
        )
        self.assertIn("$publicBase/health", text)
        self.assertNotIn("funnel reset", text.lower())
        self.assertNotIn("--https=443 off", text)


class LauncherRegressionTests(unittest.TestCase):
    def _script(self, name: str) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        return (repo_root / "scripts" / name).read_text(encoding="utf-8")

    @staticmethod
    def _normalized(text: str) -> str:
        return re.sub(r"\s+", " ", text)

    def test_start_script_uses_configured_port_and_preflight_listener_check(self) -> None:
        text = self._script("start.ps1")
        self.assertIn("Get-McpPort", text)
        self.assertIn("Get-LoopbackListenerPid", text)
        self.assertIn("netstat.exe", text)
        self.assertIn("-LocalPort $Port", text)
        self.assertIn("Port $port is already in use", text)
        self.assertIn('Set-Content -LiteralPath $pidFile -Value ([string]$listenerPid)', text)

    def test_stop_script_has_restricted_shell_listener_fallback(self) -> None:
        text = self._script("stop.ps1")
        self.assertIn("Get-LoopbackListenerPid", text)
        self.assertIn("netstat.exe", text)

    def test_stop_requires_exact_listener_and_cam_health_before_stopping(self) -> None:
        text = self._script("stop.ps1")
        normalized = self._normalized(text)

        self.assertIn("Get-Process -Id $targetPid", text)
        self.assertRegex(
            normalized,
            r"Get-LoopbackListenerPid -Port \$port.*\$listenerPid -ne \$targetPid",
        )
        self.assertRegex(normalized, r"Test-CamHealth -Port \$port")
        self.assertIn('service -eq "china-apps-mcp"', text)
        self.assertIn('mcp_path -eq "/mcp"', text)

        stop_calls = re.findall(r"(?im)^\s*Stop-Process\s+([^\r\n]+)", text)
        self.assertEqual(len(stop_calls), 1)
        self.assertRegex(stop_calls[0], r"-Id\s+\$targetPid\b")

    def test_process_identity_is_optional_but_explicit_mismatch_is_rejected(self) -> None:
        for name in ("stop.ps1", "start.ps1"):
            text = self._script(name)
            normalized = self._normalized(text)
            self.assertIn("Get-OptionalProcessIdentity", text)
            self.assertIn('return "unknown"', text)
            self.assertIn('return "invalid"', text)
            self.assertRegex(normalized, r'\$identity -eq "invalid"')
            self.assertNotRegex(
                normalized,
                r"\$null -eq \$\w+Cim -or -not \$\w+Cim\.CommandLine",
            )

    def test_start_never_claims_unknown_listener_without_pid_file_match_and_health(self) -> None:
        text = self._script("start.ps1")
        normalized = self._normalized(text)

        self.assertIn("$pidFileMatches", text)
        self.assertIn("[int]::TryParse($pidFileText", text)
        self.assertRegex(
            normalized,
            r"\$pidFileMatches -and \(Test-CamHealth -Port \$port\)",
        )
        self.assertIn("Port $port is already in use", text)

    def test_lifecycle_scripts_have_no_broad_process_kill(self) -> None:
        for name in ("stop.ps1", "start.ps1"):
            text = self._script(name)
            self.assertNotRegex(text, r"(?im)\btaskkill\s+/im\b")
            self.assertNotRegex(text, r"(?im)Stop-Process\s+-Name\b")
            self.assertNotRegex(
                text,
                r"(?im)Stop-Process[^\r\n]*(?:python|node)(?:\.exe)?",
            )


if __name__ == "__main__":
    unittest.main()
