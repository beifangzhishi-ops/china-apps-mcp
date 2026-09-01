from __future__ import annotations

import base64
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import dotenv_values


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _safe_oauth_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = payload.get("error", "unknown_error")
    description = payload.get("error_description", "")
    return f"HTTP {response.status_code}: {error} {description}".strip()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env = dotenv_values(repo_root / ".env")
    approval_secret = str(env.get("MCP_OAUTH_APPROVAL_SECRET") or "")
    public_base_url = str(env.get("MCP_PUBLIC_BASE_URL") or "").rstrip("/")
    public_mcp_url = f"{public_base_url}/mcp"
    local_host = str(env.get("MCP_HOST") or "127.0.0.1")
    local_port = int(str(env.get("MCP_PORT") or "8765"))
    local_base_url = f"http://{local_host}:{local_port}"

    if len(approval_secret) < 16:
        raise SystemExit("MCP_OAUTH_APPROVAL_SECRET is missing or too short.")
    if not public_base_url.startswith("https://"):
        raise SystemExit("MCP_PUBLIC_BASE_URL must be an HTTPS origin.")

    state_path = repo_root / str(env.get("MCP_OAUTH_STATE_FILE") or ".state/oauth-state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    clients = [
        item
        for item in state.get("clients", [])
        if item.get("client_name") == "ChatGPT"
        and item.get("client_id")
        and item.get("client_secret")
        and item.get("redirect_uris")
    ]
    if not clients:
        raise SystemExit("No persisted ChatGPT OAuth client was found. Connect once so DCR can register it.")

    client_info = clients[-1]
    client_id = client_info["client_id"]
    client_secret = client_info["client_secret"]
    redirect_uri = str(client_info["redirect_uris"][0])
    verifier = secrets.token_urlsafe(48)
    expected_state = secrets.token_urlsafe(24)
    access_token = ""
    refresh_token = ""

    with httpx.Client(base_url=local_base_url, timeout=15.0, follow_redirects=False) as client:
        authorize_response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "mcp",
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "resource": public_mcp_url,
                "state": expected_state,
            },
        )
        if authorize_response.status_code != 302:
            raise SystemExit(f"Authorization request failed: HTTP {authorize_response.status_code}")
        consent_url = urlparse(authorize_response.headers["location"])
        request_id = parse_qs(consent_url.query).get("request", [""])[0]
        if not request_id:
            raise SystemExit("Authorization response did not contain a consent request ID.")
        print("Authorization endpoint: OK")

        consent_response = client.post(
            "/oauth/consent",
            data={
                "request": request_id,
                "secret": approval_secret,
                "action": "approve",
            },
        )
        if consent_response.status_code != 302:
            raise SystemExit(f"Consent failed: HTTP {consent_response.status_code}")
        callback_url = urlparse(consent_response.headers["location"])
        callback_params = parse_qs(callback_url.query)
        authorization_code = callback_params.get("code", [""])[0]
        returned_state = callback_params.get("state", [""])[0]
        if not authorization_code or returned_state != expected_state:
            raise SystemExit("Consent callback did not return the expected code and state.")
        print("Consent callback: OK")

        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "resource": public_mcp_url,
            },
        )
        if not token_response.is_success:
            raise SystemExit(f"Token exchange failed: {_safe_oauth_error(token_response)}")
        token_payload = token_response.json()
        access_token = str(token_payload.get("access_token") or "")
        refresh_token = str(token_payload.get("refresh_token") or "")
        if not access_token or not refresh_token:
            raise SystemExit("Token endpoint did not return both access and refresh tokens.")
        print("Token exchange: OK")

        try:
            # Streamable HTTP may keep a successful GET open as an event stream.
            # Inspect the status without buffering the long-lived response body.
            with client.stream(
                "GET",
                "/mcp",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json, text/event-stream",
                },
            ) as mcp_response:
                if mcp_response.status_code == 401:
                    raise SystemExit("The issued access token was rejected by /mcp.")
                print(f"Bearer-protected MCP: OK (HTTP {mcp_response.status_code})")
        finally:
            for token, hint in (
                (access_token, "access_token"),
                (refresh_token, "refresh_token"),
            ):
                revoke_response = client.post(
                    "/revoke",
                    data={
                        "token": token,
                        "token_type_hint": hint,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                if not revoke_response.is_success:
                    raise SystemExit(f"Token revocation failed: HTTP {revoke_response.status_code}")
            print("Token revocation: OK")

    print("Local OAuth authorization-code flow is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
