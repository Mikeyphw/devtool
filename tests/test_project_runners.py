from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from devtool.models import ProjectConfig
from devtool.project_runners import (
    DEFAULT_RUNNER_REGISTRY,
    AndroidGradleRunner,
    DotnetRunner,
    CMakeRunner,
    GenericCommandRunner,
    GoRunner,
    JvmGradleRunner,
    MakeRunner,
    MavenRunner,
    MesonRunner,
    NodeRunner,
    PythonRunner,
    RustRunner,
    RunnerCapabilities,
    RunnerRegistry,
    get_project_runner,
)
from devtool.runners import build_specs, build_validation_specs, run_specs, runner_capabilities


class RunnerRegistryTests(unittest.TestCase):
    def test_default_registry_resolves_supported_project_types(self) -> None:
        self.assertIsInstance(get_project_runner("dotnet"), DotnetRunner)
        self.assertIsInstance(get_project_runner("android"), AndroidGradleRunner)
        self.assertIsInstance(get_project_runner("gradle"), JvmGradleRunner)
        self.assertIsInstance(get_project_runner("python"), PythonRunner)
        self.assertIsInstance(get_project_runner("node"), NodeRunner)
        self.assertIsInstance(get_project_runner("rust"), RustRunner)
        self.assertIsInstance(get_project_runner("go"), GoRunner)
        self.assertIsInstance(get_project_runner("maven"), MavenRunner)
        self.assertIsInstance(get_project_runner("cmake"), CMakeRunner)
        self.assertIsInstance(get_project_runner("meson"), MesonRunner)
        self.assertIsInstance(get_project_runner("make"), MakeRunner)
        self.assertIsInstance(get_project_runner("mixed"), GenericCommandRunner)
        self.assertEqual(
            DEFAULT_RUNNER_REGISTRY.registered_kinds(),
            ("android", "cmake", "dotnet", "go", "gradle", "make", "maven", "meson", "node", "python", "rust"),
        )

    def test_registry_rejects_duplicate_kinds_without_replace(self) -> None:
        registry = RunnerRegistry([DotnetRunner()])
        with self.assertRaises(ValueError):
            registry.register(DotnetRunner())
        registry.register(DotnetRunner(), replace=True)
        self.assertIsInstance(registry.resolve("dotnet"), DotnetRunner)

    def test_capability_model_is_machine_readable(self) -> None:
        capabilities = RunnerCapabilities(build=True, test=True, validate=True)
        self.assertEqual(capabilities.supported(), ("build", "test", "validate"))
        self.assertTrue(capabilities.as_dict()["build"])
        self.assertFalse(capabilities.as_dict()["lint"])


class DotnetRunnerCompatibilityTests(unittest.TestCase):
    def test_dotnet_command_planning_preserves_existing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
                run_target="Demo.App.csproj",
            )

            self.assertEqual(build_specs(config, "restore")[0].argv, ["dotnet", "restore", "Demo.sln"])
            self.assertEqual(build_specs(config, "build")[0].argv, ["dotnet", "build", "Demo.sln"])
            self.assertEqual(build_specs(config, "test")[0].argv, ["dotnet", "test", "Demo.sln"])
            self.assertEqual(
                build_specs(config, "run")[0].argv,
                ["dotnet", "run", "--project", str((Path(tmp) / "Demo.App.csproj").resolve())],
            )

    def test_validation_optimization_is_owned_by_dotnet_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
                build_command="env CONFIG=Debug dotnet build Demo.sln",
                test_command="env CONFIG=Debug dotnet test Demo.sln --logger trx",
            )
            specs = build_validation_specs(config)

        self.assertEqual(specs[0].argv, ["dotnet", "restore", "Demo.sln"])
        self.assertIn("--no-restore", specs[1].argv)
        self.assertIn("--no-build", specs[2].argv)
        self.assertTrue(specs[1].metadata["validation_no_restore"])
        self.assertTrue(specs[2].metadata["validation_no_build"])


    def test_validation_optimizes_direct_explicit_commands_without_touching_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            direct = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_command="sudo dotnet build Demo.sln",
                test_command="env CONFIG=Debug dotnet test Demo.sln",
            )
            direct_specs = build_validation_specs(direct)
            scripted = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_command="bash ./build.sh",
                test_command="bash ./test.sh",
            )
            scripted_specs = build_validation_specs(scripted)

        self.assertIn("--no-restore", direct_specs[1].argv)
        self.assertIn("--no-build", direct_specs[2].argv)
        self.assertEqual(scripted_specs[1].argv, ["bash", "./build.sh"])
        self.assertEqual(scripted_specs[2].argv, ["bash", "./test.sh"])

    def test_validation_does_not_duplicate_existing_optimization_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_command="dotnet build --no-restore Demo.sln",
                test_command="dotnet test --no-build Demo.sln",
            )
            specs = build_validation_specs(config)

        self.assertEqual(specs[1].argv.count("--no-restore"), 1)
        self.assertEqual(specs[2].argv.count("--no-build"), 1)

    def test_skipping_build_does_not_force_no_build_on_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                test_target="Demo.sln",
            )
            specs = build_validation_specs(config, skip_build=True)

        self.assertEqual([spec.name for spec in specs], ["test"])
        self.assertNotIn("--no-build", specs[-1].argv)
        self.assertTrue(specs[-1].metadata["implicit_restore"])

    def test_standalone_test_still_builds_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                build_target="Demo.sln",
                test_target="Demo.sln",
            )
            spec = build_specs(config, "test")[0]

        self.assertNotIn("--no-build", spec.argv)

    def test_dotnet_capabilities_report_only_implemented_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(repo_root=Path(tmp), kind="dotnet", build_target="Demo.sln")
            capabilities = runner_capabilities(config)

        self.assertEqual(
            capabilities.supported(),
            ("restore", "build", "test", "selective_tests", "validate", "run", "package", "clean"),
        )
        self.assertTrue(capabilities.selective_tests)
        self.assertFalse(capabilities.lint)

    def test_dotnet_variant_adds_configuration_framework_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                build_target="Demo.sln",
                test_target="Demo.sln",
                variant="release",
                runner_options={"dotnet": {"framework": "net9.0", "runtime": "linux-x64"}},
            )
            build = build_specs(config, "build")[0]
            test = build_specs(config, "test")[0]

        self.assertIn("-c", build.argv)
        self.assertIn("Release", build.argv)
        self.assertIn("-f", test.argv)
        self.assertIn("net9.0", test.argv)
        self.assertIn("-r", test.argv)
        self.assertIn("linux-x64", test.argv)
        self.assertEqual(test.env["DEVTOOL_VARIANT"], "release")

    def test_dotnet_validation_preserves_variant_and_optimization_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
                variant="debug",
            )
            specs = build_validation_specs(config)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "dotnet-validation")
        self.assertNotIn("--no-build", specs[0].argv)
        self.assertIn("Debug", specs[0].argv)
        self.assertEqual(specs[0].metadata["variant"], "debug")
        self.assertTrue(specs[0].metadata["coalesced_dotnet_validation"])


    def test_default_validation_coalesces_same_target_into_dotnet_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
            )
            specs = build_validation_specs(config)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "dotnet-validation")
        self.assertEqual(specs[0].argv[:3], ["dotnet", "test", "Demo.sln"])
        self.assertEqual(specs[0].metadata["validation_components"], ["restore", "build", "test"])

    def test_skip_restore_disables_implicit_restore_in_coalesced_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
            )
            specs = build_validation_specs(config, skip_restore=True)

        self.assertEqual(len(specs), 1)
        self.assertIn("--no-restore", specs[0].argv)
        self.assertFalse(specs[0].metadata["implicit_restore"])

    def test_explicit_restore_option_preserves_three_phase_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Demo.sln",
                build_target="Demo.sln",
                test_target="Demo.sln",
                runner_options={"dotnet": {"explicit_restore": True}},
            )
            specs = build_validation_specs(config)

        self.assertEqual([spec.name for spec in specs], ["restore", "build", "test"])
        self.assertIn("--no-restore", specs[1].argv)
        self.assertIn("--no-build", specs[2].argv)

    def test_different_build_and_test_targets_keep_safe_separate_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="dotnet",
                restore_target="Desktop.sln",
                build_target="Desktop.sln",
                test_target="tests/Desktop.Tests.csproj",
            )
            specs = build_validation_specs(config)

        self.assertEqual([spec.name for spec in specs], ["build", "test"])
        self.assertNotIn("--no-build", specs[1].argv)
        self.assertTrue(specs[0].metadata["implicit_restore"])
        self.assertTrue(specs[1].metadata["implicit_restore"])


class ExistingRunnerCompatibilityTests(unittest.TestCase):
    def test_python_planning_remains_available_through_common_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, kind="python", python_executable="python3.12")

            build_argv = next(spec.argv for spec in build_specs(config, "build") if spec.name == "build")
            test_argv = build_specs(config, "test")[0].argv
            self.assertIn("-m", build_argv)
            self.assertEqual(build_argv[build_argv.index("-m") + 1], "build")
            self.assertIn("--outdir", build_argv)
            self.assertTrue("pytest" in test_argv or "unittest" in test_argv)

    def test_python_runner_exports_uv_project_environment_for_uv_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as xdg:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8")
            (root / "uv.lock").write_text("", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"DEVTOOL_XDG_DIR": xdg}, clear=False):
                config = ProjectConfig(repo_root=root, kind="python", runner_options={"python": {"manager": "auto"}})
                runner = PythonRunner()
                env = runner._env(config)

        self.assertIn("UV_PROJECT_ENVIRONMENT", env)
        self.assertTrue(env["UV_PROJECT_ENVIRONMENT"].startswith(str(Path(xdg) / "python" / "venvs")))
        self.assertFalse(env["UV_PROJECT_ENVIRONMENT"].startswith(str(root)))

    def test_generic_capabilities_follow_configured_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ProjectConfig(
                repo_root=Path(tmp),
                kind="unknown",
                build_command="make all",
                test_command="make test",
            )
            runner = get_project_runner(config)
            capabilities = runner.capabilities_for(config)

            self.assertIsInstance(runner, GenericCommandRunner)
            self.assertTrue(capabilities.build)
            self.assertTrue(capabilities.test)
            self.assertTrue(capabilities.validate)
            self.assertFalse(capabilities.restore)
            self.assertEqual(build_specs(config, "build")[0].argv, ["make", "all"])

    def test_generic_runner_uses_selected_target_command_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extension_root = root / "extensions" / "demo"
            extension_root.mkdir(parents=True)
            config = ProjectConfig(
                repo_root=root,
                kind="command",
                project_root=str(extension_root),
                selected_target="demo",
                build_command="python3 build.py",
            )

            spec = build_specs(config, "build")[0]

        self.assertEqual(spec.argv, ["python3", "build.py"])
        self.assertEqual(spec.cwd, str(extension_root))
        self.assertEqual(spec.metadata["runner"], "command")


class DotnetBuildServerReuseTests(unittest.TestCase):
    def test_dotnet_build_server_shutdown_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = build_specs(
                ProjectConfig(repo_root=root, kind="dotnet", build_target="Demo.sln"),
                "build",
            )[0]
            retained, _ = run_specs(
                ProjectConfig(repo_root=root, kind="dotnet"),
                [spec],
                verbose=False,
                dry_run=True,
            )
            stopped, _ = run_specs(
                ProjectConfig(
                    repo_root=root,
                    kind="dotnet",
                    runner_options={"dotnet": {"stop_after_run": True}},
                ),
                [spec],
                verbose=False,
                dry_run=True,
            )

        self.assertNotIn("dotnet-build-server-shutdown", [item.name for item in retained])
        self.assertIn("dotnet-build-server-shutdown", [item.name for item in stopped])


class GradleDaemonReuseTests(unittest.TestCase):
    def test_gradle_daemon_shutdown_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name = 'demo'\n", encoding="utf-8")
            (root / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
            spec = build_specs(
                ProjectConfig(
                    repo_root=root,
                    kind="gradle",
                    runner_options={"tasks": {"build": ["build"]}},
                ),
                "build",
            )[0]
            retained, _ = run_specs(
                ProjectConfig(repo_root=root, kind="gradle"),
                [spec],
                verbose=False,
                dry_run=True,
            )
            stopped, _ = run_specs(
                ProjectConfig(
                    repo_root=root,
                    kind="gradle",
                    runner_options={"gradle": {"stop_after_run": True}},
                ),
                [spec],
                verbose=False,
                dry_run=True,
            )

        self.assertNotIn("gradle-daemon-shutdown", [item.name for item in retained])
        self.assertIn("gradle-daemon-shutdown", [item.name for item in stopped])

if __name__ == "__main__":
    unittest.main()
