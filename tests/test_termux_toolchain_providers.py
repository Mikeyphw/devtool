from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool import android_build
from devtool.android_integration import android_builder_arguments_for_tasks, android_doctor_rows, android_inspect_rows
from devtool.cli import _apply_runner_cli_options, build_parser
from devtool.config import load_project_config, minimal_global_config_template
from devtool.models import ProjectConfig
from devtool.termux_toolchains import (
    PackageProbe,
    RuntimeIdentity,
    ToolProvider,
    VALID_GRADLE_PROVIDERS,
    VALID_JAVA_PROVIDERS,
    classify_executable_provider,
    detect_runtime_identity,
    gradle_wrapper_probe,
    normalize_gradle_provider,
    normalize_java_provider,
    termux_gradle_probe,
    termux_installed_packages,
    termux_java_providers,
    termux_jvm_roots,
    termux_package_probe,
)
from devtool.toolchains import toolchain_doctor_rows


def _write_java(home: Path, version: str = "21.0.8") -> None:
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-version\" ]; then\n"
        f"  echo 'openjdk version \"{version}\"' 1>&2\n"
        "fi\n"
        "exit 0\n"
    )
    for name in ("java", "javac"):
        path = bin_dir / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)


def _write_gradle(path: Path, version: str = "8.13") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        f"  echo 'Gradle {version}'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_wrapper(root: Path, version: str = "8.13", *, jar: bool = True) -> Path:
    wrapper_dir = root / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    (wrapper_dir / "gradle-wrapper.properties").write_text(
        f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n",
        encoding="utf-8",
    )
    if jar:
        (wrapper_dir / "gradle-wrapper.jar").write_bytes(b"wrapper")
    launcher = root / "gradlew"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _write_standalone_wrapper(root: Path, version: str = "8.13") -> Path:
    launcher = root / "gradlew"
    launcher.write_text(
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        f"VERSION={version}\n"
        'BASE="${GRADLE_USER_HOME:-$HOME/.gradle}/devtool-bootstrap"\n'
        'HOME_DIR="$BASE/gradle-$VERSION"\n'
        'URL="https://services.gradle.org/distributions/gradle-$VERSION-bin.zip"\n'
        'exec "$HOME_DIR/bin/gradle" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


class TermuxProviderFoundationTests(unittest.TestCase):
    def test_standalone_builder_and_shared_provider_contracts_stay_in_sync(self) -> None:
        self.assertEqual(android_build.JAVA_PROVIDERS, VALID_JAVA_PROVIDERS)
        self.assertEqual(android_build.GRADLE_PROVIDERS, VALID_GRADLE_PROVIDERS)

    def test_detect_runtime_identity_requires_native_termux_executable(self) -> None:
        runtime = detect_runtime_identity(
            {
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "ANDROID_API": "36",
            },
            executable="/data/data/com.termux/files/usr/bin/python3",
            machine="aarch64",
        )
        self.assertTrue(runtime.is_termux)
        self.assertEqual(runtime.platform, "android")
        self.assertEqual(runtime.libc, "bionic")
        self.assertEqual(runtime.arch, "arm64")
        self.assertEqual(runtime.android_api, 36)

        inherited = detect_runtime_identity(
            {
                "PREFIX": "/data/data/com.termux/files/usr",
                "TERMUX_VERSION": "0.119",
                "DEVTOOL_INSIDE_TERMUX_CHROOT": "1",
            },
            executable="/usr/bin/python3",
            machine="aarch64",
        )
        self.assertFalse(inherited.is_termux)

    def test_runtime_identity_falls_back_to_android_getprop_for_api_level(self) -> None:
        completed = subprocess.CompletedProcess(["getprop"], 0, stdout="35\n", stderr="")
        with patch("devtool.termux_toolchains.shutil.which", return_value="/system/bin/getprop"), patch(
            "devtool.termux_toolchains.subprocess.run", return_value=completed
        ) as run:
            runtime = detect_runtime_identity(
                {
                    "PREFIX": "/data/data/com.termux/files/usr",
                    "TERMUX_VERSION": "0.119",
                    "PATH": "/data/data/com.termux/files/usr/bin:/system/bin",
                },
                executable="/data/data/com.termux/files/usr/bin/python3",
                machine="aarch64",
            )
        self.assertTrue(runtime.is_termux)
        self.assertEqual(runtime.android_api, 35)
        self.assertEqual(run.call_args.args[0][-1], "ro.build.version.sdk")

    def test_executable_provider_classification_uses_runtime_prefix(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        self.assertEqual(classify_executable_provider("/prefix/bin/gradle", runtime=runtime), "termux")
        self.assertEqual(classify_executable_provider("/usr/bin/gradle", runtime=runtime), "system")

    def test_provider_normalizers_accept_supported_values_and_aliases(self) -> None:
        self.assertEqual(normalize_java_provider("termux"), "termux")
        self.assertEqual(normalize_gradle_provider("managed"), "download")
        with self.assertRaises(ValueError):
            normalize_java_provider("download")
        with self.assertRaises(ValueError):
            normalize_gradle_provider("random")

    def test_termux_package_probe_reads_dpkg_version_without_installing(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        completed = subprocess.CompletedProcess(
            ["dpkg-query"], 0, stdout="install ok installed\t1:9.7.0\n", stderr=""
        )
        with patch("devtool.termux_toolchains.shutil.which", return_value="/prefix/bin/dpkg-query"), patch(
            "devtool.termux_toolchains.subprocess.run", return_value=completed
        ) as run:
            result = termux_package_probe("gradle", env={"PATH": "/prefix/bin"}, runtime=runtime)
        self.assertTrue(result.installed)
        self.assertEqual(result.version, "1:9.7.0")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "gradle")

    def test_termux_installed_packages_filters_installed_openjdk_versions(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        completed = subprocess.CompletedProcess(
            ["dpkg-query"], 0,
            stdout=(
                "openjdk-21\tinstall ok installed\t21.0.8\n"
                "openjdk-25\tinstall ok installed\t25.0.1\n"
                "openjdk-17\tdeinstall ok config-files\t17.0.1\n"
            ),
            stderr="",
        )
        with patch("devtool.termux_toolchains.shutil.which", return_value="/prefix/bin/dpkg-query"), patch(
            "devtool.termux_toolchains.subprocess.run", return_value=completed
        ):
            packages = termux_installed_packages("openjdk-*", env={"PATH": "/prefix/bin"}, runtime=runtime)
        self.assertEqual([(item.name, item.version) for item in packages], [("openjdk-21", "21.0.8"), ("openjdk-25", "25.0.1")])

    def test_termux_gradle_probe_and_jvm_roots_use_termux_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            gradle = prefix / "bin" / "gradle"
            _write_gradle(gradle, "9.7.0")
            jvm = prefix / "lib" / "jvm" / "java-21-openjdk"
            _write_java(jvm)
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            package = PackageProbe("gradle", True, "1:9.7.0", "dpkg")
            with patch("devtool.termux_toolchains.shutil.which", return_value=str(gradle)), patch(
                "devtool.termux_toolchains.termux_package_probe", return_value=package
            ):
                probe = termux_gradle_probe(env={"PATH": str(prefix / "bin")}, runtime=runtime)
            roots = termux_jvm_roots(runtime)
        self.assertTrue(probe.available)
        self.assertEqual(probe.provider, "termux")
        self.assertEqual(probe.path, gradle.resolve())
        self.assertEqual(probe.version, "9.7.0")
        self.assertEqual(probe.package_version, "1:9.7.0")
        self.assertEqual(probe.package_manager, "dpkg")
        self.assertEqual(roots, (jvm,))
        self.assertEqual(probe.capabilities, ("gradle",))

    def test_termux_gradle_probe_marks_unparseable_launcher_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            gradle = prefix / "bin" / "gradle"
            gradle.parent.mkdir(parents=True, exist_ok=True)
            gradle.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            gradle.chmod(0o755)
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            with patch("devtool.termux_toolchains.shutil.which", return_value=str(gradle)), patch(
                "devtool.termux_toolchains.termux_package_probe",
                return_value=PackageProbe("gradle", True, "1:9.7.0", "dpkg"),
            ):
                probe = termux_gradle_probe(env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertTrue(probe.available)
        self.assertFalse(probe.compatible)
        self.assertFalse(probe.version)
        self.assertIn("could not be parsed", probe.rejection_reason)
        self.assertEqual(probe.capabilities, ("gradle",))


    def test_termux_java_provider_probe_reports_runtime_and_package_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            jvm = prefix / "lib" / "jvm" / "java-21-openjdk"
            _write_java(jvm, "21.0.8")
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            package = PackageProbe("openjdk-21", True, "21.0.8-1", "dpkg")
            with patch(
                "devtool.termux_toolchains.termux_installed_packages",
                return_value=(package,),
            ):
                providers = termux_java_providers(env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertEqual(len(providers), 1)
        provider = providers[0]
        self.assertTrue(provider.available)
        self.assertEqual(provider.provider, "termux")
        self.assertEqual(provider.path, jvm)
        self.assertEqual(provider.version, "21.0.8")
        self.assertEqual(provider.package, "openjdk-21")
        self.assertEqual(provider.package_version, "21.0.8-1")
        self.assertEqual(provider.package_manager, "dpkg")
        self.assertTrue(provider.compatible)
        self.assertEqual(provider.capabilities, ("java", "javac"))

    def test_termux_java_provider_marks_unparseable_jdk_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            jvm = prefix / "lib" / "jvm" / "java-21-openjdk"
            bin_dir = jvm / "bin"
            bin_dir.mkdir(parents=True)
            for name in ("java", "javac"):
                binary = bin_dir / name
                binary.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                binary.chmod(0o755)
            runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix)
            with patch(
                "devtool.termux_toolchains.termux_installed_packages",
                return_value=(PackageProbe("openjdk-21", True, "21.0.8-1", "dpkg"),),
            ):
                providers = termux_java_providers(env={"PATH": str(prefix / "bin")}, runtime=runtime)
        self.assertEqual(len(providers), 1)
        provider = providers[0]
        self.assertTrue(provider.available)
        self.assertFalse(provider.compatible)
        self.assertFalse(provider.version)
        self.assertIn("could not be parsed", provider.rejection_reason)
        self.assertEqual(provider.capabilities, ("java", "javac"))

    def test_termux_package_probe_is_read_only_on_missing_or_failed_dpkg(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))
        with patch("devtool.termux_toolchains.shutil.which", return_value=None):
            missing = termux_package_probe("gradle", env={"PATH": "/prefix/bin"}, runtime=runtime)
        self.assertFalse(missing.installed)
        self.assertIn("dpkg-query not available", missing.reason)

        with patch("devtool.termux_toolchains.shutil.which", return_value="/prefix/bin/dpkg-query"), patch(
            "devtool.termux_toolchains.subprocess.run", side_effect=subprocess.TimeoutExpired("dpkg-query", 5)
        ):
            timed_out = termux_package_probe("gradle", env={"PATH": "/prefix/bin"}, runtime=runtime)
        self.assertFalse(timed_out.installed)
        self.assertEqual(timed_out.manager, "dpkg")
        self.assertTrue(timed_out.reason)

    def test_wrapper_probe_requires_launcher_properties_and_jar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wrapper(root, jar=False)
            state = gradle_wrapper_probe(root)
        self.assertTrue(state.present)
        self.assertFalse(state.complete)
        self.assertEqual(state.missing, ("gradle/wrapper/gradle-wrapper.jar",))

    def test_toolchain_doctor_reports_termux_package_metadata(self) -> None:
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="python")
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"))

        def package(name: str, **_kwargs) -> PackageProbe:
            versions = {"gradle": "1:9.7.0"}
            return PackageProbe(name, name in versions, versions.get(name, ""), "dpkg")

        with patch("devtool.toolchains.detect_runtime_identity", return_value=runtime), patch(
            "devtool.toolchains.termux_package_probe", side_effect=package
        ), patch(
            "devtool.toolchains.termux_installed_packages",
            return_value=(PackageProbe("openjdk-21", True, "21.0.8", "dpkg"),),
        ), patch(
            "devtool.toolchains.termux_gradle_probe",
            return_value=ToolProvider(
                "gradle", "termux", True, path=Path("/prefix/bin/gradle"), version="9.7.0", runtime=runtime,
                compatible=True, capabilities=("gradle",)
            ),
        ), patch(
            "devtool.toolchains.termux_java_providers",
            return_value=(ToolProvider(
                "java", "termux", True, path=Path("/prefix/lib/jvm/java-21-openjdk"), version="21.0.8", runtime=runtime,
                compatible=True, capabilities=("java", "javac")
            ),),
        ), patch("devtool.toolchains.shutil.which", return_value=None):
            rows = dict(toolchain_doctor_rows(config, {"PATH": "/prefix/bin"}))
        self.assertEqual(rows["runtime_environment"], "termux")
        self.assertEqual(rows["runtime_abi"], "android/bionic/arm64")
        self.assertEqual(rows["android_api"], "unknown")
        self.assertEqual(rows["termux_package_gradle"], "1:9.7.0")
        self.assertIn("9.7.0 @ /prefix/bin/gradle", rows["termux_gradle_provider"])
        self.assertIn("openjdk-21=21.0.8", rows["termux_package_java"])
        self.assertIn("21.0.8 @ /prefix/lib/jvm/java-21-openjdk", rows["termux_java_provider"])

    def test_toolchain_doctor_distinguishes_detected_but_unusable_providers(self) -> None:
        runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", Path("/prefix"), 35)
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="gradle", runner_options={})
        with patch("devtool.toolchains.detect_runtime_identity", return_value=runtime), patch(
            "devtool.toolchains.termux_package_probe", return_value=PackageProbe("gradle", True, "1:9.7.0", "dpkg")
        ), patch("devtool.toolchains.termux_installed_packages", return_value=()), patch(
            "devtool.toolchains.termux_gradle_probe",
            return_value=ToolProvider(
                "gradle", "termux", True, path=Path("/prefix/bin/gradle"), runtime=runtime,
                compatible=False, rejection_reason="gradle --version could not be parsed"
            ),
        ), patch(
            "devtool.toolchains.termux_java_providers",
            return_value=(ToolProvider(
                "java", "termux", True, path=Path("/prefix/lib/jvm/java-21-openjdk"), runtime=runtime,
                compatible=False, rejection_reason="java -version could not be parsed"
            ),),
        ), patch("devtool.toolchains.shutil.which", return_value=None):
            rows = dict(toolchain_doctor_rows(config, {"PATH": "/prefix/bin"}))
        self.assertEqual(rows["android_api"], "35")
        self.assertIn("UNUSABLE", rows["termux_gradle_provider"])
        self.assertIn("gradle --version could not be parsed", rows["termux_gradle_provider"])
        self.assertIn("UNUSABLE", rows["termux_java_provider"])
        self.assertIn("java -version could not be parsed", rows["termux_java_provider"])


class AndroidJavaGradleProviderTests(unittest.TestCase):
    def test_system_java_provider_does_not_auto_install_termux_jdk(self) -> None:
        with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
            "devtool.android_build.run"
        ) as run:
            with self.assertRaisesRegex(
                android_build.BuildError,
                "automatic Termux package installation only supplies Termux JDKs",
            ):
                android_build.install_missing_system_dependencies(
                    need_java=True,
                    need_certificates=False,
                    offline=False,
                    java_version=21,
                    java_provider="system",
                )
        run.assert_not_called()

    def test_java_provider_filters_termux_jdk_and_reports_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            java_home = prefix / "lib" / "jvm" / "java-21-openjdk"
            _write_java(java_home)
            env = {
                "PREFIX": str(prefix),
                "TERMUX_VERSION": "0.119",
                "PATH": str(prefix / "bin"),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ):
                candidates = android_build.java_home_candidates(None, "termux")
                selected = android_build.find_java_home(None, 17, provider="termux")
                provider = android_build.executable_provider(selected / "bin" / "java")
        self.assertIn(java_home.resolve(), candidates)
        self.assertEqual(selected, java_home.resolve())
        self.assertEqual(provider, "termux")

    def test_auto_java_prefers_termux_jdk_over_foreign_system_jdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "termux" / "usr"
            termux_java = prefix / "lib" / "jvm" / "java-21-openjdk"
            system_java = root / "usr" / "lib" / "jvm" / "java-17-openjdk"
            _write_java(termux_java, "21.0.8")
            _write_java(system_java, "17.0.12")
            env = {
                "PREFIX": str(prefix),
                "TERMUX_VERSION": "0.119",
                "PATH": str(prefix / "bin"),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ), patch("devtool.android_build.java_home_candidates", return_value=[system_java, termux_java]):
                selected = android_build.find_java_home(None, 17, provider="auto")
        self.assertEqual(selected, termux_java)

    def test_self_bootstrap_gradle_wrapper_is_complete_and_pins_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = _write_standalone_wrapper(root, "8.13")
            state = android_build.gradle_wrapper_state(root)
            self.assertTrue(state.present)
            self.assertTrue(state.complete)
            self.assertEqual(state.missing, ())
            self.assertEqual(android_build.wrapper_distribution_version(root), "8.13")
            self.assertEqual(android_build.standalone_gradle_wrapper_version(root), "8.13")
            self.assertTrue(wrapper.is_file())

    def test_auto_gradle_accepts_supported_self_bootstrap_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = _write_standalone_wrapper(root, "8.13")
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.shutil.which", return_value=None
            ):
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                )
            self.assertEqual(selected, wrapper.resolve())

    def test_random_gradlew_without_wrapper_metadata_remains_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "gradlew"
            launcher.write_text("#!/bin/sh\nexec gradle \"$@\"\n", encoding="utf-8")
            launcher.chmod(0o755)
            state = android_build.gradle_wrapper_state(root)
            self.assertTrue(state.present)
            self.assertFalse(state.complete)
            self.assertIn("gradle/wrapper/gradle-wrapper.properties", state.missing)
            self.assertIn("gradle/wrapper/gradle-wrapper.jar", state.missing)

    def test_auto_gradle_refuses_incomplete_wrapper_instead_of_silent_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wrapper(root, jar=False)
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.shutil.which", return_value="/data/data/com.termux/files/usr/bin/gradle"
            ):
                with self.assertRaises(android_build.BuildError) as raised:
                    android_build.select_gradle(
                        root,
                        explicit=None,
                        version=None,
                        cache_root=root / ".cache",
                        offline=False,
                        force=False,
                    )
        message = str(raised.exception)
        self.assertIn("Project Gradle wrapper is incomplete", message)
        self.assertIn("gradle/wrapper/gradle-wrapper.jar", message)
        self.assertIn("Termux Gradle is available", message)
        self.assertIn("--gradle-provider", message)

    def test_auto_gradle_prefers_complete_project_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = _write_wrapper(root)
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.shutil.which", return_value="/unused/gradle"
            ):
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                )
        self.assertEqual(selected, wrapper.resolve())

    def test_explicit_termux_gradle_provider_uses_termux_packaged_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "termux" / "usr"
            gradle = prefix / "bin" / "gradle"
            _write_gradle(gradle)
            env = {
                "PREFIX": str(prefix),
                "TERMUX_VERSION": "0.119",
                "PATH": str(prefix / "bin"),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ):
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version="8.13",
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                    provider="termux",
                )
        self.assertEqual(selected, gradle.resolve())

    def test_system_gradle_provider_rejects_termux_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "termux" / "usr"
            gradle = prefix / "bin" / "gradle"
            _write_gradle(gradle)
            env = {
                "PREFIX": str(prefix),
                "TERMUX_VERSION": "0.119",
                "PATH": str(prefix / "bin"),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ):
                with self.assertRaisesRegex(android_build.BuildError, "Requested Gradle provider system"):
                    android_build.select_gradle(
                        root,
                        explicit=None,
                        version="8.13",
                        cache_root=root / ".cache",
                        offline=False,
                        force=False,
                        provider="system",
                    )


    def test_explicit_java_home_is_authoritative_even_when_provider_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit-jdk"
            _write_java(explicit, "21.0.8")
            with patch("devtool.android_build.java_home_candidates") as candidates:
                selected = android_build.find_java_home(explicit, 17, provider="termux")
        self.assertEqual(selected, explicit.resolve())
        candidates.assert_not_called()

    def test_explicit_java_home_fails_if_it_cannot_meet_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "jdk"
            _write_java(home, "17.0.12")
            with self.assertRaisesRegex(android_build.BuildError, "Java 21\\+ is required"):
                android_build.find_java_home(home, 21, provider="auto")

    def test_termux_java_provider_rejects_non_termux_without_explicit_home(self) -> None:
        with patch("devtool.android_build.running_under_termux_python", return_value=False):
            with self.assertRaisesRegex(android_build.BuildError, "requires native Termux"):
                android_build.find_java_home(None, 17, provider="termux")

    def test_explicit_gradle_command_is_authoritative_when_project_has_no_version_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradle = root / "gradle-9.7"
            _write_gradle(gradle, "9.7.0")
            selected = android_build.select_gradle(
                root,
                explicit=str(gradle),
                version=None,
                cache_root=root / ".cache",
                offline=False,
                force=False,
                provider="download",
            )
        self.assertEqual(selected, gradle.resolve())

    def test_explicit_gradle_command_still_honors_wrapper_version_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wrapper(root, "8.13")
            gradle = root / "gradle-9.7"
            _write_gradle(gradle, "9.7.0")
            with self.assertRaisesRegex(android_build.BuildError, "requires Gradle 8.13"):
                android_build.select_gradle(
                    root,
                    explicit=str(gradle),
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                )

    def test_explicit_wrapper_provider_selects_complete_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = _write_wrapper(root, "8.13")
            selected = android_build.select_gradle(
                root,
                explicit=None,
                version=None,
                cache_root=root / ".cache",
                offline=False,
                force=False,
                provider="wrapper",
            )
        self.assertEqual(selected, wrapper.resolve())

    def test_explicit_download_provider_bypasses_wrapper_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wrapper(root, "8.13")
            managed = root / ".cache" / "gradle" / "gradle-8.13" / "bin" / "gradle"
            with patch("devtool.android_build.managed_gradle", return_value=managed) as download, patch(
                "devtool.android_build.shutil.which"
            ) as which:
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                    provider="download",
                )
        self.assertEqual(selected, managed)
        download.assert_called_once()
        which.assert_not_called()

    def test_termux_gradle_provider_rejects_non_termux_before_path_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "devtool.android_build.running_under_termux_python", return_value=False
        ), patch("devtool.android_build.shutil.which") as which:
            with self.assertRaisesRegex(android_build.BuildError, "requires native Termux"):
                android_build.select_gradle(
                    Path(tmp),
                    explicit=None,
                    version=None,
                    cache_root=Path(tmp) / ".cache",
                    offline=False,
                    force=False,
                    provider="termux",
                )
        which.assert_not_called()

    def test_auto_wrapperless_generic_gradle_uses_current_termux_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "termux" / "usr"
            gradle = prefix / "bin" / "gradle"
            _write_gradle(gradle, "9.7.0")
            project = android_build.ProjectInfo(root, "demo", False, False, None, None, None, False, None, None)
            env = {"PREFIX": str(prefix), "TERMUX_VERSION": "0.119", "PATH": str(prefix / "bin")}
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ):
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                    provider="auto",
                    project=project,
                )
        self.assertEqual(selected, gradle.resolve())

    def test_auto_android_agp_generation_gates_unpinned_termux_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "termux" / "usr"
            gradle = prefix / "bin" / "gradle"
            _write_gradle(gradle, "9.7.0")
            compatible = android_build.ProjectInfo(root, "demo", True, True, 36, None, 9, False, None, None)
            incompatible = android_build.ProjectInfo(root, "demo", True, True, 35, None, 8, False, None, None)
            managed = root / ".cache" / "gradle" / "gradle-8.13" / "bin" / "gradle"
            env = {"PREFIX": str(prefix), "TERMUX_VERSION": "0.119", "PATH": str(prefix / "bin")}
            with patch.dict(os.environ, env, clear=True), patch.object(
                android_build.sys, "executable", str(prefix / "bin" / "python3")
            ):
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version=None,
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                    project=compatible,
                )
                with patch("devtool.android_build.managed_gradle", return_value=managed):
                    fallback = android_build.select_gradle(
                        root,
                        explicit=None,
                        version=None,
                        cache_root=root / ".cache",
                        offline=False,
                        force=False,
                        project=incompatible,
                    )
        self.assertEqual(selected, gradle.resolve())
        self.assertEqual(fallback, managed)

    def test_auto_wrapper_warns_when_cli_version_differs_but_wrapper_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = _write_wrapper(root, "8.13")
            with patch("devtool.android_build.warn") as warn:
                selected = android_build.select_gradle(
                    root,
                    explicit=None,
                    version="9.0",
                    cache_root=root / ".cache",
                    offline=False,
                    force=False,
                    provider="auto",
                )
        self.assertEqual(selected, wrapper.resolve())
        self.assertTrue(any("wrapper remains authoritative" in str(call.args[0]) for call in warn.call_args_list))

    def test_standalone_builder_parser_accepts_provider_flags(self) -> None:
        args = android_build.parse_args([
            ".",
            "--java-provider", "termux",
            "--gradle-provider", "download",
        ])
        self.assertEqual(args.java_provider, "termux")
        self.assertEqual(args.gradle_provider, "download")

    def test_android_builder_threads_java_and_gradle_provider_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle.kts").write_text('rootProject.name="demo"\n', encoding="utf-8")
            (root / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="gradle",
                runner_options={
                    "android": {"java": {"provider": "termux"}},
                    "gradle": {"provider": "system"},
                },
            )
            args = android_builder_arguments_for_tasks(config, ["build"], scan_artifacts=False)
            rows = dict(android_inspect_rows(config))
            with patch("devtool.android_integration.android_build.find_java_home", side_effect=android_build.BuildError("missing")), patch(
                "devtool.android_integration.android_build.discover_sdk_root_with_source", return_value=(None, None)
            ), patch("devtool.android_integration.stage_android_builder", return_value=root / "build-apk"):
                doctor = dict(android_doctor_rows(config))
        self.assertEqual(args[args.index("--java-provider") + 1], "termux")
        self.assertEqual(args[args.index("--gradle-provider") + 1], "system")
        self.assertEqual(rows["java_provider"], "termux")
        self.assertEqual(rows["gradle_provider"], "system")
        self.assertEqual(doctor["java_provider"], "termux")
        self.assertEqual(doctor["gradle_provider"], "system")

    def test_cli_provider_overrides_update_runner_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--java-provider", "termux",
            "--gradle-provider", "wrapper",
            "build",
        ])
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="android", runner_options={})
        _apply_runner_cli_options(config, args)
        self.assertEqual(config.runner_options["android"]["java"]["provider"], "termux")
        self.assertEqual(config.runner_options["gradle"]["provider"], "wrapper")

    def test_sparse_global_config_can_default_java_and_gradle_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
            (root / ".devtool.toml").write_text("[project]\nkind='gradle'\n", encoding="utf-8")
            xdg = Path(tmp) / "xdg"
            global_path = xdg / "devtool" / "config.toml"
            global_path.parent.mkdir(parents=True)
            global_path.write_text(
                "[android.java]\nprovider='termux'\n[gradle]\nprovider='system'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}, clear=False):
                config = load_project_config(str(root), None)
        self.assertEqual(config.runner_options["android"]["java"]["provider"], "termux")
        self.assertEqual(config.runner_options["gradle"]["provider"], "system")

    def test_global_and_repo_templates_document_provider_options(self) -> None:
        global_text = minimal_global_config_template()
        self.assertIn("[android.java]", global_text)
        self.assertIn("[gradle]", global_text)
        self.assertIn('provider = "auto" # auto | termux | system', global_text)
        self.assertIn('auto | wrapper | termux | system | download', global_text)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='gradle'\n[android.java]\nprovider='termux'\n[gradle]\nprovider='system'\n",
                encoding="utf-8",
            )
            config = load_project_config(str(root), None)
        self.assertEqual(config.runner_options["android"]["java"]["provider"], "termux")
        self.assertEqual(config.runner_options["gradle"]["provider"], "system")


if __name__ == "__main__":
    unittest.main()
