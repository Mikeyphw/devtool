"""Mixed repo overlay validation without weakening project execution selection."""
from __future__ import annotations
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from devtool import cli
from devtool.config import load_project_config

MIXED = {"kind": "unknown", "candidates": ("android", "rust", "make"),
         "root_candidates": (), "reason": "mixed"}

class MixedRepositoryNativeOverlaySafety(unittest.TestCase):
    def args(self, root: Path, command: str, *, scope: str = "", static: bool = False):
        return argparse.Namespace(
            command=command, repo=str(root), config=None, target="",
            variant="", solution="", project="", project_type="auto",
            group=[], all_targets=False, affected=False,
            validate_scope=scope, static_validate=static,
            artifact="nonexistent.zip", validate_artifact="nonexistent.zip",
        )

    def mixed(self):
        return mock.patch("devtool.config.detect_project_kind_details", return_value=MIXED)

    def test_archive_validation_accepts_untyped_mixed_repository(self):
        with tempfile.TemporaryDirectory() as d, self.mixed():
            cfg = cli._load_command_config(self.args(Path(d), "validate", scope="overlay"))
            self.assertEqual(cfg.repo_root, Path(d))

    def test_legacy_validate_overlay_command_accepts_mixed(self):
        with tempfile.TemporaryDirectory() as d, self.mixed():
            cfg = cli._load_command_config(self.args(Path(d), "validate-overlay"))
            self.assertEqual(cfg.repo_root, Path(d))

    def test_static_apply_accepts_mixed_but_normal_apply_refuses(self):
        with tempfile.TemporaryDirectory() as d, self.mixed(), \
             mock.patch.object(cli, "_read_artifact_manifest", return_value={}):
            cfg = cli._load_command_config(self.args(Path(d), "apply-overlay", static=True))
            self.assertEqual(cfg.repo_root, Path(d))
            with self.assertRaisesRegex(SystemExit, "Mixed repository"):
                cli._load_command_config(self.args(Path(d), "apply-overlay"))

    def test_real_commands_keep_ambiguous_runner_refusal(self):
        with tempfile.TemporaryDirectory() as d, self.mixed():
            for command, scope in (("build", ""), ("test", ""), ("validate", "")):
                with self.subTest(command=command), self.assertRaisesRegex(SystemExit, "Mixed repository"):
                    cli._load_command_config(self.args(Path(d), command, scope=scope))

    def test_missing_or_damaged_archive_still_fails_native_validation(self):
        with tempfile.TemporaryDirectory() as d, self.mixed():
            args = self.args(Path(d), "validate", scope="overlay")
            cfg = cli._load_command_config(args)
            try:
                result = cli._run_artifact_validation_command(cfg, args)
            except (FileNotFoundError, ValueError, RuntimeError, OSError):
                pass
            else:
                self.assertNotEqual(result, 0, "Missing archive must fail validation")

    def test_explicit_project_type_still_available_for_mixed_execution(self):
        with tempfile.TemporaryDirectory() as d, self.mixed():
            args = self.args(Path(d), "build")
            args.project_type = "android"
            cfg = cli._load_command_config(args)
            self.assertEqual(cfg.kind, "android")

if __name__ == "__main__":
    unittest.main()
