from __future__ import annotations


def main() -> None:
    # Path-aware consent handling now lives in LocalOAuthProvider itself. Keep the
    # prototype entry point only so the isolated 8767 launcher remains usable while
    # production CAM is migrated from the root issuer to /cam.
    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
