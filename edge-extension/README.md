# China Apps Browser Bridge for Microsoft Edge

This unpacked Manifest V3 extension lets China Apps MCP read approved China-app pages from a normal Microsoft Edge session.

It does **not** launch Edge, enable remote debugging, use CDP, or create a separate browser profile. Start Edge from your normal desktop/taskbar shortcut and keep using your normal logged-in profile.

## Install

1. Pull the repository on the Windows machine running China Apps MCP.
2. Open `edge://extensions/` in Edge.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select the repository folder `edge-extension`.
6. Keep the extension enabled.
7. Set `BROWSER_ENABLED=1` in the local `.env` and restart China Apps MCP.

The extension connects to `ws://127.0.0.1:8766`. The bridge is loopback-only and is separate from the MCP/Tailscale Funnel listener on port 8765.

0.3.1 版本增加了 `alarms` 权限，用于在 Manifest V3 service worker 休眠后唤醒重连。更新扩展文件后，请在 `edge://extensions/` 中点击“重新加载”。

## Permissions

The extension requests `tabs` and `scripting`, plus host permissions only for the China-app domains listed in `manifest.json`.

It does not request `<all_urls>` and it does not read cookies directly. Page reads use `chrome.scripting.executeScript()` inside an allowed tab and return visible text plus allowed links to the local MCP bridge.

Both the extension and the MCP server validate the destination host. Tabs outside the allowlist are represented only as blocked tab IDs; their title, URL, and page contents are not returned to ChatGPT.

## Current bridge actions

- list tabs
- open an allowed URL in a normal Edge tab
- snapshot visible page text and links
- close a temporary tab used for one-shot reads

No generic JavaScript execution, form submission, purchasing, messaging, or publishing action is exposed.
