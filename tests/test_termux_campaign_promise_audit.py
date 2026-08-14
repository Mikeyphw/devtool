from __future__ import annotations

import re
import unittest
from pathlib import Path


class TermuxCampaignPromiseAuditTests(unittest.TestCase):
    def test_literal_final_audit_contains_all_38_passing_acceptance_items(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "TERMUX_PROOT_PROMISE_AUDIT.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\|\s*(\d+)\s*\|.*?\|\s*PASS\s*\|", text, re.MULTILINE)
        self.assertEqual([int(item) for item in rows], list(range(1, 39)))
        self.assertIn("does **not** launch `android/devtool-live-hud`", text)

    def test_audit_names_final_gap_fixes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "TERMUX_PROOT_PROMISE_AUDIT.md").read_text(encoding="utf-8")
        for phrase in (
            "live-progress partial totals",
            "explicit-XDG log isolation",
            "dynamic JDK package advice",
            "detailed Android/NDK/backend doctor rows",
            "hermetic Android-marker scrubbing",
            "short private temporary roots for AF_UNIX regression tests",
            "uv-native sdist rebuilds",
            "installed-wrapper CLI verification",
            "interruption-aware structured report parsing",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
