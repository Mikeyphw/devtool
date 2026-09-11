from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import build_parser, main
from devtool.final_seal import FINAL_SEAL_SCHEMA_VERSION, final_seal_report
from devtool.test_selection import TestDiscoveryResult
from devtool.validation_profiles import BUILTIN_PROFILES

ROOT = Path(__file__).resolve().parents[1]


class Fs03FinalReleaseSealTests(unittest.TestCase):
    def test_release_repository_passes_final_seal(self) -> None:
        payload = final_seal_report(ROOT)
        self.assertEqual(payload["schema_version"], FINAL_SEAL_SCHEMA_VERSION)
        self.assertEqual(payload["status"], "pass", payload)
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertGreaterEqual(payload["summary"]["checks"], 12)
        self.assertGreaterEqual(payload["summary"]["compatibility_entries"], 24)

    def test_cli_json_and_strict_use_same_contract(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "final-seal", "--json", "--strict"])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["data"]["status"], "pass")

    def test_seal_alias_is_registered(self) -> None:
        args = build_parser().parse_args(["seal", "--strict"])
        self.assertIn(args.command, {"final-seal", "seal"})
        self.assertTrue(args.strict)

    def test_final_seal_validation_profile_is_release_strict(self) -> None:
        profile = BUILTIN_PROFILES["final-seal"]
        self.assertTrue(profile["validation"]["final_seal_contracts"])
        self.assertTrue(profile["artifacts"]["required"])
        self.assertEqual(profile["artifacts"]["external_verification"], "required")

    def test_runtime_test_discovery_model_is_not_collected_by_pytest(self) -> None:
        self.assertIs(TestDiscoveryResult.__test__, False)

    def test_missing_required_release_doc_fails_a_copy(self) -> None:
        # A deliberately tiny fake repo proves strict failure semantics without mutating ROOT.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".devtool.toml").write_text('[project]\nrunner="python"\nplatform="server"\n[workspace]\ndefault_target="devtool"\n[targets.devtool]\nrunner="python"\nplatform="server"\nroot="."\n', encoding="utf-8")
            payload = final_seal_report(root)
            self.assertEqual(payload["status"], "fail")
            failed = {item["id"] for item in payload["checks"] if item["status"] == "fail"}
            self.assertIn("docs.required", failed)
            self.assertIn("roadmap.reports", failed)

    def test_readme_and_final_seal_doc_name_fs03(self) -> None:
        self.assertIn("FS03", (ROOT / "README.md").read_text(encoding="utf-8"))
        text = (ROOT / "docs" / "FINAL_RELEASE_SEAL.md").read_text(encoding="utf-8")
        self.assertIn("devtool final-seal --strict", text)
        self.assertIn("FS03", text)


if __name__ == "__main__":
    unittest.main()
