from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__
from .adapters.bilibili import register_bilibili_tools
from .oauth import LocalOAuthProvider, oauth_resource_metadata

load_dotenv()

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


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


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


register_bilibili_tools(mcp)


if oauth_provider is not None:

    @mcp.custom_route("/oauth/consent", methods=["GET"])
    async def oauth_consent_get(request: Request):
        return await oauth_provider.consent_get(request)

    @mcp.custom_route("/oauth/consent", methods=["POST"])
    async def oauth_consent_post(request: Request):
        return await oauth_provider.consent_post(request)

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

    # mcp 1.x advertises resource_server_url + '/.well-known/oauth-protected-resource'.
    # Keep that compatibility path and the RFC 9728 path-derived form as aliases.
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

        if not candidate or not hmac.compare_digest(candidate, self.token):
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
    async with mcp.session_manager.run():
        yield


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
    if AUTH_MODE == "token" and not ACCESS_TOKEN:
        raise RuntimeError("MCP_ACCESS_TOKEN is required when MCP_AUTH_MODE=token")

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
