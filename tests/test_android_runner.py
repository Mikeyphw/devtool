from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.android_build import (
    artifact_variant,
    default_tasks,
    discover_sdk_root,
    discover_sdk_root_with_source,
    find_java_home,
    inspect_project,
    normalize_gradle_arguments,
    planned_artifact_names,
    select_revanced_asset,
    maybe_reexec_in_chroot,
    wrapper_distribution_version,
)
from devtool.android_integration import (
    android_builder_arguments,
    android_doctor_rows,
    android_inspect_rows,
    configured_tasks,
    stage_android_builder,
    stage_gradle_init_script,
    should_prefer_android_chroot,
)
from devtool.config import detect_project_kind, discover_gradle_roots, load_project_config
from devtool.models import ProjectConfig
from devtool.project_runners import AndroidGradleRunner, get_project_runner
from devtool.runners import build_specs, build_validation_specs


def make_android_project(root: Path, *, application: bool = True) -> None:
    (root / "app" / "src" / "main").mkdir(parents=True)
    (root / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest package='demo'/>", encoding="utf-8")
    (root / "settings.gradle.kts").write_text('rootProject.name = "DemoApp"\ninclude(":app")\n', encoding="utf-8")
    plugin = "com.android.application" if application else "com.android.library"
    (root / "build.gradle.kts").write_text(
        f'''plugins {{ id("{plugin}") version "8.8.0" apply false }}\n''',
        encoding="utf-8",
    )
    (root / "app" / "build.gradle.kts").write_text(
        f'''plugins {{ id("{plugin}") }}\nandroid {{ compileSdk = 35 }}\n''',
        encoding="utf-8",
    )
    (root / "gradle" / "wrapper").mkdir(parents=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.2-bin.zip\n",
        encoding="utf-8",
    )
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)


class AndroidProjectInspectionTests(unittest.TestCase):
    def test_detects_android_and_extracts_static_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            project = inspect_project(root)

            self.assertEqual(detect_project_kind(root), "android")
            self.assertTrue(project.android)
            self.assertTrue(project.android_application)
            self.assertEqual(project.compile_sdk, 35)
            self.assertEqual(project.agp_major, 8)
            self.assertEqual(default_tasks(project), ["assembleDebug"])
            self.assertEqual(wrapper_distribution_version(root), "8.10.2")

    def test_discovers_nested_android_root_without_reporting_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            nested = repo / "app" / "XDM.Android"
            make_android_project(nested)
            # A module build file is part of the enclosing Gradle build, not a
            # second project root.
            roots = discover_gradle_roots(repo, android_only=True)
            detected = detect_project_kind(repo)

        self.assertEqual(roots, [nested.resolve()])
        self.assertEqual(detected, "android")

    def test_artifact_naming_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"apk")
            names = planned_artifact_names([source], root, "DemoApp", None)

        self.assertEqual(names, ["demo-app-debug.apk"])
        self.assertEqual(artifact_variant(source), "debug")

    def test_local_properties_sdk_root_is_preferred_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk_root = root / "Android" / "Sdk"
            sdk_root.mkdir(parents=True)
            (root / "local.properties").write_text(f"sdk.dir={sdk_root}\n", encoding="utf-8")

            discovered = discover_sdk_root(None, root)
            discovered_with_source = discover_sdk_root_with_source(None, root)

        self.assertEqual(discovered, sdk_root.resolve())
        self.assertEqual(discovered_with_source, (sdk_root.resolve(), "local.properties sdk.dir"))

    def test_android_auto_sdk_option_is_threaded_into_builder_and_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"auto_sdk": True}},
            )
            with patch.dict(os.environ, {
                "DEVTOOL_ANDROID_BUILDER": str(root / "state" / "build-apk"),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "events.gradle"),
            }, clear=False):
                args = android_builder_arguments(config, "build")
                inspect_rows = dict(android_inspect_rows(config))
                doctor_rows = dict(android_doctor_rows(config))

        self.assertIn("--auto-sdk", args)
        self.assertEqual(inspect_rows["sdk_install"], "auto")
        self.assertEqual(inspect_rows["auto_sdk"], "yes")
        self.assertEqual(inspect_rows["execution_mode"], "host")
        self.assertEqual(doctor_rows["sdk_install"], "auto")
        self.assertEqual(doctor_rows["execution_mode"], "host")

    def test_android_chroot_is_preferred_from_termux_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")

            with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr", "HOME": "/data/data/com.termux/files/home"}, clear=False), patch(
                "devtool.android_integration.shutil.which",
                return_value="/data/data/com.termux/files/usr/bin/chrootctl",
            ):
                self.assertTrue(should_prefer_android_chroot(config))

            with patch.dict(os.environ, {"PREFIX": "/data/data/com.termux/files/usr", "HOME": "/data/data/com.termux/files/home"}, clear=False), patch(
                "devtool.android_integration.shutil.which",
                return_value="/data/data/com.termux/files/usr/bin/chrootctl",
            ):
                native_config = ProjectConfig(
                    repo_root=root,
                    project_root=str(root),
                    kind="android",
                    runner_options={"android": {"prefer_native_termux": True}},
                )
                self.assertFalse(should_prefer_android_chroot(native_config))

    def test_revanced_asset_selection_prefers_arm_archives(self) -> None:
        release = {
            "assets": [
                {"name": "aapt2-linux-x86_64.zip"},
                {"name": "aapt2-arm64-v8a.zip"},
                {"name": "aapt2-armeabi-v7a.zip"},
            ]
        }

        arm64 = select_revanced_asset(release, "arm64")
        armv7 = select_revanced_asset(release, "armv7")

        self.assertEqual(arm64["name"], "aapt2-arm64-v8a.zip")
        self.assertEqual(armv7["name"], "aapt2-armeabi-v7a.zip")

    def test_maybe_reexec_in_chroot_delegates_termux_host_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            termux_home = repo / "termux-home"
            termux_home.mkdir()
            chroot_script = termux_home / ".cache" / "gradle-build" / "chroot" / "gradle-build.py"
            chroot_script.parent.mkdir(parents=True)
            chroot_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            command: list[str] = []

            class Result:
                returncode = 0

            def fake_run(argv, *args, **kwargs):
                command.extend(argv)
                return Result()

            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": str(termux_home),
                        "PREFIX": "/data/data/com.termux/files/usr",
                    },
                    clear=False,
                ),
                patch("devtool.android_build.running_under_termux_python", return_value=True),
                patch("devtool.android_build.chroot_is_available", return_value=(True, "")),
                patch("devtool.android_build.stage_builder_for_chroot", return_value=(chroot_script, Path("/home/termux/.cache/gradle-build/chroot/gradle-build.py"))),
                patch("devtool.android_build.Path.cwd", return_value=termux_home / "Code" / "demo"),
                patch("devtool.android_build.subprocess.run", side_effect=fake_run),
            ):
                rc = maybe_reexec_in_chroot(["--repo", str(termux_home / "Code" / "demo"), "validate"])

        self.assertEqual(rc, 0)
        self.assertEqual(command[:6], ["sudo", "chrootctl", "--name", "androidos", "exec", "--"])
        self.assertEqual(command[6], "zsh")
        self.assertEqual(command[7], "-ilc")
        self.assertIn("GRADLE_BUILD_CHROOT_ACTIVE=1", command[8])
        self.assertIn("/home/termux/Code/demo", command[8])


class AndroidConfigAndRunnerTests(unittest.TestCase):
    def test_auto_detected_nested_android_root_is_used_for_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            nested = repo / "app" / "XDM.Android"
            make_android_project(nested)
            config = load_project_config(str(repo), None)

        self.assertEqual(config.kind, "android")
        self.assertEqual(config.project_root, str(nested.resolve()))
        self.assertEqual(config.build_target, str(nested.resolve()))

    def test_named_android_target_overrides_mixed_repo_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Desktop.sln").write_text("", encoding="utf-8")
            android = root / "apps" / "android"
            make_android_project(android)
            (root / ".devtool.toml").write_text(
                '''[project]\nkind = "auto"\n\n[targets.mobile]\ntype = "android"\nroot = "apps/android"\n\n[targets.mobile.android]\nvariant = "release"\nprefer_native_termux = true\n\n[targets.mobile.gradle]\nmax_workers = 2\n\n[targets.mobile.tasks]\nbuild = ["assembleRelease"]\n\n[targets.mobile.artifacts]\noutput_directory = "artifacts/android"\n''',
                encoding="utf-8",
            )
            config = load_project_config(str(root), None, target_name="mobile")

        self.assertEqual(config.kind, "android")
        self.assertEqual(config.project_root, str(android.resolve()))
        self.assertEqual(config.runner_options["android"]["variant"], "release")
        self.assertEqual(config.runner_options["gradle"]["max_workers"], 2)
        self.assertEqual(config.runner_options["tasks"]["build"], ["assembleRelease"])
        self.assertIsInstance(get_project_runner(config), AndroidGradleRunner)

    def test_android_build_and_validation_specs_use_staged_shared_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / ".state" / "build-apk"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                build_target=str(root),
                restore_target=str(root),
                runner_options={
                    "android": {"variant": "debug", "aapt2_provider": "auto"},
                    "gradle": {"max_workers": 2},
                    "tasks": {},
                    "artifacts": {"output_directory": str(root / "artifacts")},
                },
            )
            init_script = root / ".state" / "devtool-events.init.gradle"
            with patch.dict(os.environ, {
                "DEVTOOL_ANDROID_BUILDER": str(staged),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(init_script),
            }, clear=False):
                build = build_specs(config, "build")[0]
                validation = build_validation_specs(config)
                init_script_exists = init_script.is_file()

        self.assertEqual(build.argv[0], "python3")
        self.assertEqual(Path(build.argv[1]), staged)
        self.assertIn("assembleDebug", build.argv)
        self.assertIn("--aapt2-provider", build.argv)
        self.assertIn("--output-dir", build.argv)
        self.assertEqual([spec.name for spec in validation], ["restore", "build", "test", "lint"])
        self.assertEqual(validation[-1].metadata["validation_scope"], "android-full")
        self.assertEqual(validation[2].metadata["report_parser"], "gradle-junit")
        self.assertEqual(validation[3].metadata["report_parser"], "android-lint")
        self.assertTrue(all(spec.metadata["progress_parser"] == "gradle" for spec in validation))
        self.assertTrue(init_script_exists)

    def test_configured_tasks_override_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"tasks": {"build": [":app:assembleRelease"]}},
            )
            with patch.dict(os.environ, {"DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "init.gradle")}, clear=False):
                args = android_builder_arguments(config, "build")

        self.assertEqual(configured_tasks(config, "build"), [":app:assembleRelease"])
        self.assertIn(":app:assembleRelease", args)

    def test_inspect_and_doctor_rows_are_available_without_running_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / "state" / "build-apk"
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android")
            with patch.dict(os.environ, {
                "DEVTOOL_ANDROID_BUILDER": str(staged),
                "DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "state" / "init.gradle"),
            }, clear=False):
                inspect_rows = dict(android_inspect_rows(config))
                doctor_rows = dict(android_doctor_rows(config))

            self.assertEqual(inspect_rows["project_type"], "Android application")
            self.assertEqual(inspect_rows["compile_sdk"], "35")
            self.assertIn("android_project", doctor_rows)
            self.assertTrue(staged.is_file())

    def test_android_test_and_lint_tasks_are_inferred_from_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            staged = root / "state" / "build-apk"
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"android": {"variant": "release"}},
            )
            init_script = root / "state" / "events.gradle"
            with patch.dict(
                os.environ,
                {
                    "DEVTOOL_ANDROID_BUILDER": str(staged),
                    "DEVTOOL_GRADLE_INIT_SCRIPT": str(init_script),
                },
                clear=False,
            ):
                test_spec = build_specs(config, "test")[0]
                lint_spec = build_specs(config, "lint")[0]

        self.assertIn("testReleaseUnitTest", test_spec.argv)
        self.assertIn("lintRelease", lint_spec.argv)
        self.assertIn("--no-artifact-scan", test_spec.argv)
        self.assertIn("--init-script", test_spec.argv)
        self.assertEqual(test_spec.argv.count("--no-configuration-cache"), 1)
        self.assertEqual(test_spec.metadata["report_parser"], "gradle-junit")
        self.assertEqual(lint_spec.metadata["report_parser"], "android-lint")


    def test_structured_gradle_events_do_not_duplicate_console_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_android_project(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"gradle": {"structured_events": True}},
            )
            with patch.dict(os.environ, {"DEVTOOL_GRADLE_INIT_SCRIPT": str(root / "events.gradle")}, clear=False):
                args = android_builder_arguments(config, "build")

        self.assertIn("--init-script", args)
        self.assertEqual(args.count("--no-configuration-cache"), 1)
        self.assertNotIn("--configuration-cache", args)
        self.assertEqual([value for value in args if str(value).startswith("--console")], [])

    def test_gradle_console_arguments_are_normalized_with_last_explicit_value_winning(self) -> None:
        self.assertEqual(
            normalize_gradle_arguments(["--init-script", "/tmp/events.gradle", "--console=plain", "--console", "rich"]),
            ["--init-script", "/tmp/events.gradle", "--console=rich"],
        )
        self.assertEqual(
            normalize_gradle_arguments(["--stacktrace", "--console=plain", "--console=plain"]),
            ["--stacktrace", "--console=plain"],
        )
        self.assertEqual(normalize_gradle_arguments(["--stacktrace"]), ["--stacktrace", "--console=plain"])
        self.assertEqual(normalize_gradle_arguments(["--stacktrace"], plain_console=False), ["--stacktrace"])

    def test_staged_gradle_init_script_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "devtool-events.init.gradle"
            staged = stage_gradle_init_script(destination=target)
            text = staged.read_text(encoding="utf-8")

        self.assertEqual(staged, target.resolve())
        self.assertIn("DEVTOOL_EVENT", text)
        self.assertIn("task-graph", text)
        self.assertIn("test-finish", text)

    def test_staged_builder_is_self_contained_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "build-apk"
            staged = stage_android_builder(destination=target)
            text = staged.read_text(encoding="utf-8")

            self.assertTrue(os.access(staged, os.X_OK))
            self.assertIn("def standalone_main", text)
            self.assertNotIn("from devtool", text)

    def test_find_java_home_prefers_exact_toolchain_match_over_newer_jdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            java17 = root / "java-17-openjdk-amd64"
            java21 = root / "java-21-openjdk-amd64"
            for home, version in ((java17, "17.0.19"), (java21, "21.0.11")):
                bin_dir = home / "bin"
                bin_dir.mkdir(parents=True)
                script = (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"-version\" ]; then\n"
                    f"  echo 'openjdk version \"{version}\"' 1>&2\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 0\n"
                )
                for name in ("java", "javac"):
                    path = bin_dir / name
                    path.write_text(script, encoding="utf-8")
                    path.chmod(0o755)

            with patch("devtool.android_build.java_home_candidates", return_value=[java21, java17]):
                selected = find_java_home(None, 17, exact=17)

        self.assertEqual(selected, java17)


if __name__ == "__main__":
    unittest.main()
