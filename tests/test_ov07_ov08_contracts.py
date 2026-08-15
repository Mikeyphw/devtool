from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool import android_build
from devtool.models import CommandSpec
from devtool.proot_distro_runner import ProotRunnerConfig, validate_proot_config, wrap_specs_for_proot_distro
from devtool.termux_runtime import adapt_termux_script_argv, export_artifacts, termux_wake_lock
from devtool.termux_toolchains import classify_native_requirement


def _termux_env(prefix: Path) -> dict[str, str]:
    return {"TERMUX_VERSION": "0.119", "PREFIX": str(prefix), "PATH": str(prefix / "bin"), "HOME": str(prefix.parent / "home")}


def test_proot_guest_paths_are_lexically_canonicalized() -> None:
    cfg = ProotRunnerConfig(distro="debian", cwd="//workspace/a/../project", repo_mount="/workspace/./project")
    validate_proot_config(cfg)
    assert cfg.cwd == "/workspace/project"
    assert cfg.repo_mount == "/workspace/project"


def test_proot_overlapping_guest_destination_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    vendor = tmp_path / "vendor"; vendor.mkdir()
    spec = CommandSpec("test", ["true"], cwd=str(repo))
    cfg = ProotRunnerConfig(distro="debian", binds=[f"{vendor}:/workspace/project/vendor"])
    with pytest.raises(ValueError, match="overlap"):
        wrap_specs_for_proot_distro([spec], repo_root=repo, config=cfg, dry_run=True)


def test_proot_requires_mapping_for_active_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    other = tmp_path / "other"; other.mkdir()
    spec = CommandSpec("test", ["true"], cwd=str(repo))
    cfg = ProotRunnerConfig(distro="debian", auto_bind_repo=False, binds=[f"{other}:/workspace/project"])
    with pytest.raises(ValueError, match="[Dd]oes not support validating an unrelated independent guest checkout"):
        wrap_specs_for_proot_distro([spec], repo_root=repo, config=cfg, dry_run=True)


def test_proot_does_not_stage_arbitrary_host_executable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    tool = tmp_path / "tool"; tool.write_text("#!/bin/sh\nexit 0\n"); tool.chmod(0o755)
    spec = CommandSpec("test", ["echo", str(tool)], cwd=str(repo))
    with pytest.raises(ValueError, match="not a typed PRoot data input"):
        wrap_specs_for_proot_distro([spec], repo_root=repo, config=ProotRunnerConfig(distro="debian"), dry_run=True)


def test_env_dash_s_shebang_uses_real_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = tmp_path / "usr"; (prefix / "bin").mkdir(parents=True)
    py = prefix / "bin" / "python3"; py.write_text("x"); py.chmod(0o755)
    script = tmp_path / "script.py"; script.write_text("#!/usr/bin/env -S python3 -I\nprint(1)\n"); script.chmod(0o755)
    env = _termux_env(prefix)
    monkeypatch.setattr("devtool.termux_runtime.is_termux_host", lambda _env: True)
    argv, _ = adapt_termux_script_argv([str(script), "x"], env=env)
    assert argv[:3] == [str(py), "-I", str(script)]


def test_export_artifacts_never_clobbers_different_same_basename(tmp_path: Path) -> None:
    a = tmp_path / "a" / "app.apk"; a.parent.mkdir(); a.write_bytes(b"A")
    b = tmp_path / "b" / "app.apk"; b.parent.mkdir(); b.write_bytes(b"B")
    out = tmp_path / "out"
    result = export_artifacts([a, b], out)
    assert len(result) == 2 and result[0] != result[1]
    assert sorted(path.read_bytes() for path in result) == [b"A", b"B"]


def test_wake_unlock_nonzero_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devtool.termux_runtime.is_termux_host", lambda _env: True)
    monkeypatch.setattr("devtool.termux_runtime.shutil.which", lambda name: f"/bin/{name}")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0 if "wake-lock" in argv[0] and "unlock" not in argv[0] else 7)
    monkeypatch.setattr("devtool.termux_runtime.subprocess.run", run)
    with pytest.warns(RuntimeWarning, match="cleanup could not be verified"):
        with termux_wake_lock("on", env={}):
            pass


def test_inspect_project_retains_full_agp_version(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text('plugins { id("com.android.application") version "9.3.1" }\n')
    info = android_build.inspect_project(tmp_path)
    assert info.agp_version == "9.3.1"
    assert info.agp_major == 9


@pytest.mark.parametrize(("agp", "minimum"), [("8.9.1", "8.11.1"), ("8.13.2", "8.13"), ("9.0.0", "9.1.0"), ("9.2.1", "9.4.1"), ("9.3.0", "9.5.0")])
def test_documented_agp_minimum_gradle_mapping(agp: str, minimum: str) -> None:
    assert android_build.minimum_gradle_for_agp(agp) == minimum


def test_agp_minor_compatibility_is_not_major_only(tmp_path: Path) -> None:
    project = android_build.ProjectInfo(tmp_path, "x", True, True, 36, None, 8, False, None, None, "8.9.1")
    assert not android_build.gradle_meets_agp_minimum("8.9", project)
    assert android_build.gradle_meets_agp_minimum("8.11.1", project)


def test_gradle_version_probe_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))
    monkeypatch.setattr("devtool.android_build.subprocess.run", timeout)
    assert android_build.gradle_version(Path("gradle"), timeout=0.01) is None


def test_explicit_ccache_must_own_launchers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cc = tmp_path / "ccache"; cc.write_text(""); cc.chmod(0o755)
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: False)
    monkeypatch.setattr("devtool.android_build.shutil.which", lambda name: str(cc) if name == "ccache" else None)
    env = {"CMAKE_C_COMPILER_LAUNCHER": "/other/cache"}
    with pytest.raises(android_build.BuildError, match="already configured"):
        android_build.configure_native_cache(env, "ccache")


def test_commented_android_native_markers_do_not_require_ndk(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text('// externalNativeBuild { ndkVersion = "x" }\n/* android.toolchain.cmake */\n')
    requirement = classify_native_requirement(tmp_path, android=True)
    assert requirement.capability == "none"


def test_android_helper_compatibility_always_runtime_probes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = tmp_path / "aidl"; tool.write_bytes(b"x"); tool.chmod(0o755)
    monkeypatch.setattr(android_build, "native_tool_matches_host", lambda *args: True)
    monkeypatch.setattr(android_build, "android_build_tool_executes", lambda *args: False)
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: False)
    assert not android_build.native_android_build_tool_compatible(tool, "aidl", "arm64")


def test_revanced_explicit_offline_tag_uses_digest_verified_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = tmp_path / "aapt2" / "revanced" / "v1"; release.mkdir(parents=True)
    binary = release / "aapt2"; binary.write_bytes(b"good"); binary.chmod(0o755)
    archive = release / "asset.bin"; archive.write_bytes(b"archive")
    import hashlib
    (release / "provenance.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(b"good").hexdigest(),
        "archive_sha256": hashlib.sha256(b"archive").hexdigest(),
        "asset_name": "asset.bin",
    }))
    monkeypatch.setattr(android_build, "normalized_machine", lambda: "arm64")
    monkeypatch.setattr(android_build, "validate_aapt2", lambda path, dry_run=False: path)
    monkeypatch.setattr(android_build, "github_json", lambda *a, **k: pytest.fail("network metadata must not be queried offline"))
    assert android_build.obtain_revanced_aapt2(tmp_path, release_name="v1", offline=True, force=False, dry_run=False) == binary

def test_managed_gradle_rejects_distribution_digest_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    def fake_download(url, destination, *, offline, force=False, headers=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if str(destination).endswith(".sha256"):
            destination.write_text("0" * 64 + "\n")
        else:
            destination.write_bytes(b"not-the-official-distribution")
        return destination
    monkeypatch.setattr(android_build, "download", fake_download)
    with pytest.raises(android_build.BuildError, match="SHA-256 mismatch"):
        android_build.managed_gradle("9.5.0", cache, offline=False, force=True)


def test_managed_gradle_offline_reuse_requires_archive_and_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    home = cache / "gradle" / "gradle-9.5.0"
    launcher = home / "bin" / "gradle"; launcher.parent.mkdir(parents=True); launcher.write_text("#!/bin/sh\n"); launcher.chmod(0o755)
    (home / ".devtool-gradle.json").write_text(json.dumps({"version": "9.5.0", "archive_sha256": "a" * 64}))
    monkeypatch.setattr(android_build, "gradle_version", lambda *args, **kwargs: "9.5.0")
    with pytest.raises(android_build.BuildError, match="failed version/provenance verification"):
        android_build.managed_gradle("9.5.0", cache, offline=True, force=False)


def test_build_tools_repair_stages_every_tool_before_sdk_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = tmp_path / "sdk"; directory = sdk / "build-tools" / "35.0.0"; directory.mkdir(parents=True)
    for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES:
        (directory / name).write_bytes(("old-" + name).encode())
    sources = tmp_path / "sources"; sources.mkdir()
    for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES:
        path = sources / name; path.write_bytes(("new-" + name).encode()); path.chmod(0o755)
    monkeypatch.setattr(android_build, "normalized_machine", lambda: "arm64")
    monkeypatch.setattr(android_build, "arm64_build_tools_replacements_needed", lambda *args: list(android_build.ARM_BUILD_TOOLS_NATIVE_NAMES))
    monkeypatch.setattr(android_build, "sdk_root_is_writable_for_install", lambda *args: True)
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: False)
    monkeypatch.setattr(android_build, "obtain_commit451_arm64_tool", lambda cache_root, version, name, **kwargs: sources / name)
    def compatible(path: Path, name: str, machine=None):
        return not (".devtool-build-tools-stage" in str(path) and name == "zipalign")
    monkeypatch.setattr(android_build, "native_android_build_tool_compatible", compatible)
    before = {name: (directory / name).read_bytes() for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES}
    with pytest.raises(android_build.BuildError, match="Staged ARM64 zipalign"):
        android_build.ensure_arm64_build_tools_compatible(
            sdk, "35.0.0", tmp_path / "cache", provider="commit451", offline=True, force=False, dry_run=False
        )
    assert {name: (directory / name).read_bytes() for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES} == before


def test_proot_rejects_empty_bind_and_managed_cache_override() -> None:
    with pytest.raises(ValueError, match="non-empty source"):
        validate_proot_config(ProotRunnerConfig(distro="debian", binds=[""]))
    with pytest.raises(ValueError, match="devtool-managed"):
        validate_proot_config(ProotRunnerConfig(distro="debian", env=["CARGO_HOME=/tmp/escape"]))


def test_proot_spec_env_cannot_override_managed_run_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setenv("DEVTOOL_RUN_ID", "authoritative-run")
    spec = CommandSpec(
        "test", ["true"], cwd=str(repo),
        env={"DEVTOOL_RUN_ID": "spoofed-run", "CARGO_HOME": "/tmp/escape"},
    )
    wrapped = wrap_specs_for_proot_distro(
        [spec], repo_root=repo, config=ProotRunnerConfig(distro="debian"), dry_run=True
    )[0]
    env_values = [wrapped.argv[index + 1] for index, item in enumerate(wrapped.argv[:-1]) if item == "--env"]
    assert "DEVTOOL_RUN_ID=authoritative-run" in env_values
    assert "DEVTOOL_RUN_ID=spoofed-run" not in env_values
    assert all(value != "CARGO_HOME=/tmp/escape" for value in env_values)
    assert any(value.startswith("CARGO_HOME=/var/cache/devtool/") for value in env_values)


def test_provider_evidence_is_reused_by_native_cache_and_aapt2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.termux_toolchains import ToolProvider

    native = tmp_path / "clang"; native.write_text("x"); native.chmod(0o755)
    ccache = tmp_path / "ccache"; ccache.write_text("x"); ccache.chmod(0o755)
    aapt2 = tmp_path / "aapt2"; aapt2.write_text("x"); aapt2.chmod(0o755)
    evidence = SimpleNamespace(
        native=ToolProvider("native-compiler", "termux-sysroot", True, path=native, compatible=True, reason="snapshot-native"),
        ccache=ToolProvider("ccache", "termux", True, path=ccache, compatible=True, reason="snapshot-ccache"),
        android_tools=(ToolProvider("aapt2", "termux", True, path=aapt2, compatible=True, reason="snapshot-aapt2"),),
    )
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: True)
    monkeypatch.setattr(android_build, "termux_ndk_sysroot_ready", lambda *args, **kwargs: pytest.fail("must use passed evidence"))
    project = android_build.ProjectInfo(tmp_path, "p", False, False, None, None, None, False, None, None)
    # select_native_provider passes the same evidence through its readiness helper.
    monkeypatch.undo()
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: True)
    assert android_build.select_native_provider(project, "termux-sysroot", provider_evidence=evidence) == "termux-sysroot"
    env: dict[str, str] = {}
    assert android_build.configure_native_cache(env, "ccache", provider_evidence=evidence) == "ccache"
    assert env["CMAKE_C_COMPILER_LAUNCHER"] == str(ccache)
    monkeypatch.setattr(android_build, "termux_aapt2_supports_platform", lambda *args, **kwargs: True)
    assert android_build.select_termux_aapt2(tmp_path, "android-36", required=True, provider_evidence=evidence) == aapt2


def test_managed_gradle_rejects_staged_identity_before_replacing_existing_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    home = cache / "gradle" / "gradle-9.5.0"
    old_launcher = home / "bin" / "gradle"
    old_launcher.parent.mkdir(parents=True)
    old_launcher.write_text("old-launcher")
    archive_bytes = b"verified-archive"
    import hashlib
    expected = hashlib.sha256(archive_bytes).hexdigest()

    def fake_download(url, destination, *, offline, force=False, headers=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if str(destination).endswith(".sha256"):
            destination.write_text(expected + "\n")
        else:
            destination.write_bytes(archive_bytes)
        return destination

    def fake_extract(_archive: Path, destination: Path) -> None:
        staged = destination / "gradle-9.5.0" / "bin" / "gradle"
        staged.parent.mkdir(parents=True)
        staged.write_text("wrong-version")
        staged.chmod(0o755)

    monkeypatch.setattr(android_build, "download", fake_download)
    monkeypatch.setattr(android_build, "extract_zip", fake_extract)
    monkeypatch.setattr(android_build, "gradle_version", lambda path, **kwargs: "1.0" if "gradle-build-gradle-" in str(path) else "9.5.0")
    with pytest.raises(android_build.BuildError, match="before install"):
        android_build.managed_gradle("9.5.0", cache, offline=False, force=True)
    assert old_launcher.read_text() == "old-launcher"


def test_proot_canonicalizes_host_path_before_repo_mapping(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "nested").mkdir()
    outside = tmp_path / "outside.txt"; outside.write_text("outside")
    escaped_spelling = str(repo / "nested" / ".." / ".." / "outside.txt")
    spec = CommandSpec("test", ["cat", escaped_spelling], cwd=str(repo))
    wrapped = wrap_specs_for_proot_distro(
        [spec], repo_root=repo, config=ProotRunnerConfig(distro="debian"), dry_run=True
    )[0]
    assert "/workspace/project/.." not in " ".join(wrapped.argv)
    assert any(token.startswith("/run/devtool/") and token.endswith("-outside.txt") for token in wrapped.argv)


def test_proot_managed_stage_and_cache_cannot_alias_repository_bind(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    spec = CommandSpec("test", ["true"], cwd=str(repo))
    with pytest.raises(ValueError, match="stage directory overlaps"):
        wrap_specs_for_proot_distro(
            [spec], repo_root=repo,
            config=ProotRunnerConfig(distro="debian", stage_dir=str(repo / ".stage")), dry_run=True,
        )
    with pytest.raises(ValueError, match="cache directory overlaps"):
        wrap_specs_for_proot_distro(
            [spec], repo_root=repo,
            config=ProotRunnerConfig(distro="debian", cache_dir=str(repo / ".cache")), dry_run=True,
        )


def test_proot_reserves_entire_devtool_environment_namespace() -> None:
    with pytest.raises(ValueError, match="devtool-managed"):
        validate_proot_config(ProotRunnerConfig(distro="debian", env=["DEVTOOL_FUTURE_ID=spoofed"]))


def test_failed_proot_execution_can_retain_owned_stage(tmp_path: Path) -> None:
    from devtool.proot_distro_runner import cleanup_proot_staging
    stage = tmp_path / "stage"; stage.mkdir()
    spec = CommandSpec("test", ["false"], metadata={"proot_stage_owned": True, "proot_stage_dir": str(stage)})
    warnings = cleanup_proot_staging([spec], retain=True)
    assert stage.is_dir()
    assert any("did not complete successfully" in warning for warning in warnings)


def test_real_android_inspector_ignores_commented_native_configuration(tmp_path: Path) -> None:
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "main" / "AndroidManifest.xml").write_text("<manifest />")
    (tmp_path / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") }\n'
        '// externalNativeBuild { cmake { version = "9.9" } }\n'
        '/* ndkVersion = "99.0.0" */\n'
    )
    (tmp_path / "gradle.properties").write_text('# ndkVersion=88.0.0\n! externalNativeBuild=true\n')
    project = android_build.inspect_project(tmp_path)
    assert project.android
    assert not project.native_build
    assert project.ndk_version is None
    assert project.cmake_version is None


def test_arm_helper_swap_failure_restores_current_tool_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = tmp_path / "sdk"; directory = sdk / "build-tools" / "35.0.0"; directory.mkdir(parents=True)
    sources = tmp_path / "sources"; sources.mkdir()
    before = {}
    for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES:
        target = directory / name; target.write_bytes(("old-" + name).encode()); target.chmod(0o755)
        source = sources / name; source.write_bytes(("new-" + name).encode()); source.chmod(0o755)
        before[name] = target.read_bytes()
    monkeypatch.setattr(android_build, "normalized_machine", lambda: "arm64")
    monkeypatch.setattr(android_build, "arm64_build_tools_replacements_needed", lambda *args: list(android_build.ARM_BUILD_TOOLS_NATIVE_NAMES))
    monkeypatch.setattr(android_build, "sdk_root_is_writable_for_install", lambda *args: True)
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: False)
    monkeypatch.setattr(android_build, "obtain_commit451_arm64_tool", lambda cache_root, version, name, **kwargs: sources / name)
    monkeypatch.setattr(android_build, "native_android_build_tool_compatible", lambda *args, **kwargs: True)
    original_replace = Path.replace
    def fail_stage_replace(self: Path, target: Path):
        if ".devtool-build-tools-stage-" in str(self) and self.name == "aidl":
            raise OSError("induced stage replace failure")
        return original_replace(self, target)
    monkeypatch.setattr(Path, "replace", fail_stage_replace)
    with pytest.raises(OSError, match="induced"):
        android_build.ensure_arm64_build_tools_compatible(
            sdk, "35.0.0", tmp_path / "cache", provider="commit451",
            offline=True, force=False, dry_run=False,
        )
    assert {name: (directory / name).read_bytes() for name in android_build.ARM_BUILD_TOOLS_NATIVE_NAMES} == before


def test_native_provider_uses_shared_requirement_evidence(tmp_path: Path) -> None:
    from devtool.termux_toolchains import NativeRequirement
    project = android_build.ProjectInfo(tmp_path, "p", True, True, 36, "36.0.0", 9, False, None, None, "9.4.0")
    evidence = SimpleNamespace(native_requirement=NativeRequirement("android-ndk", "snapshot requires full NDK"))
    assert android_build.select_native_provider(project, "auto", provider_evidence=evidence) == "ndk"


def test_provider_explain_respects_explicit_gradle_and_native_choices(tmp_path: Path) -> None:
    from devtool.models import ProjectConfig
    from devtool.termux_diagnostics import provider_explanation_rows
    from devtool.termux_toolchains import NativeRequirement, RuntimeIdentity, ToolProvider
    config = ProjectConfig(
        repo_root=tmp_path, kind="android",
        runner_options={"gradle": {"provider": "termux"}, "android": {"native": {"provider": "system"}}},
    )
    evidence = SimpleNamespace(
        runtime=RuntimeIdentity("android", "termux", "arm64", "bionic"),
        wrapper=SimpleNamespace(complete=True, present=True, missing=()),
        gradle=ToolProvider("gradle", "termux", False, compatible=False, reason="missing"),
        java=(),
        native=ToolProvider("native-compiler", "termux-sysroot", True, compatible=True, reason="ready"),
        native_requirement=NativeRequirement("compiler", "compiler inputs"),
    )
    rows = dict(provider_explanation_rows(config, _evidence=evidence))
    assert "Termux Gradle explicitly requested" in rows["provider_explain_gradle"]
    assert "system native provider explicitly requested" in rows["provider_explain_native"]


def test_packages_respect_explicit_termux_gradle_even_with_complete_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.models import ProjectConfig
    from devtool.termux_diagnostics import termux_package_recommendations
    from devtool.termux_toolchains import NativeRequirement, RuntimeIdentity, ToolProvider
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={"gradle": {"provider": "termux"}})
    evidence = SimpleNamespace(
        runtime=RuntimeIdentity("android", "termux", "arm64", "bionic"),
        wrapper=SimpleNamespace(complete=True, present=True, missing=()),
        gradle=ToolProvider("gradle", "termux", False, compatible=False, reason="missing"),
        java=(), native=ToolProvider("native-compiler", "termux-sysroot", False, compatible=False),
        native_requirement=NativeRequirement("none", "none"),
        cmake=ToolProvider("cmake", "termux", False, compatible=False),
        ninja=ToolProvider("ninja", "termux", False, compatible=False),
        ccache=ToolProvider("ccache", "termux", False, compatible=False),
        android_tools=(),
    )
    monkeypatch.setattr("devtool.termux_diagnostics.proot_distro_available", lambda: "proot-distro")
    monkeypatch.setattr("devtool.termux_diagnostics.tmux_available", lambda: True)
    packages = termux_package_recommendations(config, _evidence=evidence)
    assert any(item.package == "gradle" and item.required for item in packages)


def test_cli_retains_proot_stage_when_phase_result_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import nullcontext
    from devtool.cli import _run_specs_for_args
    from devtool.models import OperationResult, ProjectConfig
    stage = tmp_path / "owned-stage"; stage.mkdir()
    prepared = CommandSpec(
        "test", ["false"], cwd=str(tmp_path),
        metadata={"execution_mode": "proot-distro", "proot_stage_owned": True, "proot_stage_dir": str(stage)},
    )
    config = ProjectConfig(repo_root=tmp_path, kind="custom")
    args = SimpleNamespace(
        verbose=False, dry_run=False, termux_wake_lock=False, no_termux_wake_lock=False,
    )
    failed = OperationResult("test", ["false"], 1, 0.01, "failed", [])
    monkeypatch.setattr("devtool.cli._prepare_specs_for_execution", lambda *args, **kwargs: [prepared])
    monkeypatch.setattr("devtool.cli.run_specs", lambda *args, **kwargs: ([failed], "failed"))
    monkeypatch.setattr("devtool.termux_runtime.termux_wake_lock", lambda mode: nullcontext())
    results, _ = _run_specs_for_args(config, [prepared], args, continue_on_error=True)
    assert results[0].returncode == 1
    assert stage.is_dir()
