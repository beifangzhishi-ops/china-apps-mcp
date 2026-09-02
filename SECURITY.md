# Security model

This gateway is intended for one user's Windows machine. The MCP service stays bound to `127.0.0.1:8765`; Tailscale Funnel provides the remote HTTPS endpoint used by ChatGPT.

## Core rules

1. Never bind the MCP service directly to `0.0.0.0` or expose TCP 8765/8766 in Windows Firewall.
2. Treat a Funnel URL as public. Use OAuth before exposing tools that can read logged-in account data.
3. Never commit `.env`, `.state`, cookies, OAuth tokens, browser credentials, or logs containing secrets.
4. Keep browser capabilities read-oriented. Do not add purchase, publish, message, delete, moderation, or arbitrary JavaScript execution without a separate permission/confirmation design.
5. Prefer official APIs when a site offers an appropriate stable API. Browser extraction is a fallback and may trigger site risk controls.

## Edge browser bridge (0.3+)

The browser bridge is deliberately separate from the public MCP HTTP listener:

```text
Edge extension <-> ws://127.0.0.1:8766 <-> China Apps MCP
ChatGPT        <-> Funnel / 127.0.0.1:8765 <-> China Apps MCP
```

The extension is loaded into a normal Microsoft Edge profile. It does not launch Edge, enable Chrome DevTools remote debugging, use CDP, or use Playwright.

The bridge listener is hard-coded to `127.0.0.1:8766`. Do not add that port to Funnel configuration.

The WebSocket handler rejects browser origins that are not extension origins. A local native process can still connect without an Origin header, so the loopback bridge is not a security boundary against malicious software already running as the same Windows user.

### Browser permissions

The Edge extension requests `tabs` and `scripting`, plus host permissions for a fixed set of China-app domains. It does not request `<all_urls>`.

The extension validates destinations before opening or reading tabs. The MCP independently validates returned page URLs and links again before returning them to ChatGPT.

Tabs outside the allowlist are represented only as blocked tab IDs. Their title, URL, and page text are not returned to the MCP client.

`BROWSER_ALLOWED_HOSTS` only expands the MCP-side allowlist. It does not grant new Edge extension host permissions; changing extension host permissions requires an explicit manifest change/reload.

## OAuth model

OAuth mode is designed for a single-user personal gateway:

- Authorization Code flow with PKCE S256 is handled by the MCP Python SDK.
- Dynamic Client Registration is enabled for ChatGPT.
- OAuth client/token state is stored only in the ignored `.state/oauth-state.json` file.
- Human approval uses `MCP_OAUTH_APPROVAL_SECRET` on the local approval page.

The approval secret is not an MCP token and should never be sent in chat or passed as a tool argument.

## No-auth boundary

`MCP_AUTH_MODE=none` is suitable only for connectivity experiments with public/non-account data.

Once `BROWSER_ENABLED=1` is used with logged-in Edge pages, `MCP_AUTH_MODE=oauth` is the intended deployment mode for the remote ChatGPT connection.

## Current browser boundary

Version 0.3 exposes browser tools for:

- bridge status
- listing allowed tabs
- opening an allowed URL
- reading visible text and links from an allowed page
- temporary-tab reads

There is no generic JavaScript execution, direct cookie export, password access, form submission, purchasing, commenting, messaging, publishing, or delete operation.
