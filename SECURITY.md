# Security model

This gateway is intended for a personal Windows machine and must remain bound to `127.0.0.1`. Public HTTPS exposure is delegated to Tailscale Funnel.

## Rules

1. Never bind the MCP service directly to `0.0.0.0` or open TCP 8765 in Windows Firewall.
2. Never commit `.env`, `.state/oauth-state.json`, cookies, browser profiles, OAuth tokens, QR-login state, logs containing credentials, or platform session data.
3. Keep `MCP_ACCESS_TOKEN`, `MCP_OAUTH_APPROVAL_SECRET`, CPA/API keys, and platform credentials separate.
4. Treat a Funnel URL as public. A random hostname is not authentication.
5. Use `MCP_AUTH_MODE=oauth` before enabling account cookies or any write-capable platform tools.
6. `MCP_AUTH_MODE=none` is only for a short connectivity PoC with public/read-only data.
7. Start with read-only tools. Add message/comment/publish/delete/moderation actions only after per-tool scopes and explicit confirmation behavior are designed.
8. Prefer official OAuth/API integrations when a platform offers them. Cookie/browser automation is less stable and can trigger platform risk controls.

## OAuth model

OAuth mode is designed for a single-user personal gateway:

- Authorization Code flow with PKCE S256 is handled by the MCP Python SDK.
- Dynamic Client Registration is enabled so ChatGPT can register itself.
- Access tokens expire after one hour.
- Refresh tokens expire after 30 days and rotate when refreshed.
- Client registration and long-lived token state are stored only in the ignored `.state/oauth-state.json` file.
- Short-lived authorization codes and pending approvals stay only in memory.
- The human approval page requires `MCP_OAUTH_APPROVAL_SECRET` and invalidates a pending request after five failed attempts.
- The approval page sets no cookies and sends `Cache-Control: no-store`, a restrictive CSP, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`.

The approval secret is not an MCP token and should never be sent in chat or passed as a tool argument. Enter it only into the browser approval page served by your own Funnel hostname.

## Funnel exposure

OAuth requires more than `/mcp`; discovery and authorization endpoints must be reachable on the same HTTPS origin. The Funnel helper therefore adds a root fallback in addition to `/mcp`.

This means routes such as `/health`, `/.well-known/...`, `/authorize`, `/token`, `/register`, `/revoke`, and `/oauth/consent` are Internet-reachable through Funnel. The MCP endpoint itself remains OAuth-protected, and authorization still requires the local approval secret.

The disable script removes only the root and `/mcp` routes owned by this gateway. Do not use `tailscale funnel reset` unless you also intend to remove unrelated routes such as CPA `/v1`.

## Current PoC boundary

Version 0.1 exposes only:

- `gateway_ping`
- `bilibili_get_video` (public metadata)
- `bilibili_account_status` (boolean only)
- `bilibili_get_my_profile` (read-only; requires an optional local cookie)

The Bilibili cookie is read from the local process environment and is never returned by any tool.
