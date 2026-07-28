from pathlib import Path
import json
import shutil
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud"
JAVA = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase7_support_bundle_surface_is_exposed_and_privacy_gated() -> None:
    main = read("MainActivity.java")
    settings = read("AppSettings.java")
    bundle = read("SupportBundle.java")
    assert "buildSupportBundleSettings" in main
    assert "Copy support bundle" in main
    assert "Share support bundle" in main
    assert "Include redacted recent commands" in main
    assert "SupportBundle.build(this)" in main
    assert "KEY_SUPPORT_INCLUDE_COMMANDS" in settings
    assert "includeSupportCommands" in settings
    assert "setIncludeSupportCommands" in settings
    assert '"commands_included"' in bundle
    assert '"raw_log_output_included", false' in bundle
    assert '"bridge_token_included", false' in bundle
    assert '"command", includeCommand ?' in bundle
    assert '"<disabled>"' in bundle


def test_phase7_support_bundle_covers_field_diagnostics_without_raw_logs() -> None:
    bundle = read("SupportBundle.java")
    for key in (
        '"root_bridge"', '"settings"', '"database"', '"active_run"',
        '"recent_runs"', '"repositories"', '"operations"',
        '"root_log_indexer_busy"', '"repository_refresh_busy"',
        '"schema_version"', '"git_operations"',
    ):
        assert key in bundle
    assert "RunStore.all(context)" in bundle
    assert "RepositoryStore.all(context)" in bundle
    assert "repositoryOperations(repositoryId" in bundle
    assert "MAX_RUNS = 12" in bundle
    assert "MAX_REPOSITORIES = 24" in bundle
    assert "MAX_OPERATIONS_PER_REPOSITORY = 6" in bundle
    assert "stdout" not in bundle
    assert "stderr" not in bundle
    assert "launchLog" not in bundle


def test_phase7_redactor_removes_tokens_credentials_and_private_keys(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable")
    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(JAVA / "SupportRedactor.java", package / "SupportRedactor.java")
    (package / "SupportRedactorHarness.java").write_text(textwrap.dedent(r'''
        package io.github.mikeceead.devtoolhud;
        public final class SupportRedactorHarness {
            public static void main(String[] args) {
                String input = "token=abc password:sekret Authorization: Bearer deadbeef "
                    + "https://user:pass@example.com/repo.git bridge-token "
                    + "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----";
                String output = SupportRedactor.redact(input, "bridge-token");
                if (output.contains("abc")) throw new AssertionError(output);
                if (output.contains("sekret")) throw new AssertionError(output);
                if (output.contains("deadbeef")) throw new AssertionError(output);
                if (output.contains("user:pass")) throw new AssertionError(output);
                String withoutMarker = output.replace("<redacted:bridge-token>", "");
                if (withoutMarker.contains("bridge-token")) throw new AssertionError(output);
                if (output.contains("OPENSSH PRIVATE KEY")) throw new AssertionError(output);
                if (!output.contains("<redacted:bridge-token>")) throw new AssertionError(output);
                if (!output.contains("https://<redacted>@example.com/repo.git")) throw new AssertionError(output);
            }
        }
    '''), encoding="utf-8")
    subprocess.run([javac, "-Xlint:all", "-Werror", "-d", str(tmp_path), *map(str, package.glob("*.java"))],
                   check=True, capture_output=True, text=True)
    subprocess.run([java, "-cp", str(tmp_path), "io.github.mikeceead.devtoolhud.SupportRedactorHarness"],
                   check=True, capture_output=True, text=True)


def test_phase7_version_contract() -> None:
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    versions = json.loads((ROOT / "release" / "versions.json").read_text(encoding="utf-8"))
    assert "versionCode 30" in build
    assert "versionName '1.16.0'" in build
    assert versions["android"]["version_name"] == "1.16.0"
    assert versions["android"]["version_code"] == 30
