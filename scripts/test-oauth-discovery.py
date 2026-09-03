from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

import httpx


def _authorization_metadata_candidates(issuer: str) -> list[str]:
    parsed = urlparse(issuer)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("Authorization server issuer must be an absolute HTTPS URL.")

    origin = f"{parsed.scheme}://{parsed.netloc}"
    issuer_path = parsed.path.rstrip("/")
    candidates = [f"{origin}/.well-known/oauth-authorization-server{issuer_path}"]
    alias = f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"
    if alias not in candidates:
        candidates.append(alias)
    return candidates


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/test-oauth-discovery.py https://host.example[/prefix]/mcp")
        return 2

    mcp_url = sys.argv[1].rstrip("/")
    parsed = urlparse(mcp_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("MCP URL must be an absolute HTTPS URL.")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        print(f"MCP: {mcp_url}")
        response = client.get(mcp_url)
        print(f"Unauthenticated MCP status: {response.status_code}")
        challenge = response.headers.get("www-authenticate", "")
        print(f"WWW-Authenticate: {challenge}")
        if response.status_code != 401:
            raise SystemExit("Expected OAuth-protected MCP to return HTTP 401 without a token.")

        resource_candidates: list[str] = []
        match = re.search(r'resource_metadata="([^"]+)"', challenge)
        if match:
            resource_candidates.append(match.group(1))

        resource_path = parsed.path.rstrip("/")
        for candidate in (
            f"{origin}/.well-known/oauth-protected-resource{resource_path}",
            f"{mcp_url}/.well-known/oauth-protected-resource",
            f"{origin}/.well-known/oauth-protected-resource",
        ):
            if candidate not in resource_candidates:
                resource_candidates.append(candidate)

        resource_metadata = None
        resource_metadata_url = ""
        for url in resource_candidates:
            response = client.get(url)
            print(f"\nResource metadata candidate {url}: HTTP {response.status_code}")
            if response.is_success:
                resource_metadata = response.json()
                resource_metadata_url = url
                print(json.dumps(resource_metadata, ensure_ascii=False, indent=2))
                break
        if resource_metadata is None:
            raise SystemExit("No OAuth protected-resource metadata endpoint succeeded.")
        if resource_metadata.get("resource") != mcp_url:
            raise SystemExit("Protected-resource metadata does not identify the requested MCP URL.")

        authorization_servers = resource_metadata.get("authorization_servers") or []
        if len(authorization_servers) != 1:
            raise SystemExit("Expected exactly one OAuth authorization server for this CAM resource.")
        issuer = str(authorization_servers[0])

        metadata = None
        metadata_url = ""
        for url in _authorization_metadata_candidates(issuer):
            response = client.get(url)
            print(f"\nAuthorization metadata candidate {url}: HTTP {response.status_code}")
            if response.is_success:
                metadata = response.json()
                metadata_url = url
                print(json.dumps(metadata, ensure_ascii=False, indent=2))
                break
        if metadata is None:
            raise SystemExit("No OAuth authorization-server metadata endpoint succeeded.")

        if metadata.get("issuer") != issuer:
            raise SystemExit("Authorization metadata issuer does not exactly match protected-resource metadata.")
        for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            if not metadata.get(field):
                raise SystemExit(f"OAuth metadata is missing {field}.")
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise SystemExit("OAuth metadata must advertise PKCE S256 support.")

        expected_prefix = issuer.rstrip("/") + "/"
        for field in (
            "authorization_endpoint",
            "token_endpoint",
            "registration_endpoint",
            "revocation_endpoint",
        ):
            value = str(metadata.get(field) or "")
            if value and not value.startswith(expected_prefix):
                raise SystemExit(f"{field} escaped the OAuth issuer path prefix: {value}")

        print(f"\nProtected-resource metadata used: {resource_metadata_url}")
        print(f"Authorization metadata used:      {metadata_url}")

    print("\nOAuth discovery looks ready for ChatGPT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
