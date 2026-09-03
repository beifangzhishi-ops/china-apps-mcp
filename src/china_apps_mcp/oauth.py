from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
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
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


logger = logging.getLogger(__name__)


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    created_at: float
    failed_attempts: int = 0


@dataclass
class CompletedConsent:
    completed_at: float
    redirect_url: str


@dataclass(frozen=True)
class TokenGrant:
    family_id: str
    resource: str


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
        self.public_mcp_url = f"{self.public_base_url}/mcp"
        self.approval_secret = approval_secret
        self.state_file = state_file
        self.scopes = scopes or ["mcp"]

        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.token_grants: dict[str, TokenGrant] = {}
        self.pending: dict[str, PendingAuthorization] = {}
        self.completed_consents: dict[str, CompletedConsent] = {}
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
            loaded_access = {
                item["token"]: AccessToken.model_validate(item)
                for item in raw.get("access_tokens", [])
                if (item.get("expires_at") is None or int(item["expires_at"]) > now)
                and item.get("resource") == self.public_mcp_url
            }
            raw_grants = {
                item["token"]: TokenGrant(
                    family_id=str(item["family_id"]),
                    resource=str(item["resource"]),
                )
                for item in raw.get("token_grants", [])
                if item.get("token") and item.get("family_id") and item.get("resource")
            }
            loaded_refresh = {
                item["token"]: RefreshToken.model_validate(item)
                for item in raw.get("refresh_tokens", [])
                if (item.get("expires_at") is None or int(item["expires_at"]) > now)
                and item.get("token") in raw_grants
                and raw_grants[item["token"]].resource == self.public_mcp_url
            }

            # Older state files did not persist refresh-token resource/family metadata.
            # Keep audience-bound access tokens, but intentionally discard legacy
            # refresh tokens rather than allowing them to mint unbound access tokens.
            self.access_tokens = loaded_access
            self.refresh_tokens = loaded_refresh
            active_tokens = set(self.access_tokens) | set(self.refresh_tokens)
            self.token_grants = {
                token: grant
                for token, grant in raw_grants.items()
                if token in active_tokens and grant.resource == self.public_mcp_url
            }
        except Exception as exc:
            raise RuntimeError(f"Failed to load OAuth state from {self.state_file}: {exc}") from exc

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": [item.model_dump(mode="json") for item in self.clients.values()],
            "access_tokens": [item.model_dump(mode="json") for item in self.access_tokens.values()],
            "refresh_tokens": [item.model_dump(mode="json") for item in self.refresh_tokens.values()],
            "token_grants": [
                {
                    "token": token,
                    "family_id": grant.family_id,
                    "resource": grant.resource,
                }
                for token, grant in self.token_grants.items()
                if token in self.access_tokens or token in self.refresh_tokens
            ],
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
        self.completed_consents = {
            key: value
            for key, value in self.completed_consents.items()
            if value.completed_at + 300 > now
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
        family_id: str | None = None,
    ) -> OAuthToken:
        if resource != self.public_mcp_url:
            raise TokenError(
                "invalid_target",
                "token resource does not identify this MCP server",
            )

        now = int(time.time())
        access_value = "mcp_at_" + secrets.token_urlsafe(32)
        refresh_value = "mcp_rt_" + secrets.token_urlsafe(40)
        grant = TokenGrant(
            family_id=family_id or secrets.token_urlsafe(24),
            resource=resource,
        )
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
        self.token_grants[access_value] = grant
        self.token_grants[refresh_value] = grant
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
        if params.resource != self.public_mcp_url:
            raise AuthorizeError(
                "invalid_target",
                "resource must identify this MCP server",
            )

        request_id = secrets.token_urlsafe(32)
        self.pending[request_id] = PendingAuthorization(
            client_id=client.client_id,
            params=params,
            created_at=time.time(),
        )
        logger.info(
            "AUTH_REQUEST_CREATED request=%s client=%s state=%s redirect_uri=%s timestamp=%d",
            short_hash(request_id),
            client.client_id,
            short_hash(str(params.state or "")),
            str(params.redirect_uri),
            int(time.time()),
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
        if code.resource != self.public_mcp_url:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        code_hash = short_hash(authorization_code.code)
        current = self.codes.pop(authorization_code.code, None)
        if current is None or current.client_id != client.client_id:
            logger.warning(
                "TOKEN_EXCHANGE_FAILED code=%s client=%s",
                code_hash,
                client.client_id,
            )
            raise TokenError("invalid_grant", "authorization code is invalid or already used")
        if current.resource != self.public_mcp_url:
            raise TokenError("invalid_target", "authorization code has an invalid resource")
        try:
            token = self._mint_token_pair(
                client_id=current.client_id,
                scopes=current.scopes,
                resource=current.resource,
            )
        except Exception:
            logger.exception(
                "TOKEN_EXCHANGE_FAILED code=%s client=%s",
                code_hash,
                client.client_id,
            )
            raise
        logger.info(
            "TOKEN_EXCHANGE_SUCCESS code=%s client=%s",
            code_hash,
            client.client_id,
        )
        return token

    async def load_access_token(self, token: str) -> AccessToken | None:
        item = self.access_tokens.get(token)
        if item is None:
            return None
        if item.expires_at is not None and item.expires_at <= int(time.time()):
            self.access_tokens.pop(token, None)
            self.token_grants.pop(token, None)
            self._save_state()
            return None
        if item.resource != self.public_mcp_url:
            self.access_tokens.pop(token, None)
            self.token_grants.pop(token, None)
            self._save_state()
            return None
        return item

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        item = self.refresh_tokens.get(refresh_token)
        grant = self.token_grants.get(refresh_token)
        if item is None or item.client_id != client.client_id:
            return None
        if grant is None or grant.resource != self.public_mcp_url:
            self.refresh_tokens.pop(refresh_token, None)
            self.token_grants.pop(refresh_token, None)
            self._save_state()
            return None
        if item.expires_at is not None and item.expires_at <= int(time.time()):
            self.refresh_tokens.pop(refresh_token, None)
            self.token_grants.pop(refresh_token, None)
            self._save_state()
            return None
        return item

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        current = self.refresh_tokens.get(refresh_token.token)
        grant = self.token_grants.get(refresh_token.token)
        if current is None or current.client_id != client.client_id or grant is None:
            raise TokenError("invalid_grant", "refresh token is invalid or already used")
        if grant.resource != self.public_mcp_url:
            raise TokenError("invalid_target", "refresh token has an invalid resource")

        requested = scopes or current.scopes
        if not set(requested).issubset(set(current.scopes)):
            raise TokenError("invalid_scope", "requested scope exceeds the original grant")

        self.refresh_tokens.pop(refresh_token.token, None)
        self.token_grants.pop(refresh_token.token, None)
        return self._mint_token_pair(
            client_id=current.client_id,
            scopes=requested,
            resource=grant.resource,
            family_id=grant.family_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        grant = self.token_grants.get(token.token)
        if grant is None:
            self.access_tokens.pop(token.token, None)
            self.refresh_tokens.pop(token.token, None)
            self._save_state()
            return

        family_tokens = [
            value
            for value, candidate in self.token_grants.items()
            if candidate.family_id == grant.family_id
        ]
        for value in family_tokens:
            self.access_tokens.pop(value, None)
            self.refresh_tokens.pop(value, None)
            self.token_grants.pop(value, None)
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
        base_path = urlparse(self.public_base_url).path.rstrip("/")
        consent_action = f"{base_path}/oauth/consent" if base_path else "/oauth/consent"
        consent_action = html.escape(consent_action, quote=True)
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
<form method="post" action="{consent_action}">
<input type="hidden" name="request" value="{html.escape(request_id, quote=True)}">
<label>Gateway approval secret</label>
<input type="password" name="secret" autocomplete="current-password" required autofocus>
<button type="submit" name="action" value="approve">Approve</button>
<button type="submit" name="action" value="deny" formnovalidate>Deny</button>
</form></div>
<script>
document.querySelector("form").addEventListener("submit", function () {{
    this.querySelectorAll("button").forEach(function (button) {{
        button.disabled = true;
    }});
}});
</script></body></html>"""
        return HTMLResponse(body, headers=self._page_headers())

    def _completed_consent_response(self) -> HTMLResponse:
        return HTMLResponse(
            "<h1>Authorization already completed.</h1>"
            "<p>Please return to ChatGPT and continue.</p>",
            status_code=409,
            headers=self._page_headers(),
        )

    async def consent_get(self, request: Request) -> Response:
        self._cleanup_ephemeral()
        request_id = request.query_params.get("request", "")
        pending = self.pending.get(request_id)
        completed = self.completed_consents.get(request_id)
        logger.info(
            "CONSENT_GET request=%s exists=%s completed=%s",
            short_hash(request_id),
            pending is not None,
            completed is not None,
        )
        if pending is None:
            if completed is not None:
                return self._completed_consent_response()
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
            if request_id in self.completed_consents:
                logger.info("CONSENT_DUPLICATE request=%s", short_hash(request_id))
                return self._completed_consent_response()
            return HTMLResponse(
                "Authorization request is missing or expired.",
                status_code=400,
                headers=self._page_headers(),
            )

        if action == "deny":
            redirect_url = construct_redirect_uri(
                str(pending.params.redirect_uri),
                error="access_denied",
                state=pending.params.state,
            )
            self.completed_consents[request_id] = CompletedConsent(
                completed_at=time.time(),
                redirect_url=redirect_url,
            )
            self.pending.pop(request_id, None)
            logger.info("CONSENT_DENIED request=%s", short_hash(request_id))
            logger.info(
                "REDIRECT_SENT request=%s code_hash=%s",
                short_hash(request_id),
                "none",
            )
            return RedirectResponse(
                redirect_url,
                status_code=302,
                headers={"Cache-Control": "no-store"},
            )

        # compare_digest() only accepts ASCII strings. Comparing UTF-8 bytes keeps
        # the constant-time comparison behavior while treating accidental Unicode
        # input as a normal mismatch instead of crashing the OAuth callback.
        secret_bytes = secret.encode("utf-8")
        expected_bytes = self.approval_secret.encode("utf-8")
        if not hmac.compare_digest(secret_bytes, expected_bytes):
            pending.failed_attempts += 1
            logger.info(
                "CONSENT_SECRET_REJECTED request=%s attempts=%d",
                short_hash(request_id),
                pending.failed_attempts,
            )
            if pending.failed_attempts >= 5:
                self.pending.pop(request_id, None)
                return HTMLResponse(
                    "Too many failed approval attempts. Start the OAuth flow again.",
                    status_code=429,
                    headers=self._page_headers(),
                )
            return self._render_consent(request_id, pending, "Approval secret is incorrect.")

        logger.info("CONSENT_APPROVED request=%s", short_hash(request_id))
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
        redirect_url = construct_redirect_uri(
            str(pending.params.redirect_uri),
            code=code_value,
            state=pending.params.state,
        )
        self.completed_consents[request_id] = CompletedConsent(
            completed_at=time.time(),
            redirect_url=redirect_url,
        )
        self.pending.pop(request_id, None)
        logger.info(
            "REDIRECT_SENT request=%s code_hash=%s",
            short_hash(request_id),
            short_hash(code_value),
        )
        return RedirectResponse(
            redirect_url,
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
