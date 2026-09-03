from __future__ import annotations

import unittest
from pathlib import Path

from china_apps_mcp.path_prototype import consent_action_path


class PathPrototypeTests(unittest.TestCase):
    def test_consent_action_preserves_public_prefix(self) -> None:
        self.assertEqual(
            consent_action_path("https://gateway.example/cam"),
            "/cam/oauth/consent",
        )
        self.assertEqual(
            consent_action_path("https://gateway.example/cam/"),
            "/cam/oauth/consent",
        )

    def test_root_behavior_remains_unchanged(self) -> None:
        self.assertEqual(
            consent_action_path("https://gateway.example"),
            "/oauth/consent",
        )

    def test_prototype_funnel_scripts_have_narrow_ownership(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        configure = (repo_root / "scripts" / "configure-path-prototype-funnel.ps1").read_text(
            encoding="utf-8"
        )
        disable = (repo_root / "scripts" / "disable-path-prototype-funnel.ps1").read_text(
            encoding="utf-8"
        )
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
