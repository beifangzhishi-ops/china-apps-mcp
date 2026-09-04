from __future__ import annotations

import unittest
from pathlib import Path


class PathPrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_prototype_entrypoint_is_thin_and_uses_native_path_aware_oauth(self) -> None:
        text = (
            self.repo_root / "src" / "china_apps_mcp" / "path_prototype.py"
        ).read_text(encoding="utf-8")

        self.assertIn("from .server import main as server_main", text)
        self.assertIn("server_main()", text)
        self.assertNotIn("LocalOAuthProvider._render_consent", text)
        self.assertNotIn("consent_action_path", text)

    def test_prototype_funnel_scripts_have_narrow_ownership(self) -> None:
        configure = (
            self.repo_root / "scripts" / "configure-path-prototype-funnel.ps1"
        ).read_text(encoding="utf-8")
        disable = (
            self.repo_root / "scripts" / "disable-path-prototype-funnel.ps1"
        ).read_text(encoding="utf-8")
        combined = configure + "\n" + disable

        self.assertNotIn("funnel reset", combined)
        self.assertNotIn("--set-path=/v1", combined)
        self.assertNotIn("--https=8443", combined)
        self.assertNotIn("--https=10000", combined)
        self.assertIn('"/cam/mcp"', combined)
        self.assertIn('"/.well-known/oauth-authorization-server/cam"', combined)
        self.assertIn('"/.well-known/oauth-protected-resource/cam/mcp"', combined)


if __name__ == "__main__":
    unittest.main()
