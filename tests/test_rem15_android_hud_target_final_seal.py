from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import main
from devtool.final_seal import final_seal_report
from devtool.target_seal import TARGET_SEAL_SCHEMA_VERSION, target_seal_report

ROOT = Path(__file__).resolve().parents[1]


def _unwrap(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    return payload.get("data", payload)


class Rem15AndroidHudTargetFinalSealTests(unittest.TestCase):
    def test_android_hud_target_seal_passes(self) -> None:
        payload = target_seal_report(ROOT, "android_hud")
        self.assertEqual(payload["schema_version"], TARGET_SEAL_SCHEMA_VERSION)
        self.assertEqual(payload["report_kind"], "devtool-target-final-seal")
        self.assertEqual(payload["target"], "android_hud")
        self.assertEqual(payload["status"], "pass", payload)
        self.assertEqual(payload["summary"]["expected_targets"], ["android_hud", "devtool"])

    def test_android_hud_target_cli_json_uses_target_report(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "final-seal", "--target", "android_hud", "--json", "--strict"])
        self.assertEqual(rc, 0)
        payload = _unwrap(stdout.getvalue())
        self.assertEqual(payload["report_kind"], "devtool-target-final-seal")
        self.assertEqual(payload["target"], "android_hud")
        self.assertEqual(payload["status"], "pass")

    def test_repository_wide_final_seal_requires_every_target_overlay(self) -> None:
        payload = final_seal_report(ROOT)
        self.assertEqual(payload["status"], "pass", payload)
        self.assertEqual(payload["summary"]["target_final_reports"], 2)
        self.assertEqual(payload["summary"]["configured_targets"], 2)
        checks = {item["id"]: item for item in payload["checks"]}
        self.assertEqual(checks["targets.final-seal-overlays"]["status"], "pass")
        self.assertEqual(checks["coverage.final-remediation"]["status"], "pass")
        self.assertGreaterEqual(payload["summary"]["roadmap_reports"], 33)
        self.assertGreaterEqual(payload["summary"]["roadmap_test_files"], 37)

    def test_android_target_seal_fails_when_required_manifest_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".devtool.toml").write_text((ROOT / ".devtool.toml").read_text(encoding="utf-8"), encoding="utf-8")
            (root / "android/devtool-live-hud").mkdir(parents=True)
            (root / "android/devtool-live-hud/REM15_ANDROID_HUD_TARGET_FINAL_QUALIFICATION_REPORT.md").write_text("# report\n" + "x" * 200, encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_rem15_android_hud_target_final_seal.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
            payload = target_seal_report(root, "android_hud")
            self.assertEqual(payload["status"], "fail")
            required = next(item for item in payload["checks"] if item["id"] == "target.required-paths")
            self.assertEqual(required["status"], "fail")
            self.assertIn("android/devtool-live-hud/app/src/main/AndroidManifest.xml", required["details"])

    def test_coverage_manifest_accounts_for_308_findings_and_two_target_overlays(self) -> None:
        payload = json.loads((ROOT / "FINAL_REMEDIATION_COVERAGE.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["roadmap_total_overlays"], 15)
        self.assertEqual(payload["deduped_findings_total"], 308)
        self.assertEqual(payload["assigned_findings_closed_by_overlays_1_to_14"], 308)
        self.assertEqual({item["target"] for item in payload["overlay_15_target_overlays"]}, {"devtool", "android_hud"})


if __name__ == "__main__":
    unittest.main()
