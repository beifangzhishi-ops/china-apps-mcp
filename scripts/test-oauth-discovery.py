from __future__ import annotations

import json
import sys
from urllib.parse import urlparse

import httpx


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/test-oauth-discovery.py https://host.example/mcp")
        return 2

    mcp_url = sys.argv[1].rstrip("/")
    parsed = urlparse(mcp_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        print(f"MCP: {mcp_url}")
        response = client.get(mcp_url)
        print(f"Unauthenticated MCP status: {response.status_code}")
        print(f"WWW-Authenticate: {response.headers.get('www-authenticate', '')}")
        if response.status_code != 401:
            raise SystemExit("Expected OAuth-protected /mcp to return HTTP 401 without a token.")

        metadata_url = f"{base}/.well-known/oauth-authorization-server"
        response = client.get(metadata_url)
        response.raise_for_status()
        metadata = response.json()
        print("\nAuthorization server metadata:")
        print(json.dumps(metadata, ensure_ascii=False, indent=2))

        resource_candidates = [
            f"{mcp_url}/.well-known/oauth-protected-resource",
            f"{base}/.well-known/oauth-protected-resource/mcp",
            f"{base}/.well-known/oauth-protected-resource",
        ]
        resource_ok = False
        for url in resource_candidates:
            response = client.get(url)
            print(f"\nResource metadata candidate {url}: HTTP {response.status_code}")
            if response.is_success:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2))
                resource_ok = True
                break
        if not resource_ok:
            raise SystemExit("No OAuth protected-resource metadata endpoint succeeded.")

        for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            if not metadata.get(field):
                raise SystemExit(f"OAuth metadata is missing {field}.")

    print("\nOAuth discovery looks ready for ChatGPT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
