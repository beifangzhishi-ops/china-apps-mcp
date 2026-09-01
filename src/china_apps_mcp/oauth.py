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
    TokenError,
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
    def __init__(self, *, public_base_url: str, approval_secret: str, state_file: Path, scopes: list[str] | None = None) -> None:
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
        raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.clients = {item["client_id"]: OAuthClientInformationFull.model_validate(item) for item in raw.get("clients", [])}
        now = int(time.time())
        self.access_tokens = {item["token"]: AccessToken.model_validate(item) for item in raw.get("access_tokens", []) if item.get("expires_at") is None or int(item["expires_at"]) > now}
        self.refresh_tokens = {item["token"]: RefreshToken.model_validate(item) for item in raw.get("refresh_tokens", []) if item.get("expires_at") is None or int(item["expires_at"]) > now}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"clients": [x.model_dump(mode="json") for x in self.clients.values()], "access_tokens": [x.model_dump(mode="json") for x in self.access_tokens.values()], "refresh_tokens": [x.model_dump(mode="json") for x in self.refresh_tokens.values()]}
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cleanup_ephemeral(self) -> None:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v.created_at + 300 > now}
        self.codes = {k: v for k, v in self.codes.items() if v.expires_at > now}

    async def get_client(self, client_id: str):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info
        self._save_state()

    async def authorize(self, client, params):
        self._cleanup_ephemeral()
        request_id = secrets.token_urlsafe(32)
        self.pending[request_id] = PendingAuthorization(client.client_id, params, time.time())
        return f"{self.public_base_url}/oauth/consent?request={quote(request_id, safe='')}"

    async def consent_post(self, request: Request) -> Response:
        self._cleanup_ephemeral()
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
        request_id = form.get("request", [""])[0]
        secret = form.get("secret", [""])[0]
        pending = self.pending.get(request_id)
        if pending is None:
            return HTMLResponse("Authorization request is missing or expired.", status_code=400)
        if not hmac.compare_digest(secret.encode("utf-8"), self.approval_secret.encode("utf-8")):
            return HTMLResponse("Approval secret is incorrect.", status_code=401)

        code_value = "mcp_code_" + secrets.token_urlsafe(32)
        self.codes[code_value] = AuthorizationCode(
            code=code_value,
            client_id=pending.client_id,
            scopes=pending.params.scopes or self.scopes,
            expires_at=time.time() + 300,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
        )

        redirect = RedirectResponse(construct_redirect_uri(str(pending.params.redirect_uri), code=code_value, state=pending.params.state), status_code=302)
        self.pending.pop(request_id, None)
        return redirect


def oauth_resource_metadata(public_mcp_url: str, public_base_url: str, scopes: list[str]) -> dict[str, Any]:
    return {"resource": public_mcp_url, "authorization_servers": [public_base_url], "scopes_supported": scopes, "bearer_methods_supported": ["header"]}
