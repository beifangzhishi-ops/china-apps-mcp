from __future__ import annotations

import hmac
import html
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


@dataclass
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    created_at: float
    failed_attempts: int = 0


class LocalOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Small single-user OAuth provider for a personal MCP gateway.

    Dynamic clients and long-lived tokens are persisted under .state so ChatGPT does
    not have to re-register after every gateway restart. Authorization codes and
    pending consent requests intentionally stay in memory because they are short-lived.
    """

    def __init__(
        self,
        *,
        public_base_url: str,
        approval_secret: str,
        state_file: Path,
        scopes: list[str] | None = None,
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.approval_secret = approval_secret
        self.state_file = state_file
        self.scopes = scopes or ["mcp"]

        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.pending: dict[str, PendingAuthorization] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.clients = {
                item["client_id"]: OAuthClientInformationFull.model_validate(item)
                for item in raw.get("clients", [])
            }
            now = int(time.time())
            self.access_tokens = {
                item["token"]: AccessToken.model_validate(item)
                for item in raw.get("access_tokens", [])
                if item.get("expires_at") is None or int(item["expires_at"]) > now
            }
            self.refresh_tokens = {
                item["token"]: RefreshToken.model_validate(item)
                for item in raw.get("refresh_tokens", [])
                if item.get("expires_at") is None or int(item["expires_at"]) > now
            }
        except Exception as exc:  # fail closed instead of silently losing auth state
            raise RuntimeError(f"Failed to load OAuth state from {self.state_file}: {exc}") from exc

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": [item.model_dump(mode="json") for item in self.clients.values()],
            "access_tokens": [item.model_dump(mode="json") for item in self.access_tokens.values()],
            "refresh_tokens": [item.model_dump(mode="json") for item in self.refresh_tokens.values()],
        }
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def _cleanup_ephemeral(self) -> None:
        now = time.time()
        self.pending = {
            key: value
            for key, value in self.pending.items()
            if value.created_at + 300 > now
        }
        self.codes = {
            key: value
            for key, value in self.codes.items()
            if value.expires_at > now
        }

    def _mint_token_pair(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        now = int(time.time())
        access_value = "mcp_at_" + secrets.token_urlsafe(32)
        refresh_value = "mcp_rt_" + secrets.token_urlsafe(40)
        self.access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 3600,
            resource=resource,
        )
        self.refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + 30 * 24 * 3600,
        )
        self._save_state()
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info
        self._save_state()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._cleanup_ephemeral()
        request_id = secrets.token_urlsafe(32)
        self.pending[request_id] = PendingAuthorization(
            client_id=client.client_id,
            params=params,
            created_at=time.time(),
        )
        return f"{self.public_base_url}/oauth/consent?request={quote(request_id, safe='')}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        self._cleanup_ephemeral()
        code = self.codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        current = self.codes.pop(authorization_code.code, None)
        if current is None or current.client_id != client.client_id:
            raise ValueError("authorization code is invalid or already used")
        return self._mint_token_pair(
            client_id=current.client_id,
            scopes=current.scopes,
            resource=current.resource,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        item = self.access_tokens.get(token)
        if item is None:
            return None
        if item.expires_at is not None and item.expires_at <= int(time.time()):
            self.access_tokens.pop(token, None)
            self._save_state()
            return None
        return item

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        item = self.refresh_tokens.get(refresh_token)
        if item is None or item.client_id != client.client_id:
            return None
        if item.expires_at is not None and item.expires_at <= int(time.time()):
            self.refresh_tokens.pop(refresh_token, None)
            self._save_state()
            return None
        return item

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        current = self.refresh_tokens.pop(refresh_token.token, None)
        if current is None or current.client_id != client.client_id:
            raise ValueError("refresh token is invalid or already used")
        requested = scopes or current.scopes
        if not set(requested).issubset(set(current.scopes)):
            raise ValueError("requested scope exceeds the original grant")
        return self._mint_token_pair(
            client_id=current.client_id,
            scopes=requested,
            resource=None,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)
        self._save_state()

    def _page_headers(self) -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }

    def _render_consent(self, request_id: str, pending: PendingAuthorization, error: str = "") -> HTMLResponse:
        client = self.clients.get(pending.client_id)
        client_name = html.escape((client.client_name if client else None) or "ChatGPT / MCP client")
        redirect_host = html.escape(urlparse(str(pending.params.redirect_uri)).netloc)
        scope_text = html.escape(" ".join(pending.params.scopes or self.scopes))
        error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>China Apps MCP authorization</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:560px;margin:48px auto;padding:0 20px;line-height:1.5}}
.card{{border:1px solid #ddd;border-radius:12px;padding:24px}} input{{width:100%;box-sizing:border-box;padding:10px;margin:8px 0 16px}}
button{{padding:10px 16px;margin-right:8px}} .error{{color:#b00020}} code{{word-break:break-all}}
</style></head><body><div class="card">
<h1>Authorize China Apps MCP</h1>
<p><strong>{client_name}</strong> is requesting access to this personal MCP gateway.</p>
<p>Scope: <code>{scope_text}</code><br>Redirect host: <code>{redirect_host}</code></p>
{error_html}
<form method="post" action="/oauth/consent">
<input type="hidden" name="request" value="{html.escape(request_id, quote=True)}">
<label>Gateway approval secret</label>
<input type="password" name="secret" autocomplete="current-password" required autofocus>
<button type="submit" name="action" value="approve">Approve</button>
<button type="submit" name="action" value="deny" formnovalidate>Deny</button>
</form></div></body></html>"""
        return HTMLResponse(body, headers=self._page_headers())

    async def consent_get(self, request: Request) -> Response:
        self._cleanup_ephemeral()
        request_id = request.query_params.get("request", "")
        pending = self.pending.get(request_id)
        if pending is None:
            return HTMLResponse(
                "Authorization request is missing or expired.",
                status_code=400,
                headers=self._page_headers(),
            )
        return self._render_consent(request_id, pending)

    async def consent_post(self, request: Request) -> Response:
        self._cleanup_ephemeral()
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        request_id = form.get("request", [""])[0]
        action = form.get("action", [""])[0]
        secret = form.get("secret", [""])[0]
        pending = self.pending.get(request_id)
        if pending is None:
            return HTMLResponse(
                "Authorization request is missing or expired.",
                status_code=400,
                headers=self._page_headers(),
            )

        if action == "deny":
            self.pending.pop(request_id, None)
            return RedirectResponse(
                construct_redirect_uri(
                    str(pending.params.redirect_uri),
                    error="access_denied",
                    state=pending.params.state,
                ),
                status_code=302,
                headers={"Cache-Control": "no-store"},
            )

        if not hmac.compare_digest(secret, self.approval_secret):
            pending.failed_attempts += 1
            if pending.failed_attempts >= 5:
                self.pending.pop(request_id, None)
                return HTMLResponse(
                    "Too many failed approval attempts. Start the OAuth flow again.",
                    status_code=429,
                    headers=self._page_headers(),
                )
            return self._render_consent(request_id, pending, "Approval secret is incorrect.")

        self.pending.pop(request_id, None)
        code_value = "mcp_code_" + secrets.token_urlsafe(32)
        code = AuthorizationCode(
            code=code_value,
            client_id=pending.client_id,
            scopes=pending.params.scopes or self.scopes,
            expires_at=time.time() + 300,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
        )
        self.codes[code_value] = code
        return RedirectResponse(
            construct_redirect_uri(
                str(pending.params.redirect_uri),
                code=code_value,
                state=pending.params.state,
            ),
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )


def oauth_resource_metadata(public_mcp_url: str, public_base_url: str, scopes: list[str]) -> dict[str, Any]:
    return {
        "resource": public_mcp_url,
        "authorization_servers": [public_base_url],
        "scopes_supported": scopes,
        "bearer_methods_supported": ["header"],
    }
