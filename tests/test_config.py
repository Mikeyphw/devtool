from __future__ import annotations

import os
import io
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch

from devtool.config import detect_project_kind, discover_dotnet_candidates, generate_template, load_project_config, resolve_config_dict


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

    def test_discovers_dotnet_candidates_ignoring_vendor_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vendor").mkdir()
            (root / "vendor" / "Ignored.sln").write_text("", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "Real.sln").write_text("", encoding="utf-8")
            (root / "dist").mkdir()
            (root / "dist" / "Ignored.csproj").write_text("", encoding="utf-8")

            candidates = discover_dotnet_candidates(root)

            self.assertEqual([str(path.relative_to(root)) for path in candidates], ["src/Real.sln"])

    def test_detects_mixed_repo_and_emits_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stderr(stream):
                kind = detect_project_kind(root)
            self.assertEqual(kind, "dotnet")
            self.assertIn("explicitly", stream.getvalue())

    def test_auto_kind_override_does_not_mask_explicit_config_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            android = root / "app" / "XDM.Android"
            android.mkdir(parents=True)
            (root / "App.sln").write_text("", encoding="utf-8")
            (android / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
            (android / "build.gradle.kts").write_text(
                'plugins { id("com.android.application") version "8.8.0" apply false }\n',
                encoding="utf-8",
            )
            (root / ".devtool.toml").write_text("[project]\nkind='android'\n", encoding="utf-8")
            stream = io.StringIO()

            with redirect_stderr(stream):
                config = load_project_config(str(root), None, kind_override="auto")

            self.assertEqual(config.kind, "android")
            self.assertNotIn("mixed project markers", stream.getvalue())

    def test_generate_template_contains_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
            text = generate_template(root)
            self.assertIn('restore_command = ""', text)
            self.assertIn('build_command = ""', text)
            self.assertRegex(text, r'manager = "(uv|venv)" # auto \| uv \| poetry \| pdm \| pipenv \| hatch \| venv')

    def test_generate_template_detects_python_hatch_and_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires=['hatchling']\nbuild-backend='hatchling.build'\n"
                "[project]\nname='x'\nversion='0.1.0'\n"
                "[project.optional-dependencies]\ndev=['pytest>=8']\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")
            text = generate_template(root)
        self.assertIn('manager = "hatch" # auto | uv | poetry | pdm | pipenv | hatch | venv', text)
        self.assertIn('test_framework = "pytest" # auto | pytest | unittest | tox | nox', text)

    def test_generate_template_detects_node_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"packageManager":"pnpm@10.0.0"}\n', encoding="utf-8")
            text = generate_template(root)
        self.assertIn('kind = "node"', text)
        self.assertIn('manager = "pnpm" # auto | npm | pnpm | yarn | bun', text)

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

    def test_config_paths_are_resolved_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "apps" / "foo"
            repo.mkdir(parents=True)
            (root / "apps" / "foo" / "Foo.sln").write_text("", encoding="utf-8")
            (root / "apps" / "foo" / "Foo.App.csproj").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\ndefault_target='foo'\n"
                "\n[targets.foo]\n"
                "solution='apps/foo/Foo.sln'\n"
                "run_project='apps/foo/Foo.App.csproj'\n"
                "cwd='apps/foo'\n",
                encoding="utf-8",
            )

            config = load_project_config(str(repo), None, target_name="foo")

            self.assertEqual(config.repo_root, repo.resolve())
            self.assertEqual(config.selected_target, "foo")
            self.assertEqual(config.build_target, str((root / "apps" / "foo" / "Foo.sln").resolve()))
            self.assertEqual(config.run_target, str((root / "apps" / "foo" / "Foo.App.csproj").resolve()))
            self.assertEqual(config.targets["foo"].cwd, str((root / "apps" / "foo").resolve()))


    def test_execution_environment_configuration_and_target_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\n"
                "\n[execution]\nenvironment='native-termux'\nchroot_name='androidos'\ndevice_serial='host-device'\n"
                "\n[targets.app]\nsolution='App.sln'\nexecution_environment='chroot'\nchroot_name='ubuntu'\ndevice_serial='target-device'\n",
                encoding="utf-8",
            )

            base = load_project_config(str(root), None)
            selected = load_project_config(str(root), None, target_name="app")

        self.assertEqual(base.execution_environment, "native-termux")
        self.assertEqual(base.chroot_name, "androidos")
        self.assertEqual(base.device_serial, "host-device")
        self.assertEqual(selected.execution_environment, "chroot")
        self.assertEqual(selected.chroot_name, "ubuntu")
        self.assertEqual(selected.device_serial, "target-device")

    def test_named_target_variant_options_override_target_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            android = root / "apps" / "android"
            android.mkdir(parents=True)
            (root / ".devtool.toml").write_text(
                "[project]\nkind='auto'\n"
                "\n[android]\nvariant='debug'\n"
                "\n[targets.mobile]\ntype='android'\nroot='apps/android'\n"
                "\n[targets.mobile.android]\nvariant='debug'\n"
                "\n[targets.mobile.variants.release.android]\nvariant='release'\n"
                "\n[targets.mobile.variants.release.gradle]\nmax_workers=2\n"
                "\n[targets.mobile.variants.release.env]\nDEV_FLAVOR='release'\n",
                encoding="utf-8",
            )

            config = load_project_config(str(root), None, target_name="mobile", variant_name="release")

        self.assertEqual(config.variant, "release")
        self.assertEqual(config.runner_options["android"]["variant"], "release")
        self.assertEqual(config.runner_options["gradle"]["max_workers"], 2)
        self.assertEqual(config.env["DEV_FLAVOR"], "release")

    def test_named_command_target_overrides_project_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"scripts":{"build":"node root.js"}}\n', encoding="utf-8")
            (root / "extensions" / "demo").mkdir(parents=True)
            (root / ".devtool.toml").write_text(
                "[project]\nkind='node'\nbuild_command='node root-build.js'\n"
                "\n[targets.demo_extension]\n"
                "type='command'\n"
                "root='extensions/demo'\n"
                "build_command='python3 build.py'\n"
                "test_command='python3 -m pytest'\n",
                encoding="utf-8",
            )

            base = load_project_config(str(root), None)
            selected = load_project_config(str(root), None, target_name="demo_extension")
            data = resolve_config_dict(selected)

        self.assertEqual(base.kind, "node")
        self.assertEqual(base.build_command, "node root-build.js")
        self.assertEqual(selected.kind, "command")
        self.assertEqual(selected.project_root, str((root / "extensions" / "demo").resolve()))
        self.assertEqual(selected.build_command, "python3 build.py")
        self.assertEqual(selected.test_command, "python3 -m pytest")
        self.assertEqual(data["targets"]["demo_extension"]["build_command"], "python3 build.py")

    def test_generated_template_documents_execution_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            text = generate_template(root)

        self.assertIn("[execution]", text)
        self.assertIn('environment = "auto"', text)
        self.assertIn('chroot_name = "androidos"', text)

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

            with self.assertRaises(SystemExit):
                load_project_config(str(root), None)

            config = load_project_config(str(root), None, target_name="s3pe")
            self.assertEqual(config.selected_target, "s3pe")
            self.assertTrue(config.build_target.endswith("apps/S3pe.Modern.sln"))
            self.assertTrue(config.run_target.endswith("apps/S3pe.App.csproj"))
            self.assertEqual(sorted(config.targets), ["s3pe", "tools"])

    def test_load_project_config_resolves_project_and_run_targets_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps").mkdir()
            (root / "apps" / "S3pe.Modern.sln").write_text("", encoding="utf-8")
            (root / "apps" / "S3pe.App.csproj").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\nsolution='apps/S3pe.Modern.sln'\nproject='apps/S3pe.App.csproj'\nrun_target='apps/S3pe.App.csproj'\n",
                encoding="utf-8",
            )

            config = load_project_config(str(root / "apps"), None)

            self.assertEqual(config.build_target, str((root / "apps" / "S3pe.Modern.sln").resolve()))
            self.assertEqual(config.run_target, str((root / "apps" / "S3pe.App.csproj").resolve()))

    def test_load_project_config_fails_on_ambiguous_monorepo_without_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one").mkdir()
            (root / "two").mkdir()
            (root / "one" / "One.sln").write_text("", encoding="utf-8")
            (root / "two" / "Two.sln").write_text("", encoding="utf-8")

            with self.assertRaises(SystemExit):
                load_project_config(str(root), None)


    def test_generate_template_uses_unique_target_names_for_duplicate_monorepo_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps").mkdir()
            (root / "apps" / "One.sln").write_text("", encoding="utf-8")
            (root / "apps" / "One.App.csproj").write_text("", encoding="utf-8")
            (root / "apps" / "Two.sln").write_text("", encoding="utf-8")
            (root / "apps" / "Two.App.csproj").write_text("", encoding="utf-8")

            text = generate_template(root)

            self.assertEqual(text.count("[targets.apps_One]"), 1)
            self.assertEqual(text.count("[targets.apps_Two]"), 1)
            self.assertNotIn("default_target", text)

    def test_generate_template_picks_nearest_run_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps" / "foo").mkdir(parents=True)
            (root / "apps" / "foo" / "Foo.sln").write_text("", encoding="utf-8")
            (root / "apps" / "foo" / "Foo.App.csproj").write_text("", encoding="utf-8")
            (root / "apps" / "foo" / "src").mkdir()
            (root / "apps" / "foo" / "src" / "Foo.Host.csproj").write_text("", encoding="utf-8")
            (root / "apps" / "bar").mkdir(parents=True)
            (root / "apps" / "bar" / "Bar.sln").write_text("", encoding="utf-8")
            (root / "apps" / "bar" / "Bar.App.csproj").write_text("", encoding="utf-8")

            text = generate_template(root)

            self.assertIn('[targets.apps_foo_Foo]', text)
            self.assertIn('run_project = "apps/foo/Foo.App.csproj"', text)
            self.assertIn('[targets.apps_bar_Bar]', text)
            self.assertIn('run_project = "apps/bar/Bar.App.csproj"', text)

    def test_generate_template_leaves_run_project_empty_when_no_local_app_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps" / "foo").mkdir(parents=True)
            (root / "apps" / "foo" / "Foo.sln").write_text("", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "Tools.sln").write_text("", encoding="utf-8")
            (root / "shared").mkdir()
            (root / "shared" / "Shared.App.csproj").write_text("", encoding="utf-8")

            text = generate_template(root)

            self.assertIn('[targets.apps_foo_Foo]', text)
            self.assertIn('run_project = ""', text)
