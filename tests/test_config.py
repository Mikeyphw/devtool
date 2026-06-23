from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_devtool.config import detect_project_kind, generate_template, load_project_config, resolve_config_dict


class ConfigTests(unittest.TestCase):
    def test_detects_dotnet_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            self.assertEqual(detect_project_kind(root), "dotnet")

    def test_detects_python_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            self.assertEqual(detect_project_kind(root), "python")

    def test_generate_template_contains_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            text = generate_template(root)
            self.assertIn('restore_command = ""', text)
            self.assertIn('build_command = ""', text)

    def test_project_values_can_be_overridden_by_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\n"
                'kind = "python"\n'
                'python = "python3"\n'
                'run_module = "from_config"\n'
                'restore_command = "python3 -m pip install -e ."\n'
                'build_command = "python3 -m build"\n'
                'test_command = "python3 -m pytest"\n',
                encoding="utf-8",
            )
            env = {
                "DEVTOOL_PROJECT_PYTHON": "python3.12",
                "DEVTOOL_PROJECT_RUN_MODULE": "from_env",
                "DEVTOOL_PROJECT_RESTORE_COMMAND": "python3 -m pip install -r requirements.txt",
                "DEVTOOL_PROJECT_BUILD_COMMAND": "python3 -m hatch build",
                "DEVTOOL_PROJECT_TEST_COMMAND": "python3 -m pytest -q",
            }
            with patch.dict(os.environ, env, clear=False):
                config = load_project_config(str(root), None)

            self.assertEqual(config.python_executable, "python3.12")
            self.assertEqual(config.run_module, "from_env")
            self.assertEqual(config.restore_command, "python3 -m pip install -r requirements.txt")
            self.assertEqual(config.build_command, "python3 -m hatch build")
            self.assertEqual(config.test_command, "python3 -m pytest -q")

    def test_env_section_values_can_be_overridden_by_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind = 'dotnet'\n"
                "\n[env]\n"
                'SIMSTOOLKIT_WINE_PATH = "wine-from-config"\n',
                encoding="utf-8",
            )
            env = {
                "DEVTOOL_ENV_SIMSTOOLKIT_WINE_PATH": "wine-from-env",
                "DEVTOOL_ENV_EXTRA_FLAG": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                config = load_project_config(str(root), None)

            self.assertEqual(config.env["SIMSTOOLKIT_WINE_PATH"], "wine-from-env")
            self.assertEqual(config.env["EXTRA_FLAG"], "1")

    def test_bad_toml_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text("[project\nkind='dotnet'\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_project_config(str(root), None)

    def test_resolve_config_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            config = load_project_config(str(root), None)
            data = resolve_config_dict(config)
            self.assertEqual(data["project"]["kind"], "dotnet")


    def test_load_project_config_selects_named_dotnet_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps").mkdir()
            (root / "apps" / "S3pe.Modern.sln").write_text("", encoding="utf-8")
            (root / "apps" / "S3pe.App.csproj").write_text("", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "Tools.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\ndefault_target='s3pe'\n"
                "\n[targets.s3pe]\nsolution='apps/S3pe.Modern.sln'\nrun_project='apps/S3pe.App.csproj'\n"
                "\n[targets.tools]\nsolution='tools/Tools.sln'\n",
                encoding="utf-8",
            )

            config = load_project_config(str(root), None)

            self.assertEqual(config.selected_target, "s3pe")
            self.assertTrue(config.build_target.endswith("apps/S3pe.Modern.sln"))
            self.assertTrue(config.run_target.endswith("apps/S3pe.App.csproj"))
            self.assertEqual(sorted(config.targets), ["s3pe", "tools"])

    def test_load_project_config_fails_on_ambiguous_monorepo_without_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one").mkdir()
            (root / "two").mkdir()
            (root / "one" / "One.sln").write_text("", encoding="utf-8")
            (root / "two" / "Two.sln").write_text("", encoding="utf-8")

            with self.assertRaises(SystemExit):
                load_project_config(str(root), None)
