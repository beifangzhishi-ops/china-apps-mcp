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

        resource_path = parsed.path.rstrip("/")
        resource_candidates = [
            f"{base}/.well-known/oauth-protected-resource{resource_path}",
            f"{mcp_url}/.well-known/oauth-protected-resource",
            f"{base}/.well-known/oauth-protected-resource",
        ]
        resource_metadata = None
        for url in resource_candidates:
            response = client.get(url)
            print(f"\nResource metadata candidate {url}: HTTP {response.status_code}")
            if response.is_success:
                resource_metadata = response.json()
                print(json.dumps(resource_metadata, ensure_ascii=False, indent=2))
                break
        if resource_metadata is None:
            raise SystemExit("No OAuth protected-resource metadata endpoint succeeded.")

        for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            if not metadata.get(field):
                raise SystemExit(f"OAuth metadata is missing {field}.")

        issuer = metadata.get("issuer")
        if issuer not in resource_metadata.get("authorization_servers", []):
            raise SystemExit(
                "Protected-resource authorization_servers must contain the exact OAuth issuer, "
                "including any trailing slash."
            )
        if resource_metadata.get("resource") != mcp_url:
            raise SystemExit("Protected-resource metadata does not identify the requested MCP URL.")
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise SystemExit("OAuth metadata must advertise PKCE S256 support.")

    print("\nOAuth discovery looks ready for ChatGPT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
