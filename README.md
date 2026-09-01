# China Apps MCP Gateway

A personal **Streamable HTTP MCP gateway** for exposing local China-app integrations to ChatGPT through Tailscale Funnel while the Python service stays bound to `127.0.0.1`.

## Current status

The end-to-end path is working:

```text
ChatGPT Custom MCP
      |
      | HTTPS + MCP
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
      +-- Bilibili public/read-only PoC
      +-- Zhihu       [planned]
      +-- Douyin      [planned]
      +-- QQ / OneBot [planned]
      +-- WeChat      [planned]
```

The gateway supports three authentication modes:

```text
MCP_AUTH_MODE=none   # temporary network PoC only
MCP_AUTH_MODE=token  # static Bearer token for scripts/debugging
MCP_AUTH_MODE=oauth  # recommended for ChatGPT Custom MCP
```

OAuth mode uses Authorization Code + PKCE (S256), Dynamic Client Registration, refresh tokens, protected-resource metadata, and a local human approval secret.

## Current tools

- `gateway_ping` - verifies MCP connectivity and reports the active auth mode.
- `bilibili_get_video(bvid)` - reads public Bilibili video metadata.
- `bilibili_account_status()` - reports only whether a local Bilibili cookie is configured.
- `bilibili_get_my_profile()` - reads the current account profile when `BILIBILI_COOKIE` is configured locally.

There are currently **no Bilibili write operations**.

## Requirements

- Windows 10/11
- Python 3.11+
- Tailscale with Funnel available
- PowerShell 5.1+ or PowerShell 7

Python dependencies are installed automatically by the launcher from `pyproject.toml`.

## Quick start

```powershell
git clone https://github.com/beifangzhishi-ops/china-apps-mcp.git
cd china-apps-mcp
```

The repository root contains:

```text
启动MCP.cmd
停止MCP.cmd
```

Double-click `启动MCP.cmd` to create/update `.venv`, install the project, start it in the background, and wait for `/health`.

Double-click `停止MCP.cmd` to stop only the process recorded in `.state/mcp.pid`.

The `.cmd` bodies are intentionally ASCII-only and should remain UTF-8 **without BOM**. The PowerShell launcher scripts are also kept ASCII-only where practical for Windows PowerShell 5.1 compatibility.

## OAuth setup for ChatGPT

### 1. Pull the latest code

```powershell
git pull
```

### 2. Enable OAuth locally

For the current Funnel host:

```powershell
.\scripts\enable-oauth.ps1 -PublicBaseUrl https://cpa-node.tail7c23f0.ts.net
```

The helper updates only the local ignored `.env` and:

- sets `MCP_AUTH_MODE=oauth`;
- sets `MCP_PUBLIC_BASE_URL`;
- adds the Funnel hostname to `MCP_ALLOWED_HOSTS`;
- creates a random `MCP_OAUTH_APPROVAL_SECRET` if one is missing;
- keeps OAuth clients/tokens in `.state/oauth-state.json` so authorization survives normal restarts.

如果本机授权密钥曾经泄露，使用下面的命令轮换密钥；命令不会打印密钥，也不会删除 OAuth 状态：

```powershell
.\scripts\enable-oauth.ps1 -PublicBaseUrl https://cpa-node.tail7c23f0.ts.net -RotateSecret
```

Do **not** paste `MCP_OAUTH_APPROVAL_SECRET`, `MCP_ACCESS_TOKEN`, OAuth tokens, or platform cookies into chat.

### 3. Restart the gateway

```text
停止MCP.cmd
启动MCP.cmd
```

Check local status:

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
  "auth_mode": "oauth",
  "auth_enabled": true
}
```

### 4. Expose MCP + OAuth routes through Funnel

Run an elevated PowerShell:

```powershell
.\scripts\configure-funnel.ps1
```

The script keeps an explicit `/mcp` route and also adds a root fallback for the OAuth endpoints on the same HTTPS origin. More-specific existing Funnel routes such as CPA `/v1` remain more specific than the root fallback.

OAuth needs these public routes:

```text
/.well-known/oauth-authorization-server
/.well-known/oauth-protected-resource
/authorize
/token
/register
/revoke
/oauth/consent
/mcp
```

The Python process itself still listens only on `127.0.0.1:8765`; no Windows firewall port needs to be opened.

### 5. Smoke-test OAuth discovery

```powershell
.\.venv\Scripts\python.exe .\scripts\test-oauth-discovery.py https://cpa-node.tail7c23f0.ts.net/mcp
```

A healthy OAuth deployment should show:

- unauthenticated `/mcp` returns `401`;
- `WWW-Authenticate` is present;
- OAuth authorization-server metadata is reachable;
- protected-resource metadata is reachable;
- `authorization_endpoint`, `token_endpoint`, and `registration_endpoint` are advertised.

### 6. Switch the ChatGPT plugin to OAuth

In ChatGPT developer mode, edit/recreate the Custom MCP with:

```text
Name: China Apps MCP
Server URL: https://cpa-node.tail7c23f0.ts.net/mcp
Authentication: OAuth
```

ChatGPT should discover the OAuth endpoints and open the gateway approval page in the browser. Copy `MCP_OAUTH_APPROVAL_SECRET` from your **local `.env` file** into that browser page. The secret is for the human approval page only; it is never sent as an MCP tool argument.

After approval, test:

```text
@China Apps MCP call gateway_ping
```

The result should report:

```json
{
  "auth_mode": "oauth",
  "auth_enabled": true
}
```

## Static token mode

For scripts or local debugging, set:

```text
MCP_AUTH_MODE=token
MCP_ACCESS_TOKEN=<high-entropy-secret>
```

Then `/mcp` accepts:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

The old remote smoke test remains available:

```powershell
.\.venv\Scripts\python.exe .\scripts\test-remote.py https://cpa-node.tail7c23f0.ts.net/mcp
```

## No-auth mode

`MCP_AUTH_MODE=none` exists only for connectivity testing. Do not use it after adding account cookies or write tools.

## Bilibili account PoC

Public video metadata works without account credentials.

Only after OAuth is verified end-to-end should `BILIBILI_COOKIE` be set locally for `bilibili_get_my_profile`. The cookie is never returned by the tools and `.env` is ignored by Git.

Do **not** send Bilibili cookies, `SESSDATA`, QR-login state, browser profiles, or OAuth secrets through chat.

## Runtime and secret files

These are ignored by Git:

```text
.env
.state/mcp.pid
.state/oauth-state.json
logs/gateway.out.log
logs/gateway.err.log
cookies/
profiles/
```

## Funnel management

Check:

```powershell
.\scripts\status.ps1
```

Disable only the China Apps MCP Funnel routes:

```powershell
.\scripts\disable-funnel.ps1
```

The disable script intentionally avoids `tailscale funnel reset` so unrelated routes such as CPA `/v1` are not cleared.

## Repository layout

```text
.
├─ src/china_apps_mcp/
│  ├─ server.py
│  ├─ oauth.py
│  └─ adapters/
│     └─ bilibili.py
├─ scripts/
│  ├─ init-env.ps1
│  ├─ enable-oauth.ps1
│  ├─ start.ps1
│  ├─ stop.ps1
│  ├─ status.ps1
│  ├─ configure-funnel.ps1
│  ├─ disable-funnel.ps1
│  ├─ test-remote.py
│  └─ test-oauth-discovery.py
├─ 启动MCP.cmd
├─ 停止MCP.cmd
├─ .env.example
├─ .gitignore
├─ pyproject.toml
└─ SECURITY.md
```

## Next milestones

1. Verify ChatGPT OAuth login end-to-end.
2. Enable the Bilibili account cookie only after OAuth is confirmed.
3. Add Bilibili search, subtitles, favorites, history, and comments as read-only tools first.
4. Add separate write scopes and explicit confirmation before any like/comment/publish/message operation.
5. Add Zhihu, Douyin, QQ/OneBot, and WeChat adapters behind the same gateway.

See [SECURITY.md](SECURITY.md) before adding account or write capabilities.
