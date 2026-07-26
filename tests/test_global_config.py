from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.android_integration import should_prefer_android_chroot
from devtool.cli import _apply_global_cli_defaults, _resolve_config_editor, build_parser, main
from devtool.config import global_config_doctor_entries, load_project_config, minimal_global_config_template
from devtool.models import ProjectConfig


class GlobalConfigTests(unittest.TestCase):
    def test_sparse_global_config_merges_user_preferences_but_repo_kind_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (root / ".devtool.toml").write_text("[project]\nkind='python'\n", encoding="utf-8")
            xdg = Path(tmp) / "xdg"
            global_path = xdg / "devtool" / "config.toml"
            global_path.parent.mkdir(parents=True)
            global_path.write_text(
                "[environment]\ndefault_chroot='ubuntu'\n"
                "[termux_chroot]\nproject_kinds=['python']\n"
                "[dotnet]\nstop_after_run=true\n"
                "[android]\naapt2_provider='auto'\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}, clear=False):
                config = load_project_config(str(root), None)

        self.assertEqual(config.kind, "python")
        self.assertEqual(config.chroot_name, "ubuntu")
        self.assertEqual(config.global_config_path, global_path)
        self.assertEqual(config.runner_options["termux_chroot"]["project_kinds"], ["python"])
        self.assertTrue(config.runner_options["dotnet"]["stop_after_run"])
        self.assertEqual(config.runner_options["android"]["aapt2_provider"], "auto")

    def test_repo_config_overrides_global_chroot_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='python'\n"
                "[execution]\nchroot_name='repochroot'\n",
                encoding="utf-8",
            )
            xdg = Path(tmp) / "xdg"
            global_path = xdg / "devtool" / "config.toml"
            global_path.parent.mkdir(parents=True)
            global_path.write_text("[environment]\ndefault_chroot='globalchroot'\n", encoding="utf-8")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}, clear=False):
                config = load_project_config(str(root), None)
        self.assertEqual(config.chroot_name, "repochroot")

    def test_global_cli_defaults_apply_sparse_preferences_without_beating_cli_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "xdg"
            path = xdg / "devtool" / "config.toml"
            path.parent.mkdir(parents=True)
            path.write_text(
                "[cli.apply]\nvalidate=true\nrollback_on_failure=false\n"
                "[cli.output]\ncopy_via='termux'\n"
                "[cli.hud]\nmode='android-bridge'\n",
                encoding="utf-8",
            )
            parser = build_parser()
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}, clear=False):
                args = parser.parse_args(["apply-overlay", "demo.zip"])
                _apply_global_cli_defaults(args, ["apply-overlay", "demo.zip"])
                explicit = parser.parse_args(["--copy-via", "x11", "apply-overlay", "--no-validate", "demo.zip"])
                _apply_global_cli_defaults(explicit, ["--copy-via", "x11", "apply-overlay", "--no-validate", "demo.zip"])

        self.assertTrue(args.validate)
        self.assertTrue(args.no_rollback_on_failure)
        self.assertEqual(args.copy_via, "termux")
        self.assertEqual(args.hud_mode, "android-bridge")
        self.assertFalse(explicit.validate)
        self.assertTrue(explicit.no_validate)
        self.assertEqual(explicit.copy_via, "x11")

    def test_global_doctor_warns_about_repo_and_command_scoped_keys(self) -> None:
        entries = global_config_doctor_entries(
            {
                "project": {"kind": "python"},
                "cli": {
                    "validation": {
                        "path": ["tests"],
                        "session": {"resume": True},
                    },
                    "environment": {"chroot": "androidos"},
                },
            }
        )
        names = {name for name, _message in entries}
        self.assertIn("project", names)
        self.assertIn("cli.validation.path", names)
        self.assertIn("cli.validation.session.resume", names)
        self.assertIn("cli.environment.chroot", names)

    def test_editor_resolution_prefers_requested_then_micro(self) -> None:
        with patch("devtool.cli.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"micro", "helix"} else None):
            self.assertEqual(_resolve_config_editor("helix --wait"), ["helix", "--wait"])
            with patch.dict(os.environ, {"DEVTOOL_EDITOR": "", "VISUAL": "", "EDITOR": ""}, clear=False):
                self.assertEqual(_resolve_config_editor(""), ["micro"])

    def test_config_commands_accept_global_and_editor_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "edit", "--global", "--editor", "micro"])
        self.assertEqual(args.config_command, "edit")
        self.assertTrue(args.global_config)
        self.assertEqual(args.editor, "micro")
        args = parser.parse_args(["config", "init", "--global", "--minimal", "--force"])
        self.assertTrue(args.global_config)
        self.assertTrue(args.minimal)
        self.assertTrue(args.force)

    def test_termux_chroot_policy_can_include_python_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="python",
                runner_options={"termux_chroot": {"project_kinds": ["python"]}},
            )
            termux = {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
            }
            with patch.dict("os.environ", termux, clear=True), patch("devtool.android_integration.shutil.which", return_value="/usr/bin/chrootctl"):
                self.assertTrue(should_prefer_android_chroot(config))
            default_config = ProjectConfig(repo_root=Path(tmp), kind="python", runner_options={})
            with patch.dict("os.environ", termux, clear=True), patch("devtool.android_integration.shutil.which", return_value="/usr/bin/chrootctl"):
                self.assertFalse(should_prefer_android_chroot(default_config))

    def test_global_config_template_documents_chroot_project_kinds(self) -> None:
        text = minimal_global_config_template()
        self.assertIn("[termux_chroot]", text)
        self.assertIn("project_kinds", text)
        self.assertIn("micro", text)


if __name__ == "__main__":
    unittest.main()
