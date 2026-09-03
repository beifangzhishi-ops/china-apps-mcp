# China Apps MCP Gateway

A personal Streamable HTTP MCP gateway that lets ChatGPT read approved pages from the user's normal Microsoft Edge session while the Python service stays on a Windows machine.

## Architecture

```text
ChatGPT Web
    |
    | HTTPS + MCP
    v
Tailscale Funnel
    |
    v
127.0.0.1:8765  China Apps MCP
    |
    +-- browser_* tools
            |
            | local WebSocket only
            v
       127.0.0.1:8766
            |
            v
  China Apps Browser Bridge
        Edge extension
            |
            v
  normal Microsoft Edge tabs
```

Version 0.3 uses a normal Microsoft Edge session. The MCP does not launch Chrome/Edge, does not use Playwright/CDP, does not require `--remote-debugging-port`, and does not maintain a separate browser profile.

You open Edge normally from the desktop/taskbar and sign in to websites yourself. The unpacked extension reads approved pages from that existing session.

There are no site-specific API adapters or cookie integrations. Public information can be obtained through normal web search; this MCP exists specifically for information that benefits from the user's logged-in browser session.

## Current MCP tools

- `gateway_ping()`
- `browser_status()`
- `browser_start()` - compatibility/status check; it does not launch Edge
- `browser_open(url)`
- `browser_read_page(url="", tab_id=0, max_chars=30000)`
- `browser_list_pages()`
- `browser_stop()` - compatibility no-op; Edge remains user-owned

Browser tools are read-oriented. There is no generic JavaScript execution, form submission, purchasing, commenting, messaging, publishing, or delete action.

## Requirements

- Windows 10/11
- Python 3.11+
- Microsoft Edge
- PowerShell 5.1+ or PowerShell 7
- Tailscale Funnel for remote ChatGPT MCP access

## 项目协作规则

对项目进行修改后，必须提交相关改动并推送到远端仓库。

## Quick start

```powershell
git clone https://github.com/beifangzhishi-ops/china-apps-mcp.git
cd china-apps-mcp
```

Double-click:

```text
启动MCP.cmd
```

The launcher creates/updates `.venv`, installs the project, starts the gateway in the background, and waits for `/health`.

To stop the gateway:

```text
停止MCP.cmd
```

## Enable the Edge browser bridge

Set this in the local ignored `.env`:

```text
BROWSER_ENABLED=1
```

Then restart the MCP gateway.

The browser bridge listens only on:

```text
ws://127.0.0.1:8766
```

Port 8766 is not the public MCP endpoint and should not be exposed through Tailscale Funnel or Windows Firewall.

### Install the Edge extension

1. Open Edge normally.
2. Visit `edge://extensions/`.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select this repository's `edge-extension` directory.
6. Keep **China Apps Browser Bridge** enabled.

The extension reconnects to the local bridge automatically. No special Edge shortcut or command-line flags are needed.

扩展 0.3.1 起使用 `chrome.alarms` 在 Manifest V3 service worker 休眠后继续唤醒重连。更新仓库后，需要在 `edge://extensions/` 中对已加载的解压扩展点击一次“重新加载”，使新版 `manifest.json` 和后台脚本生效。

若扩展显示“不活动”，先用 `netstat.exe -ano | findstr ":8766"` 检查本地监听和 `ESTABLISHED` 连接。受限 PowerShell 中 `Get-NetTCPConnection` 可能无输出，不能据此单独判断服务未运行。bridge 只有在客户端发送扩展 `hello` 后才报告 `extension_connected=true`，普通的手动 WebSocket 测试不会再替换真实扩展连接。

See `edge-extension/README.md` for the permission model.

### Browser allowlist

The built-in list currently includes Taobao/Tmall, JD, Ctrip, Dianping/Meituan, Zhihu, Douyin, QQ/WeChat, and Bilibili domains.

The extension itself has fixed host permissions in `edge-extension/manifest.json`. The MCP independently validates URLs and returned links a second time before returning data to ChatGPT.

Tabs outside the allowlist are not exposed with title, URL, or page contents.

## OAuth setup for ChatGPT

The gateway supports:

```text
MCP_AUTH_MODE=none
MCP_AUTH_MODE=token
MCP_AUTH_MODE=oauth
```

`oauth` is the recommended mode for a ChatGPT Custom MCP that can read account-authenticated browser pages.

For the current Funnel host:

```powershell
.\scripts\enable-oauth.ps1 -PublicBaseUrl https://your-node.your-tailnet.ts.net:8443
```

CAM 使用专用 HTTPS `8443` 端口；配置 Funnel 后，公网基地址和 OAuth MCP 地址都应包含 `:8443`。

Then restart the gateway and configure Funnel:

```powershell
.\scripts\configure-funnel.ps1
```

The ChatGPT MCP server URL is:

```text
https://your-node.your-tailnet.ts.net:8443/mcp
```

The OAuth approval secret is local-only. Do not paste `MCP_OAUTH_APPROVAL_SECRET`, `MCP_ACCESS_TOKEN`, OAuth tokens, cookies, or browser credentials into chat.

Useful local checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
.\.venv\Scripts\python.exe .\scripts\test-oauth-flow.py
```

## Runtime and secret files

These are ignored by Git:

```text
.env
.state/
logs/
cookies/
profiles/
```

The browser bridge does not use `profiles/`; the ignore remains only for older/local experiments.

## Repository layout

```text
.
├─ edge-extension/
│  ├─ manifest.json
│  ├─ background.js
│  └─ README.md
├─ src/china_apps_mcp/
│  ├─ server.py
│  ├─ oauth.py
│  └─ adapters/
│     └─ browser.py
├─ scripts/
├─ tests/
├─ 启动MCP.cmd
├─ 停止MCP.cmd
├─ .env.example
├─ pyproject.toml
└─ SECURITY.md
```

## Next milestones

1. Improve snapshots for SPA/lazy-loaded pages without adding arbitrary JavaScript execution.
2. Add browser-only semantic helpers when repeated workflows justify them, while keeping the Edge session as the single source of login state.
3. Keep write actions out of scope until explicit confirmation/scoping is designed.

See `SECURITY.md` before expanding browser permissions or account-capable tools.
