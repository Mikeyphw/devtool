from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import textwrap
import zipfile

import pytest

from devtool.release_qualification import (
    android_sdk_available,
    load_versions,
    qt_available,
    shell_entry_points,
    validate_version_consistency,
)


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "devtool-control-center"
ANDROID = ROOT / "android" / "devtool-live-hud"
JAVA = ANDROID / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_slice5_versions_are_canonical_and_consistent() -> None:
    versions = load_versions(ROOT)
    assert versions == {
        "schema_version": 1,
        "release": "1.1.0-rc.3",
        "devtool": "1.1.0",
        "desktop": "0.4.0",
        "android": {
            "version_name": "1.13.0",
            "version_code": 26,
            "application_id": "io.github.mikeceead.devtoolhud",
        },
            "minimums": {
                "python": "3.11",
                "qt": "5.15",
                "android_min_sdk": 26,
                "android_target_sdk": 36,
                "java": 17,
            },
        }
    assert validate_version_consistency(ROOT) == []


def test_release_qualification_matrix_covers_every_native_and_package_gate() -> None:
    matrix = json.loads(read(ROOT / "release" / "qualification-matrix.json"))
    gates = {gate["id"]: gate for gate in matrix["gates"]}
    assert set(gates) == {
        "versions", "python-compile", "python-tests", "shell-syntax",
        "qt-configure", "qt-build", "qt-smoke",
        "android-lint", "android-unit", "android-release", "android-instrumentation",
        "source-archive",
    }
    assert gates["versions"]["required"]
    assert gates["source-archive"]["required"]
    assert not gates["qt-build"]["required"]
    assert not gates["android-release"]["required"]


def test_release_qualifier_has_explicit_toolchain_skip_and_require_semantics() -> None:
    source = read(ROOT / "devtool" / "release_qualification.py")
    for token in (
        "require_qt", "require_android", "run_device_tests",
        "Qt 5 development files were not detected",
        "Android SDK platform 36 and build-tools 36.0.0 were not detected",
        "release signing environment is incomplete",
    ):
        assert token in source
    assert all(path.is_file() for path in shell_entry_points(ROOT))
    assert isinstance(qt_available(), bool)
    assert not android_sdk_available({})


def test_qt_release_build_has_version_and_offscreen_qml_smoke_tests() -> None:
    cmake = read(DESKTOP / "CMakeLists.txt")
    main = read(DESKTOP / "src" / "main.cpp")
    hud = read(DESKTOP / "src" / "HudMain.cpp")
    smoke = read(DESKTOP / "tests" / "run-headless-smoke.sh")
    for token in (
        "DEVTOOL_ENABLE_TESTS", "control-center-version", "control-center-hud-version",
        "control-center-qml-smoke", "control-center-hud-qml-smoke", "DEVTOOL_CC_VERSION",
    ):
        assert token in cmake
    assert 'hasArgument(argc, argv, "--version")' in main
    assert 'hasArgument(argc, argv, "--version")' in hud
    assert '"--exit-when-idle"' in hud
    for token in ("QT_QPA_PLATFORM", "offscreen", "QT_QUICK_BACKEND", "QSG_RHI_BACKEND", "timeout 15"):
        assert token in smoke


def test_desktop_package_is_reproducible_checksummed_and_self_verifying() -> None:
    package = read(DESKTOP / "package-deb.sh")
    for token in (
        "SOURCE_DATE_EPOCH", "touch -h -d", "--uniform-compression", "-Zxz",
        'sha256sum "$deb_path"', "--verify", "dpkg-deb --contents", "Package verification passed",
    ):
        assert token in package


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


def test_android_release_models_compile_and_execute_without_android_framework(tmp_path: Path) -> None:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")
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
    classes = tmp_path / "classes"
    classes.mkdir()
    subprocess.run(["javac", "-d", str(classes), *map(str, package.glob("*.java"))], check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["java", "-cp", str(classes), "io.github.mikeceead.devtoolhud.ReleaseHarness"],
        check=True, capture_output=True, text=True,
    )
    assert completed.stdout.splitlines() == [
        "test", "$HOME/Code/demo repo", "mobile", "/data/data/com.termux/files/home/Code/demo", "true",
    ]


def test_ci_matrix_builds_qt_signed_android_emulator_and_source_release() -> None:
    workflow = read(ROOT / ".github" / "workflows" / "release-qualification.yml")
    for job in ("core:", "qt:", "android:", "android-device:", "source-release:"):
        assert job in workflow
    for token in (
        "ctest --test-dir", "package-deb.sh --skip-build --verify",
        "lintRelease testReleaseUnitTest verifyReleaseApk",
        "connectedDebugAndroidTest", "android-emulator-runner",
        "build-release.py --out-dir dist/release --verify-reproducible",
        "tools/verify-identical.py", "app-release-first.apk",
        "desktop-first", "desktop-second",
        "tests/snapshots/ui",
    ):
        assert token in workflow


def test_source_release_archive_is_deterministic_and_well_formed(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    command = ["python3", str(ROOT / "tools" / "build-release.py"), "--root", str(ROOT), "--verify-reproducible"]
    subprocess.run([*command, "--out-dir", str(first)], check=True, capture_output=True, text=True)
    subprocess.run([*command, "--out-dir", str(second)], check=True, capture_output=True, text=True)
    archive_name = "devtool-1.1.0-source.zip"
    assert (first / "SHA256SUMS").read_bytes() == (second / "SHA256SUMS").read_bytes()
    assert (first / archive_name).read_bytes() == (second / archive_name).read_bytes()
    with zipfile.ZipFile(first / archive_name) as archive:
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
