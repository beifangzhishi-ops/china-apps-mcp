# Security model

This gateway is intended for a personal Windows machine and should remain bound to `127.0.0.1`. Public HTTPS exposure is delegated to Tailscale Funnel.

## Rules

1. Never bind the MCP service directly to `0.0.0.0` or open TCP 8765 in Windows Firewall.
2. Never commit `.env`, cookies, browser profiles, OAuth tokens, QR-login state, logs containing credentials, or platform session data.
3. Keep `MCP_ACCESS_TOKEN` separate from any CPA/API keys and from platform credentials.
4. Start with read-only tools. Add write tools (message, comment, publish, delete, moderation) only after explicit per-tool authorization and confirmation behavior is designed.
5. Treat a Funnel URL as public. A random URL is not authentication.
6. If account credentials are configured, do not run the gateway without an authentication layer.
7. Prefer official OAuth/API integrations when a platform offers them. Cookie/browser automation is less stable and can trigger platform risk controls.

## Current PoC boundary

Version 0.1 exposes only:

- `gateway_ping`
- `bilibili_get_video` (public metadata)
- `bilibili_account_status` (boolean only)
- `bilibili_get_my_profile` (read-only; requires an optional local cookie)

The Bilibili cookie is read from the local process environment and is never returned by any tool.

## ChatGPT authentication note

The gateway implements optional static Bearer authentication at `/mcp`. ChatGPT Custom App authentication capabilities can vary by plan and product version. If the client you use requires OAuth rather than a static Bearer token, do not weaken the production gateway. Add an OAuth front end or compatible auth layer instead.

For a temporary connectivity-only test without authentication, keep `BILIBILI_COOKIE` empty and expose only the current read-only PoC tools, then disable the Funnel immediately after testing.
