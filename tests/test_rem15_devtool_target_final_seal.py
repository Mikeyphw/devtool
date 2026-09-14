from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path

from devtool.cli import main
from devtool.config import load_project_config
from devtool.runners import build_validation_specs
from devtool.validation_profiles import apply_profile
from devtool.final_seal import final_seal_report
from devtool.target_seal import TARGET_SEAL_SCHEMA_VERSION, target_seal_report

ROOT = Path(__file__).resolve().parents[1]


class Rem15DevtoolTargetFinalSealTests(unittest.TestCase):
    def test_devtool_target_seal_passes(self) -> None:
        payload = target_seal_report(ROOT, "devtool")
        self.assertEqual(payload["schema_version"], TARGET_SEAL_SCHEMA_VERSION)
        self.assertEqual(payload["report_kind"], "devtool-target-final-seal")
        self.assertEqual(payload["target"], "devtool")
        self.assertEqual(payload["status"], "pass", payload)
        self.assertGreaterEqual(payload["summary"]["checks"], 8)
        self.assertIn("devtool", payload["summary"]["expected_targets"])

    def test_final_seal_target_cli_json_uses_target_report(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "final-seal", "--target", "devtool", "--json", "--strict"])
        self.assertEqual(rc, 0)
        envelope = json.loads(stdout.getvalue())
        payload = envelope["data"]
        self.assertEqual(payload["report_kind"], "devtool-target-final-seal")
        self.assertEqual(payload["target"], "devtool")
        self.assertEqual(payload["status"], "pass")

    def test_final_seal_global_target_cli_json_uses_target_report(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "--target", "devtool", "final-seal", "--json", "--strict"])
        self.assertEqual(rc, 0)
        envelope = json.loads(stdout.getvalue())
        payload = envelope["data"]
        self.assertEqual(payload["target"], "devtool")
        self.assertEqual(payload["status"], "pass")

    def test_missing_target_fails_strictly(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "final-seal", "--target", "missing_target", "--json", "--strict"])
        self.assertEqual(rc, 1)
        envelope = json.loads(stdout.getvalue())
        payload = envelope["data"]
        self.assertEqual(payload["status"], "fail")
        failed = {item["id"] for item in payload["checks"] if item["status"] == "fail"}
        self.assertIn("target.configured", failed)
        self.assertIn("target.expectation", failed)

    def test_final_seal_profile_plans_python_target_command(self) -> None:
        config = load_project_config(str(ROOT), str(ROOT / ".devtool.toml"), target_name="devtool")
        args = SimpleNamespace(
            validation_workflow_profile="final-seal",
            validation_resource_profile="automatic",
            skip_restore=True,
            skip_build=True,
            skip_test=True,
        )
        apply_profile(config, args)
        specs = build_validation_specs(
            config,
            skip_restore=args.skip_restore,
            skip_build=args.skip_build,
            skip_test=args.skip_test,
        )
        final_specs = [spec for spec in specs if spec.name == "python-final-seal"]
        self.assertEqual(len(final_specs), 1, [spec.name for spec in specs])
        argv = final_specs[0].argv
        self.assertIn("final-seal", argv)
        self.assertIn("--strict", argv)
        self.assertEqual(argv[-2:], ["--target", "devtool"])
        self.assertTrue(final_specs[0].metadata["final_seal_contract"])

    def test_repository_wide_final_seal_still_passes_after_target_split(self) -> None:
        payload = final_seal_report(ROOT)
        self.assertEqual(payload["status"], "pass", payload)
        report_check = next(item for item in payload["checks"] if item["id"] == "roadmap.reports")
        self.assertEqual(report_check["status"], "pass")


if __name__ == "__main__":
    unittest.main()
