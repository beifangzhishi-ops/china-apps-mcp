from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__
from .adapters.bilibili import register_bilibili_tools

load_dotenv()

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8765"))
ACCESS_TOKEN = os.getenv("MCP_ACCESS_TOKEN", "").strip()


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
for _host in _csv_env("MCP_ALLOWED_HOSTS"):
    _allowed_hosts.append(_host)
    if not _host.endswith(":*"):
        _allowed_hosts.append(f"{_host}:*")

_allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
_allowed_origins.extend(_csv_env("MCP_ALLOWED_ORIGINS"))

mcp = FastMCP(
    "China Apps MCP Gateway",
    stateless_http=True,
    json_response=True,
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
        "auth_enabled": bool(ACCESS_TOKEN),
    }


register_bilibili_tools(mcp)


class BearerAuthMiddleware:
    """Small ASGI middleware that protects the MCP endpoint without buffering streams."""

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
            "auth_enabled": bool(ACCESS_TOKEN),
        }
    )


mcp_http_app = BearerAuthMiddleware(mcp.streamable_http_app(), ACCESS_TOKEN)


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

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
