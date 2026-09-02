from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from dotenv import load_dotenv
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from . import __version__
from .adapters.browser import (
    register_browser_tools,
    start_browser_bridge,
    stop_browser_bridge,
)
from .oauth import LocalOAuthProvider, oauth_resource_metadata

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8765"))
ACCESS_TOKEN = os.getenv("MCP_ACCESS_TOKEN", "").strip()

_raw_auth_mode = os.getenv("MCP_AUTH_MODE", "").strip().lower()
AUTH_MODE = _raw_auth_mode or ("token" if ACCESS_TOKEN else "none")
if AUTH_MODE not in {"none", "token", "oauth"}:
    raise RuntimeError("MCP_AUTH_MODE must be one of: none, token, oauth")

PUBLIC_BASE_URL = os.getenv("MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
PUBLIC_MCP_URL = f"{PUBLIC_BASE_URL}/mcp" if PUBLIC_BASE_URL else ""
OAUTH_APPROVAL_SECRET = os.getenv("MCP_OAUTH_APPROVAL_SECRET", "").strip()
OAUTH_STATE_FILE = Path(os.getenv("MCP_OAUTH_STATE_FILE", ".state/oauth-state.json"))
OAUTH_SCOPES = ["mcp"]
OAUTH_DEBUG_LOG_SECRETS = os.getenv("MCP_OAUTH_DEBUG_LOG_SECRETS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_oauth_debug_log_raw = os.getenv(
    "MCP_OAUTH_DEBUG_LOG_FILE",
    "logs/oauth-consent-debug.log",
).strip()
OAUTH_DEBUG_LOG_FILE = Path(_oauth_debug_log_raw)
if not OAUTH_DEBUG_LOG_FILE.is_absolute():
    OAUTH_DEBUG_LOG_FILE = REPO_ROOT / OAUTH_DEBUG_LOG_FILE

# SHA-256 of the fixed inline submit guard rendered by LocalOAuthProvider.
# The provider's CSP deliberately starts at default-src 'none'; authorize only
# this exact script rather than enabling all inline script execution.
CONSENT_SUBMIT_SCRIPT_CSP = "'sha256-tLpzUZb3JN1sjoH7XloLsXx7t5Xf77fZJoL4dkgFzs0='"


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _constant_time_text_equal(candidate: str, expected: str) -> bool:
    """Compare arbitrary Unicode text without compare_digest() ASCII failures."""
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _request_peer(request: Request) -> dict[str, str]:
    client_host = request.client.host if request.client is not None else ""
    return {
        "client": client_host,
        "x_forwarded_for": request.headers.get("x-forwarded-for", ""),
        "x_forwarded_proto": request.headers.get("x-forwarded-proto", ""),
        "host": request.headers.get("host", ""),
        "user_agent": request.headers.get("user-agent", ""),
    }


def _append_oauth_debug(record: dict[str, Any]) -> None:
    if not OAUTH_DEBUG_LOG_SECRETS:
        return
    OAUTH_DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with OAUTH_DEBUG_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _allow_consent_submit_script(response: Response) -> Response:
    """Permit only the exact consent-page submit guard under the strict CSP."""
    csp = response.headers.get("content-security-policy", "")
    if csp and "script-src" not in csp:
        response.headers["Content-Security-Policy"] = (
            f"{csp.rstrip('; ')}; script-src {CONSENT_SUBMIT_SCRIPT_CSP}"
        )
    return response


def _retry_completed_consent(request_id: str) -> RedirectResponse | None:
    """Replay a completed consent callback instead of returning a terminal 409 page."""
    if oauth_provider is None:
        return None
    completed = oauth_provider.completed_consents.get(request_id)
    if completed is None:
        return None
    return RedirectResponse(
        completed.redirect_url,
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


async def _log_consent_post(request: Request) -> None:
    if not OAUTH_DEBUG_LOG_SECRETS:
        return
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8", errors="replace")
    form = parse_qs(body, keep_blank_values=True)
    submitted_secret = form.get("secret", [""])[0]
    request_id = form.get("request", [""])[0]
    action = form.get("action", [""])[0]
    _append_oauth_debug(
        {
            "event": "consent_post",
            **_request_peer(request),
            "content_type": request.headers.get("content-type", ""),
            "content_length": request.headers.get("content-length", ""),
            "body_length": len(body_bytes),
            "raw_body": body,
            "form_fields": sorted(form.keys()),
            "request_id": request_id,
            "action": action,
            "submitted_secret": submitted_secret,
            "submitted_secret_length": len(submitted_secret),
            "expected_secret": OAUTH_APPROVAL_SECRET,
            "expected_secret_length": len(OAUTH_APPROVAL_SECRET),
            "secret_match": _constant_time_text_equal(
                submitted_secret,
                OAUTH_APPROVAL_SECRET,
            ),
        }
    )


_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
for _host in _csv_env("MCP_ALLOWED_HOSTS"):
    _allowed_hosts.append(_host)
    if not _host.endswith(":*"):
        _allowed_hosts.append(f"{_host}:*")

_allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
_allowed_origins.extend(_csv_env("MCP_ALLOWED_ORIGINS"))


oauth_provider: LocalOAuthProvider | None = None
auth_settings: AuthSettings | None = None
if AUTH_MODE == "oauth":
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("MCP_PUBLIC_BASE_URL must be an https:// URL when MCP_AUTH_MODE=oauth")
    if len(OAUTH_APPROVAL_SECRET) < 16:
        raise RuntimeError(
            "MCP_OAUTH_APPROVAL_SECRET must contain at least 16 characters when MCP_AUTH_MODE=oauth"
        )

    oauth_provider = LocalOAuthProvider(
        public_base_url=PUBLIC_BASE_URL,
        approval_secret=OAUTH_APPROVAL_SECRET,
        state_file=OAUTH_STATE_FILE,
        scopes=OAUTH_SCOPES,
    )
    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(PUBLIC_BASE_URL),
        resource_server_url=AnyHttpUrl(PUBLIC_MCP_URL),
        required_scopes=OAUTH_SCOPES,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=OAUTH_SCOPES,
            default_scopes=OAUTH_SCOPES,
        ),
        revocation_options=RevocationOptions(enabled=True),
    )

mcp = FastMCP(
    "China Apps MCP Gateway",
    stateless_http=True,
    json_response=True,
    auth_server_provider=oauth_provider,
    auth=auth_settings,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    ),
)


@mcp.tool()
async def gateway_ping() -> dict[str, Any]:
    """Simple connectivity check for the China Apps MCP Gateway."""
    return {
        "ok": True,
        "service": "china-apps-mcp",
        "version": __version__,
        "auth_mode": AUTH_MODE,
        "auth_enabled": AUTH_MODE != "none",
    }


register_browser_tools(mcp)


if oauth_provider is not None:

    @mcp.custom_route("/oauth/consent", methods=["GET"])
    async def oauth_consent_get(request: Request):
        if OAUTH_DEBUG_LOG_SECRETS:
            _append_oauth_debug(
                {
                    "event": "consent_get",
                    **_request_peer(request),
                    "request_id": request.query_params.get("request", ""),
                }
            )
        response = await oauth_provider.consent_get(request)
        return _allow_consent_submit_script(response)

    @mcp.custom_route("/oauth/consent", methods=["POST"])
    async def oauth_consent_post(request: Request):
        await _log_consent_post(request)
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        request_id = form.get("request", [""])[0]

        retry = _retry_completed_consent(request_id)
        if retry is not None:
            if OAUTH_DEBUG_LOG_SECRETS:
                _append_oauth_debug(
                    {
                        "event": "consent_post_retry_redirect",
                        **_request_peer(request),
                        "request_id": request_id,
                        "status_code": retry.status_code,
                        "location": retry.headers.get("location", ""),
                    }
                )
            return retry

        response = await oauth_provider.consent_post(request)
        location = response.headers.get("location", "")
        if location and response.status_code in {301, 302, 307, 308}:
            response = RedirectResponse(
                location,
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
        if OAUTH_DEBUG_LOG_SECRETS:
            _append_oauth_debug(
                {
                    "event": "consent_post_result",
                    **_request_peer(request),
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "location": response.headers.get("location", ""),
                }
            )
        return _allow_consent_submit_script(response)

    async def _resource_metadata(_: Request) -> JSONResponse:
        assert auth_settings is not None
        return JSONResponse(
            oauth_resource_metadata(
                PUBLIC_MCP_URL,
                str(auth_settings.issuer_url),
                OAUTH_SCOPES,
            ),
            headers={"Cache-Control": "no-store"},
        )

    mcp.custom_route(
        "/mcp/.well-known/oauth-protected-resource",
        methods=["GET", "OPTIONS"],
    )(_resource_metadata)
    mcp.custom_route(
        "/.well-known/oauth-protected-resource/mcp",
        methods=["GET", "OPTIONS"],
    )(_resource_metadata)


class BearerAuthMiddleware:
    """Small ASGI middleware for the legacy/static token mode."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw = headers.get(b"authorization", b"").decode("latin-1")
        prefix = "Bearer "
        candidate = raw[len(prefix):] if raw.startswith(prefix) else ""

        if not candidate or not _constant_time_text_equal(candidate, self.token):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def health(_: Any) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "service": "china-apps-mcp",
            "version": __version__,
            "mcp_path": "/mcp",
            "auth_mode": AUTH_MODE,
            "auth_enabled": AUTH_MODE != "none",
        }
    )


_inner_mcp_app = mcp.streamable_http_app()
if AUTH_MODE == "token":
    mcp_http_app: Any = BearerAuthMiddleware(_inner_mcp_app, ACCESS_TOKEN)
else:
    mcp_http_app = _inner_mcp_app


@asynccontextmanager
async def lifespan(_: Starlette):
    await start_browser_bridge()
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        await stop_browser_bridge()


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/", app=mcp_http_app),
    ],
    lifespan=lifespan,
)


def main() -> None:
    if HOST not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "MCP_HOST must remain loopback-only. Use Tailscale Funnel for public HTTPS exposure."
        )
    if not 1 <= PORT <= 65535:
        raise RuntimeError("MCP_PORT must be between 1 and 65535")
    if AUTH_MODE == "token" and not ACCESS_TOKEN:
        raise RuntimeError("MCP_ACCESS_TOKEN is required when MCP_AUTH_MODE=token")

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
