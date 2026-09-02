# BrowserRuntime 0.2.1: attach-only Chrome

BrowserRuntime no longer launches Chrome through Playwright. The user starts a dedicated Chrome profile first, then the MCP attaches to that already-running browser through the loopback Chrome DevTools Protocol (CDP) endpoint.

## Why

Launching Chrome with Playwright adds automation-oriented default command-line switches. Some sites, especially Google sign-in, treat that browser as unsupported or automated. Attach-only mode keeps Chrome startup under the user's control and lets Playwright act only as the CDP client.

This does not make automation undetectable. Sites can still use other signals and may trigger risk controls. The goal is to avoid unnecessary Playwright launch flags while keeping a normal persistent Chrome profile.

## Start order

1. Pull the latest repository.
2. Enable the browser runtime in `.env`:

```text
BROWSER_ENABLED=1
BROWSER_CDP_URL=http://127.0.0.1:9222
BROWSER_PROFILE_DIR=profiles/chrome
```

3. Double-click `启动浏览器.cmd`.
4. Log in manually to the sites you want to use in that Chrome window.
5. Start/restart the MCP with `启动MCP.cmd`.
6. Call `browser_status`, then `browser_start` from ChatGPT. `browser_start` now means "attach", not "launch".

The default launcher starts Chrome with only the parameters needed for a separate persistent profile and loopback remote debugging:

```text
--remote-debugging-address=127.0.0.1
--remote-debugging-port=9222
--user-data-dir=<repo>\profiles\chrome
```

It does not add `--no-sandbox`, `--enable-automation`, or Playwright's other browser-launch defaults.

## Tool behavior

- `browser_status()` reports whether the local CDP endpoint is ready and whether MCP is attached.
- `browser_start()` attaches to the user-started Chrome.
- `browser_open(url)` opens an allowed URL in a new tab.
- `browser_read_page(url=...)` uses a temporary tab for the read and closes it afterward, preventing concurrent GPT calls from navigating each other's tabs.
- `browser_read_page()` without a URL reads the newest already-open tab whose URL is on the allowlist.
- `browser_list_pages()` lists tabs and marks which ones are on allowed hosts.
- `browser_stop()` detaches MCP only. It intentionally does not close Chrome.

Navigation is validated both before and after `goto()`. Page text and returned links are exposed only for allowlisted hosts.

## Login state

The Chrome profile under `profiles/chrome` is ignored by Git and persists cookies, local storage and other site state. Reuse the same profile every time. The profile created by the earlier Playwright-launch implementation can be reused; do not delete it if it contains active logins.

## Security boundary

Keep the CDP endpoint bound to loopback only. Never expose port 9222 through Tailscale Funnel, Windows Firewall, LAN forwarding, or a public reverse proxy.

The MCP endpoint itself may still be public through Funnel. OAuth remains the recommended authentication mode before exposing browser/account tools to ChatGPT.
