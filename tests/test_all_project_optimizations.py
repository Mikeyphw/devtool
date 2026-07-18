from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from devtool.android_integration import android_builder_arguments_for_tasks
from devtool.build_optimization import optimized_build_env, resolve_android_validation_policy, resolve_build_policy
from devtool.config import detect_project_kind, load_project_config
from devtool.execution import wrap_specs_for_chroot
from devtool.generic_progress import GenericProgressTracker
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import build_specs, build_validation_specs


class ProjectKindDetectionTests(unittest.TestCase):
    def test_detects_all_first_class_project_kinds(self) -> None:
        cases = {
            "node": ("package.json", "{}"),
            "rust": ("Cargo.toml", "[package]\nname='demo'\nversion='0.1.0'\n"),
            "go": ("go.mod", "module example.com/demo\n"),
            "maven": ("pom.xml", "<project/>\n"),
            "cmake": ("CMakeLists.txt", "cmake_minimum_required(VERSION 3.20)\n"),
            "meson": ("meson.build", "project('demo', 'c')\n"),
            "make": ("Makefile", "all:\n\t@true\n"),
        }
        for expected, (filename, content) in cases.items():
            with self.subTest(kind=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / filename).write_text(content, encoding="utf-8")
                self.assertEqual(detect_project_kind(root), expected)
                config = load_project_config(str(root), None)
                self.assertEqual(config.kind, expected)
                self.assertEqual(Path(config.project_root), root)


class ResourcePolicyTests(unittest.TestCase):
    def test_desktop_uses_more_parallelism_than_constrained_termux(self) -> None:
        config = ProjectConfig(repo_root=Path("/repo"), kind="rust")
        with (
            patch("devtool.build_optimization.os.cpu_count", return_value=12),
            patch("devtool.build_optimization.available_memory_mb", return_value=16000),
            patch.dict(os.environ, {"HOME": "/home/mike", "PREFIX": ""}, clear=True),
        ):
            desktop = resolve_build_policy(config, "rust")
        with (
            patch("devtool.build_optimization.os.cpu_count", return_value=12),
            patch("devtool.build_optimization.available_memory_mb", return_value=2500),
            patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
            }, clear=True),
        ):
            termux = resolve_build_policy(config, "rust")
        self.assertEqual(desktop.max_workers, 8)
        self.assertEqual(termux.max_workers, 2)
        self.assertLess(termux.max_workers, desktop.max_workers)

    def test_explicit_worker_limit_wins_and_reaches_tool_environment(self) -> None:
        config = ProjectConfig(repo_root=Path("/repo"), kind="dotnet")
        with patch.dict(os.environ, {"HOME": "/home/mike", "DEVTOOL_MAX_WORKERS": "3"}, clear=True):
            env = optimized_build_env(config, "dotnet")
        self.assertEqual(env["DEVTOOL_MAX_WORKERS"], "3")
        self.assertEqual(env["DOTNET_PROCESSOR_COUNT"], "3")
        self.assertEqual(env["NUGET_XMLDOC_MODE"], "skip")

    def test_chroot_cache_path_uses_guest_home(self) -> None:
        config = ProjectConfig(repo_root=Path("/repo"), kind="node", execution_environment="chroot")
        with (
            patch("devtool.build_optimization.os.cpu_count", return_value=8),
            patch("devtool.build_optimization.available_memory_mb", return_value=6000),
            patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_TERMUX_CHROOT_USER": "mike",
            }, clear=True),
        ):
            policy = resolve_build_policy(config, "node")
        self.assertEqual(str(policy.cache_root), "/home/mike/.cache/devtool/build/chroot/node")

    def test_chroot_wrapper_exports_runner_resource_environment(self) -> None:
        spec = CommandSpec(
            "build",
            ["cargo", "build"],
            env={"CARGO_BUILD_JOBS": "2", "DEVTOOL_BUILD_CACHE_ROOT": "/home/mike/.cache/devtool"},
        )
        wrapped = wrap_specs_for_chroot([spec], "androidos")[0]
        inner = wrapped.argv[-1]
        self.assertIn("CARGO_BUILD_JOBS=2", inner)
        self.assertIn("DEVTOOL_BUILD_CACHE_ROOT=/home/mike/.cache/devtool", inner)


    def test_android_validation_defaults_to_phone_safe_profile_in_termux(self) -> None:
        config = ProjectConfig(repo_root=Path("/repo"), kind="android")
        with (
            patch("devtool.build_optimization.os.cpu_count", return_value=8),
            patch("devtool.build_optimization.memory_info_mb", return_value=(4200, 6144)),
            patch.dict(os.environ, {
                "HOME": "/data/data/com.termux/files/home",
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
            }, clear=True),
        ):
            policy = resolve_android_validation_policy(config)

        self.assertTrue(policy.enabled)
        self.assertEqual(policy.max_workers, 1)
        self.assertEqual(policy.cpu_limit, 2)
        self.assertTrue(policy.split_gradle)
        self.assertTrue(policy.no_daemon)
        self.assertTrue(policy.low_priority)
        self.assertGreaterEqual(policy.gradle_heap_mb, 768)
        self.assertGreaterEqual(policy.memory_guard_mb, 512)

    def test_android_validation_can_disable_only_the_memory_guard(self) -> None:
        config = ProjectConfig(
            repo_root=Path("/repo"),
            kind="android",
            execution_environment="chroot",
            runner_options={"validation": {"memory_guard_mb": 0}},
        )
        policy = resolve_android_validation_policy(config)
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.memory_guard_mb, 0)


class FirstClassRunnerPlanningTests(unittest.TestCase):
    def test_node_uses_incremental_install_and_package_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({
                "name": "demo",
                "scripts": {"build": "vite build", "test": "vitest run", "lint": "eslint ."},
            }), encoding="utf-8")
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="node")
            specs = build_validation_specs(config)
        self.assertEqual([spec.name for spec in specs], ["restore", "build", "test", "lint"])
        self.assertEqual(specs[0].argv[0:2], ["npm", "install"])
        self.assertIn("--prefer-offline", specs[0].argv)
        self.assertEqual(specs[1].argv, ["npm", "run", "build"])
        self.assertTrue(all(spec.metadata["validation_scope"] == "node-incremental" for spec in specs))

    def test_rust_coalesces_build_and_test_and_preserves_incremental_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (root / "Cargo.lock").write_text("", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="rust")
            specs = build_validation_specs(config)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "cargo-validation")
        self.assertEqual(specs[0].argv[0:3], ["cargo", "test", "--all-targets"])
        self.assertIn("--locked", specs[0].argv)
        self.assertEqual(specs[0].env["CARGO_BUILD_JOBS"], "2")
        self.assertEqual(specs[0].env["CARGO_INCREMENTAL"], "1")

    def test_go_coalesces_compile_and_test_then_reuses_cache_for_vet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "3"}, clear=False):
            root = Path(tmp)
            (root / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="go")
            specs = build_validation_specs(config)
        self.assertEqual([spec.name for spec in specs], ["go-validation", "lint"])
        self.assertEqual(specs[0].argv, ["go", "test", "-p", "3", "./..."])
        self.assertEqual(specs[1].argv, ["go", "vet", "./..."])
        self.assertEqual(specs[0].env["GOMAXPROCS"], "3")

    def test_maven_uses_one_verify_reactor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="maven")
            specs = build_validation_specs(config)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "maven-validation")
        self.assertEqual(specs[0].argv[-1], "verify")
        self.assertIn("--no-transfer-progress", specs[0].argv)
        self.assertIn("2", specs[0].argv)

    def test_cmake_cold_and_warm_validation_reuse_build_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="cmake")
            cold = build_validation_specs(config)
            build_dir = root / ".devtool" / "build" / "cmake"
            build_dir.mkdir(parents=True)
            (build_dir / "CMakeCache.txt").write_text("", encoding="utf-8")
            warm = build_validation_specs(config)
        self.assertEqual([spec.name for spec in cold], ["configure", "build", "test"])
        self.assertEqual([spec.name for spec in warm], ["build", "test"])
        self.assertIn("--parallel", warm[0].argv)
        self.assertIn("2", warm[0].argv)

    def test_meson_cold_and_warm_validation_reuse_ninja_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "meson.build").write_text("project('demo', 'c')\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="meson")
            cold = build_validation_specs(config)
            build_dir = root / ".devtool" / "build" / "meson"
            build_dir.mkdir(parents=True)
            (build_dir / "build.ninja").write_text("", encoding="utf-8")
            warm = build_validation_specs(config)
        self.assertEqual([spec.name for spec in cold], ["configure", "build", "test"])
        self.assertEqual([spec.name for spec in warm], ["build", "test"])
        self.assertEqual(warm[0].argv[-2:], ["-j", "2"])

    def test_make_uses_bounded_parallelism_and_detected_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n\t@true\ntest:\n\t@true\nlint:\n\t@true\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="make")
            specs = build_validation_specs(config)
        self.assertEqual([spec.name for spec in specs], ["build", "test", "lint"])
        self.assertEqual(specs[0].argv, ["make", "-j", "2"])
        self.assertEqual(specs[1].argv[-1], "test")

    def test_python_does_not_recreate_an_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8")
            python = root / ".devtool" / "venvs" / "python" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"python": {"manager": "venv"}})
            specs = build_specs(config, "restore")
        self.assertEqual([spec.name for spec in specs], ["restore", "python-environment"])
        self.assertNotEqual(specs[0].argv[2:4], ["venv", str(python.parent.parent)])
        self.assertEqual(specs[0].metadata["manager"], "venv")
        self.assertIn("environment_fingerprint", specs[1].metadata)

    def test_android_gradle_receives_environment_aware_worker_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp, "DEVTOOL_MAX_WORKERS": "2"}, clear=False):
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
            (root / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="gradle")
            argv = android_builder_arguments_for_tasks(config, ["build"], scan_artifacts=False)
        index = argv.index("--max-workers")
        self.assertEqual(argv[index + 1], "2")


class CrossFrontendProgressTests(unittest.TestCase):
    def test_generic_build_progress_exposes_completed_and_total(self) -> None:
        tracker = GenericProgressTracker(["ninja"], "/repo", "build", Console(force_terminal=False))
        tracker.update_from_line("[37/124] Building CXX object demo.o")
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["completed"], 37)
        self.assertEqual(snapshot["total"], 124)
        self.assertAlmostEqual(snapshot["percent"], 37 * 100 / 124)

    def test_maven_reactor_progress_exposes_total(self) -> None:
        tracker = GenericProgressTracker(["mvn"], "/repo", "build", Console(force_terminal=False))
        tracker.update_from_line("[INFO] Building module-api 2/7")
        snapshot = tracker.snapshot()
        self.assertEqual((snapshot["completed"], snapshot["total"]), (2, 7))


if __name__ == "__main__":
    unittest.main()
