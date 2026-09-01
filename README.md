# China Apps MCP Gateway

A personal **Streamable HTTP MCP gateway** for exposing local China-app integrations to remote AI clients without opening Windows service ports directly to the Internet.

The first PoC is intentionally small: prove the path **ChatGPT/remote client -> HTTPS -> Tailscale Funnel -> localhost MCP**, then add platform adapters one by one.

## Current architecture

```text
Remote MCP client
      |
      | HTTPS /mcp
      v
Tailscale Funnel
      |
      v
127.0.0.1:8765/mcp
      |
      v
China Apps MCP Gateway
      |
      +-- gateway_ping
      +-- Bilibili (read-only PoC)
      +-- Zhihu       [planned]
      +-- Douyin      [planned]
      +-- QQ / OneBot [planned]
      +-- WeChat      [planned]
```

The Python service refuses non-loopback `MCP_HOST` values. Tailscale provides the public HTTPS edge.

## v0.1 tools

- `gateway_ping` - verifies MCP tool connectivity.
- `bilibili_get_video(bvid)` - reads public video metadata; no login required.
- `bilibili_account_status()` - reports only whether a local Bilibili cookie is configured.
- `bilibili_get_my_profile()` - reads the current account profile when `BILIBILI_COOKIE` is configured locally.

There are **no Bilibili write operations** in this PoC.

## Requirements

- Windows 10/11
- Python 3.11+
- Tailscale with Funnel available on the node
- PowerShell 5.1+ or PowerShell 7

## Quick start

Clone the repository on the always-on Windows machine:

```powershell
git clone https://github.com/beifangzhishi-ops/china-apps-mcp.git
cd china-apps-mcp
```

Initialize local secrets:

```powershell
.\scripts\init-env.ps1
```

This creates an ignored `.env` containing a random `MCP_ACCESS_TOKEN`. Do not commit or paste that token into chat.

Start the gateway in the foreground for initial debugging:

```powershell
.\scripts\start.ps1
```

Check local health from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Expected shape:

```json
{
  "ok": true,
  "service": "china-apps-mcp",
  "version": "0.1.0",
  "mcp_path": "/mcp",
  "auth_enabled": true
}
```

The MCP endpoint is:

```text
http://127.0.0.1:8765/mcp
```

## Expose only `/mcp` with Tailscale Funnel

Run an elevated PowerShell after the local health check succeeds:

```powershell
.\scripts\configure-funnel.ps1
```

The script maps only the `/mcp` public path to:

```text
http://127.0.0.1:8765/mcp
```

It does not open TCP 8765 in Windows Firewall.

Check status:

```powershell
.\scripts\status.ps1
```

Disable the public MCP route:

```powershell
.\scripts\disable-funnel.ps1
```

The Funnel mapping follows the same pattern as the existing CPA deployment, but uses a separate path and separate authentication secret.

## Bilibili account PoC

Public video metadata works with no account credentials.

For `bilibili_get_my_profile`, set `BILIBILI_COOKIE` only in the local `.env` file. The cookie is never returned by the MCP tools and `.env` is ignored by Git.

Do **not** send Bilibili cookies, `SESSDATA`, QR-login state, or browser profile files through chat.

The account integration is deliberately read-only until the gateway authentication and confirmation model is proven end to end.

## Authentication

`/mcp` supports an optional static bearer token using:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

`/health` stays local and does not require the token. The Funnel script exposes `/mcp`, not `/health`.

Important: ChatGPT Custom App authentication support can differ by product version/plan. If the ChatGPT configuration available to you requires OAuth instead of a static bearer token, the next step is to add an OAuth-compatible front end. Do not solve that by permanently exposing account-enabled MCP tools without authentication.

For a short **network-only** test with auth disabled, leave `BILIBILI_COOKIE` empty and remove/comment `MCP_ACCESS_TOKEN`, test only `gateway_ping` / public Bilibili metadata, then disable Funnel again.

## Repository layout

```text
.
├─ src/china_apps_mcp/
│  ├─ server.py
│  └─ adapters/
│     └─ bilibili.py
├─ scripts/
│  ├─ init-env.ps1
│  ├─ start.ps1
│  ├─ status.ps1
│  ├─ configure-funnel.ps1
│  └─ disable-funnel.ps1
├─ .env.example
├─ .gitignore
├─ pyproject.toml
└─ SECURITY.md
```

## Next milestones

1. Run the local MCP and verify `gateway_ping` with an MCP inspector/client.
2. Verify Tailscale Funnel preserves Streamable HTTP behavior.
3. Connect the remote MCP URL to ChatGPT Custom Apps and determine the available auth method.
4. Add OAuth if required before enabling account credentials.
5. Add a Bilibili login/cookie adapter with least-privilege read tools.
6. Add upstream MCP bridging for Zhihu, Douyin, QQ/OneBot, and WeChat.
7. Add per-platform read/write scopes and explicit confirmation for write operations.

See [SECURITY.md](SECURITY.md) before adding any account or write capability.
