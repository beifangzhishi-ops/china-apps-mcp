from __future__ import annotations

from urllib.parse import urlparse

from starlette.responses import HTMLResponse

from .oauth import LocalOAuthProvider


def consent_action_path(public_base_url: str) -> str:
    """Return the public consent POST path for an OAuth issuer with an optional path prefix."""
    path = urlparse(public_base_url).path.rstrip("/")
    return f"{path}/oauth/consent" if path else "/oauth/consent"


def _install_path_prefix_patch() -> None:
    """Patch only this prototype process so consent POST stays under the public prefix."""
    original = LocalOAuthProvider._render_consent

    def render_consent(self, request_id, pending, error: str = "") -> HTMLResponse:
        response = original(self, request_id, pending, error)
        desired = consent_action_path(self.public_base_url)
        if desired == "/oauth/consent":
            return response

        old = b'action="/oauth/consent"'
        new = f'action="{desired}"'.encode("utf-8")
        if old not in response.body:
            raise RuntimeError("Consent form action marker was not found; refusing unsafe path prototype startup.")

        response.body = response.body.replace(old, new, 1)
        response.headers["content-length"] = str(len(response.body))
        return response

    LocalOAuthProvider._render_consent = render_consent  # type: ignore[method-assign]


def main() -> None:
    _install_path_prefix_patch()
    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
