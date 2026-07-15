from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.config import load_project_config
from devtool.models import CommandSpec, ProjectConfig
from devtool.toolchains import apply_toolchain_wrapper


class ToolchainWrapperTests(unittest.TestCase):
    def test_config_reads_toolchain_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='x'\nversion='0.1.0'\n",
                encoding="utf-8",
            )
            (root / ".devtool.toml").write_text(
                "[project]\nkind='python'\n\n[toolchain]\nmanager='mise'\n",
                encoding="utf-8",
            )

            config = load_project_config(str(root), None)

            self.assertEqual(config.toolchain_manager, "mise")

    def test_auto_wraps_when_mise_and_marker_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mise.toml").write_text("[tools]\npython='3.12'\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, kind="python", project_root=str(root))
            spec = CommandSpec("test", ["pytest", "-q"], cwd=str(root))

            with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
                wrapped = apply_toolchain_wrapper(
                    config,
                    [spec],
                    env={"PATH": os.environ.get("PATH", "")},
                )

            self.assertEqual(wrapped[0].argv, ["mise", "exec", "--", "pytest", "-q"])
            self.assertEqual(wrapped[0].metadata["progress_argv"], ["pytest", "-q"])
            self.assertEqual(wrapped[0].metadata["toolchain_manager"], "mise")

    def test_auto_does_not_wrap_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="python", project_root=str(root))
            spec = CommandSpec("test", ["pytest", "-q"], cwd=str(root))

            with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
                wrapped = apply_toolchain_wrapper(
                    config,
                    [spec],
                    env={"PATH": os.environ.get("PATH", "")},
                )

            self.assertEqual(wrapped[0].argv, ["pytest", "-q"])

    def test_env_override_disables_mise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mise.toml").write_text("[tools]\npython='3.12'\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                kind="python",
                project_root=str(root),
                toolchain_manager="mise",
            )
            spec = CommandSpec("test", ["pytest", "-q"], cwd=str(root))

            with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
                wrapped = apply_toolchain_wrapper(
                    config,
                    [spec],
                    env={"PATH": os.environ.get("PATH", ""), "DEVTOOL_TOOLCHAIN": "none"},
                )

            self.assertEqual(wrapped[0].argv, ["pytest", "-q"])
            self.assertEqual(wrapped[0].metadata["toolchain_manager"], "none")

    def test_skips_destructive_and_git_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mise.toml").write_text("[tools]\nnode='22'\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                kind="command",
                project_root=str(root),
                toolchain_manager="mise",
            )
            specs = [
                CommandSpec("tag", ["git", "tag", "v1"], destructive=True),
                CommandSpec("build", ["npm", "test"], cwd=str(root)),
            ]

            with patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
                wrapped = apply_toolchain_wrapper(
                    config,
                    specs,
                    env={"PATH": os.environ.get("PATH", "")},
                )

            self.assertEqual(wrapped[0].argv, ["git", "tag", "v1"])
            self.assertEqual(wrapped[1].argv, ["mise", "exec", "--", "npm", "test"])


if __name__ == "__main__":
    unittest.main()
