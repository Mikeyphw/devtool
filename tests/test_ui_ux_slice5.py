from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from types import SimpleNamespace
import zipfile

import pytest

from devtool.release_qualification import (
    android_sdk_available,
    gtk4_available,
    load_versions,
    desktop_available,
    qt_available,
    shell_entry_points,
    validate_version_consistency,
)


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "gtk-control-center"
ANDROID = ROOT / "android" / "devtool-live-hud"
JAVA = ANDROID / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_slice5_versions_are_canonical_and_consistent() -> None:
    versions = load_versions(ROOT)
    assert versions["schema_version"] == 1
    assert versions["release"] == "1.1.0-rc.3"
    assert versions["devtool"] == "1.1.0"
    assert versions["desktop"] == "0.5.0"
    assert versions["android"]["application_id"] == "io.github.mikeceead.devtoolhud"
    assert versions["android"]["version_code"] >= 1
    assert versions["android"]["version_name"].count(".") >= 1
    assert versions["minimums"] == {
        "python": "3.11",
        "android_min_sdk": 26,
        "android_target_sdk": 36,
        "java": 17,
        "gtk": "4.0",
        "libadwaita": "1",
    }
    assert validate_version_consistency(ROOT) == []


def test_release_qualification_matrix_covers_every_native_and_package_gate() -> None:
    matrix = json.loads(read(ROOT / "release" / "qualification-matrix.json"))
    gates = {gate["id"]: gate for gate in matrix["gates"]}
    expected_gates = {
        "versions", "python-compile", "python-tests", "shell-syntax", "ui-ux-contract",
        "desktop-gtk-imports", "desktop-gtk-model-snapshot",
        "desktop-gtk-control-center-startup", "desktop-gtk-hud-startup",
        "desktop-gtk-control-center-constrained-layout", "desktop-gtk-hud-constrained-layout",
        "desktop-gtk-package",
        "android-lint", "android-unit", "android-release", "android-instrumentation",
        "source-archive",
    }
    assert expected_gates <= set(gates)
    assert gates["versions"]["required"]
    assert gates["source-archive"]["required"]
    assert gates["ui-ux-contract"]["required"]
    assert not gates["desktop-gtk-package"]["required"]
    assert not gates["android-release"]["required"]


def test_release_qualifier_has_explicit_toolchain_skip_and_require_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    source = read(ROOT / "devtool" / "release_qualification.py")
    for token in (
        "require_qt", "require_android", "run_device_tests",
        "GTK4/libadwaita Python bindings were not detected",
        "desktop-gtk-package",
        "Android SDK platform 36 and build-tools 36.0.0 were not detected",
        "release signing environment is incomplete",
    ):
        assert token in source
    assert all(path.is_file() for path in shell_entry_points(ROOT))

    # This is a release-policy contract, not a GTK/SDK integration benchmark.
    # Real runtime probes are covered by the dedicated GTK and Android provider
    # suites. Keep this test deterministic and cheap on native Termux.
    monkeypatch.setattr("devtool.desktop.gtk_smoke.gtk_runtime_import_probe", lambda _python: (True, "ok"))
    monkeypatch.setattr("devtool.desktop.gtk_smoke.has_display", lambda: True)
    monkeypatch.setattr(
        "devtool.release_qualification.resolve_android_execution_context",
        lambda *_args, **_kwargs: SimpleNamespace(sdk_root=""),
    )
    assert gtk4_available() is True
    assert desktop_available() is True
    assert qt_available() is True  # compatibility API alias, no Qt probing
    assert not android_sdk_available({})


def test_gtk_release_build_is_python_based_and_legacy_qt_tree_is_removed() -> None:
    build = read(DESKTOP / "build.sh")
    package = read(DESKTOP / "package-deb.sh")
    control = read(DESKTOP / "packaging" / "debian" / "control")
    assert "compileall" in build
    assert "no native build step is required" in build
    assert "usr/lib/python3/dist-packages/devtool" in package
    assert "devtool.desktop.control_center_cli" in package
    assert "devtool.desktop.gtk_hud_cli" in package
    assert "gir1.2-gtk-4.0" in control
    assert "gir1.2-adw-1" in control
    assert "libqt5" not in control.lower()
    assert not (ROOT / "desktop" / "devtool-control-center").exists()


def test_desktop_package_is_reproducible_checksummed_and_self_verifying() -> None:
    package = read(ROOT / "desktop" / "gtk-control-center" / "package-deb.sh")
    control = read(ROOT / "desktop" / "gtk-control-center" / "packaging" / "debian" / "control")
    for token in (
        "SOURCE_DATE_EPOCH", "touch -h -d", "--uniform-compression", "-Zxz",
        'sha256sum "$deb_path"', "--verify", "dpkg-deb --contents", "GTK package verification passed",
        "devtool-control-center-hud", "gir1.2-gtk-4.0", "gir1.2-adw-1",
    ):
        assert token in package or token in control
    assert "qml-module" not in control
    assert "libqt5" not in control


def test_android_release_fails_closed_and_has_local_and_device_tests() -> None:
    gradle = read(ANDROID / "app" / "build.gradle")
    assert "applicationIdSuffix '.debug'" in gradle
    assert "verifyReleaseSigning" in gradle
    assert "preReleaseBuild" in gradle
    assert "Release signing is required" in gradle
    assert "signingConfig signingConfigs.debug" not in gradle
    assert "verifyReleaseApk" in gradle
    assert "apksigner" in gradle and "--print-certs" in gradle
    assert (ANDROID / "app" / "src" / "test" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "ReleaseModelTest.java").is_file()
    smoke = read(ANDROID / "app" / "src" / "androidTest" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "MainActivitySmokeTest.java")
    assert "startActivitySync" in smoke
    assert "MainActivity.class" in smoke
    runner = read(JAVA / "TermuxCommandRunner.java")
    bridge = read(ROOT / "devtool" / "android_live_hud.py")
    assert "context.getPackageName()" in runner
    assert "BridgeReceiver.class.getName()" in runner
    assert "DEVTOOL_ANDROID_HUD_PACKAGE" in runner
    assert "DEVTOOL_ANDROID_HUD_COMPONENT" in runner
    assert "DEVTOOL_ANDROID_HUD_ACTION_PREFIX" in runner
    assert "DEVTOOL_ANDROID_HUD_PACKAGE" in bridge
    assert "DEVTOOL_ANDROID_HUD_COMPONENT" in bridge
    assert "io.github.mikeceead.devtoolhud.debug" in bridge


@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
@pytest.mark.slow_external
def test_android_release_models_compile_and_execute_without_android_framework(tmp_path: Path) -> None:
    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    for name in ("DevtoolCommandBuilder.java", "DevtoolCommandImporter.java", "DevtoolRunConfig.java", "TermuxPaths.java", "RunIdentity.java"):
        shutil.copy2(JAVA / name, package / name)
    (package / "ReleaseHarness.java").write_text(textwrap.dedent("""
        package io.github.mikeceead.devtoolhud;
        public final class ReleaseHarness {
            public static void main(String[] args) {
                DevtoolRunConfig config = new DevtoolRunConfig();
                config.operation = "test";
                config.repo = "$HOME/Code/demo repo";
                config.target = "mobile";
                config.testProfile = "quick";
                String command = DevtoolCommandBuilder.build(config);
                DevtoolCommandImporter.ImportResult imported = DevtoolCommandImporter.parse(command);
                System.out.println(imported.config.operation);
                System.out.println(imported.config.repo);
                System.out.println(imported.config.target);
                System.out.println(TermuxPaths.normalizeWorkdir("~/Code/demo"));
                System.out.println(RunIdentity.notificationId("run-a") == RunIdentity.notificationId("run-a"));
            }
        }
    """), encoding="utf-8")
    annotation = tmp_path / "android" / "annotation"
    annotation.mkdir(parents=True)
    (annotation / "SuppressLint.java").write_text(
        'package android.annotation; public @interface SuppressLint { String[] value(); }\n',
        encoding="utf-8",
    )
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(
        ["javac", "-d", str(classes), str(annotation / "SuppressLint.java"), *map(str, package.glob("*.java"))],
        check=True, capture_output=True, text=True,
    )
    completed = subprocess.run(
        ["java", "-cp", str(classes), "io.github.mikeceead.devtoolhud.ReleaseHarness"],
        check=True, capture_output=True, text=True,
    )
    assert completed.stdout.splitlines() == [
        "test", "$HOME/Code/demo repo", "mobile", "/data/data/com.termux/files/home/Code/demo", "true",
    ]


def test_ci_matrix_builds_gtk_desktop_signed_android_emulator_and_source_release() -> None:
    workflow = read(ROOT / ".github" / "workflows" / "release-qualification.yml")
    for job in ("core:", "gtk-desktop:", "android:", "android-device:", "source-release:"):
        assert job in workflow
    for token in (
        "desktop/gtk-control-center/package-deb.sh --verify", "devtool control-center --backend gtk4 --once",
        "lintRelease testReleaseUnitTest verifyReleaseApk",
        "connectedDebugAndroidTest", "android-emulator-runner",
        "build-release.py --out-dir dist/release --verify-reproducible",
        "tools/verify-identical.py", "app-release-first.apk",
        "desktop-first", "desktop-second",
        "tests/snapshots/ui",
    ):
        assert token in workflow


@pytest.mark.slow_external
def test_source_release_archive_is_deterministic_and_well_formed(tmp_path: Path) -> None:
    output = tmp_path / "release"
    command = [
        sys.executable, "-S", str(ROOT / "tools" / "build-release.py"),
        "--root", str(ROOT),
        "--verify-reproducible",
        "--out-dir", str(output),
    ]
    # --verify-reproducible already creates a second archive internally and
    # byte-compares it. Running the entire release builder twice duplicated the
    # most expensive work without adding coverage.
    subprocess.run(command, check=True, capture_output=True, text=True)
    archive_name = "devtool-1.1.0-source.zip"
    sums = (output / "SHA256SUMS").read_text(encoding="utf-8")
    assert archive_name in sums
    with zipfile.ZipFile(output / archive_name) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == sorted(names)
        assert all(name.startswith("devtool-1.1.0/") for name in names)
        assert not any("/__pycache__/" in name or name.endswith(".pyc") for name in names)


def test_release_candidate_docs_cover_upgrade_manual_matrix_and_publication() -> None:
    checklist = read(ROOT / "docs" / "RELEASE_CHECKLIST.md")
    migration = read(ROOT / "docs" / "MIGRATION_1.1.md")
    changelog = read(ROOT / "CHANGELOG.md")
    for token in ("640, 900, and 1440", "200%", "TalkBack", "process death", "signature mismatch rejection", "v1.1.0-rc.3"):
        assert token in checklist
    assert "Debug builds now use the `.debug` application ID suffix" in migration
    assert "Release tasks require an explicit keystore" in migration
    assert "1.1.0-rc.3" in changelog
