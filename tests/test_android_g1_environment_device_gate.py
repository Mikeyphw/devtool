from __future__ import annotations

import os
from pathlib import Path

import pytest

from devtool.android.cli import android_environment_payload
from devtool.android.environment import ANDROID_HELPERS, resolve_android_environment
from devtool.doctor import collect_doctor_checks
from devtool.models import ProjectConfig
from devtool.termux_toolchains import RuntimeIdentity
from devtool.project_runners import AndroidGradleRunner


def _exe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)




def _runtime(*, environment: str, prefix: Path | None = None) -> RuntimeIdentity:
    return RuntimeIdentity(
        platform="android" if environment == "termux" else "linux",
        environment=environment,
        arch="arm64" if environment == "termux" else "x86_64",
        libc="bionic" if environment == "termux" else "glibc",
        prefix=prefix,
        android_api=36 if environment == "termux" else None,
    )


def _termux_prefix(root: Path) -> Path:
    prefix = root / "termux-prefix"
    for name in ANDROID_HELPERS:
        _exe(prefix / "bin" / name)
    _exe(prefix / "bin" / "apksigner")
    return prefix


def _project(root: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name='gate'\n", encoding="utf-8")
    (root / "build.gradle").write_text("if (false) { compileSdk 36; buildToolsVersion '36.0.0' }\n", encoding="utf-8")
    manifest = root / "src/main/AndroidManifest.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('<manifest package="gate"/>\n', encoding="utf-8")


def _sdk(root: Path, *, missing: str = "") -> Path:
    sdk = root / "sdk"
    platform = sdk / "platforms/android-36"
    platform.mkdir(parents=True)
    (platform / "android.jar").write_bytes(b"jar")
    bt = sdk / "build-tools/36.0.0"
    (bt / "lib").mkdir(parents=True)
    (bt / "lib/d8.jar").write_bytes(b"d8")
    (bt / "source.properties").write_text("Pkg.Revision=36.0.0\n", encoding="utf-8")
    for name in ANDROID_HELPERS:
        if name != missing:
            _exe(bt / name)
    _exe(bt / "apksigner")
    return sdk


def _config(root: Path, sdk: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root, project_root=str(root), kind="android", runner="android",
        selected_target="app", variant="release",
        runner_options={"android": {"sdk_root": str(sdk), "build_tools": {"version": "36.0.0"}, "adb": {"provider": "none"}, "release": {"variant": "release", "module": "app"}}},
    )


def _metadata(plan: str, *, jdk: str = "jdk-21") -> dict:
    return {"gradle_execution_receipt": {"context_digest": "ctx", "plan_digest": plan, "jdk_identity": jdk, "launcher_identity": "gradle-9.7"}}


def test_environment_identity_is_action_and_workload_plan_invariant_but_gradle_domain_stays_linked(tmp_path: Path) -> None:
    _project(tmp_path); sdk = _sdk(tmp_path); cfg = _config(tmp_path, sdk)
    env = {"PATH": "", "ANDROID_SDK_ROOT": str(sdk)}
    _, qa, build = resolve_android_environment(cfg, action="build", env=env, gradle_metadata=_metadata("plan-a"))
    _, qb, release = resolve_android_environment(cfg, action="release", env=env, gradle_metadata=_metadata("plan-b"))
    assert qa.ok and qb.ok and build is not None and release is not None
    assert build.environment_id == release.environment_id
    assert build.component_identities["android.gradle"] == release.component_identities["android.gradle"]
    _, _, changed_jdk = resolve_android_environment(cfg, action="release", env=env, gradle_metadata=_metadata("plan-b", jdk="jdk-17"))
    assert changed_jdk is not None and changed_jdk.environment_id == release.environment_id
    assert changed_jdk.component_identities["android.gradle"] != release.component_identities["android.gradle"]


def test_builder_doctor_explain_release_project_same_canonical_environment_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path); sdk = _sdk(tmp_path); cfg = _config(tmp_path, sdk)
    prefix = _termux_prefix(tmp_path)
    runtime = _runtime(environment="termux", prefix=prefix)
    monkeypatch.setattr("devtool.android.environment.detect_runtime_identity", lambda _env=None: runtime)
    monkeypatch.setenv("PREFIX", str(prefix))
    monkeypatch.setenv("TERMUX_VERSION", "test")
    monkeypatch.setenv("ANDROID_SDK_ROOT", str(sdk))
    monkeypatch.setenv("PATH", "")
    hermetic_env = {"PREFIX": str(prefix), "TERMUX_VERSION": "test", "ANDROID_SDK_ROOT": str(sdk), "PATH": ""}
    specs = AndroidGradleRunner().build_task_specs(
        cfg, [":app:assembleRelease"], action="build", env_override=hermetic_env,
    )
    builder_id = next(str(s.metadata.get("android_environment_identity") or "") for s in specs if s.metadata.get("android_environment_identity"))
    assert builder_id
    explain = android_environment_payload(cfg, action="explain")
    release = android_environment_payload(cfg, action="release")
    assert explain["ok"] and release["ok"]
    assert explain["environment_id"] == release["environment_id"] == builder_id
    checks = collect_doctor_checks(cfg)
    doctor = next(item for item in checks if item.section == "android" and item.name == "environment")
    assert doctor.status == "ok" and doctor.detail.startswith(builder_id)


def test_incomplete_build_tools_cannot_qualify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path); sdk = _sdk(tmp_path, missing="aidl"); cfg = _config(tmp_path, sdk)
    runtime = _runtime(environment="host")
    monkeypatch.setattr("devtool.android.environment.detect_runtime_identity", lambda _env=None: runtime)
    request, qualification, resolved = resolve_android_environment(cfg, action="build", env={"PATH": "", "ANDROID_SDK_ROOT": str(sdk)}, fail_closed=False)
    assert not qualification.ok and resolved is None
    assert "aidl" in qualification.capabilities and not qualification.capabilities["aidl"].ready


def test_environment_explain_is_device_side_effect_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path); sdk = _sdk(tmp_path); cfg = _config(tmp_path, sdk)
    monkeypatch.setattr("devtool.android.execution_snapshot.resolve_adb", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ADB must not be touched")))
    payload = android_environment_payload(cfg, action="explain")
    assert payload["ok"] and payload["request"]["device_required"] is False


def test_gate_fixture_remediation_keeps_instrumentation_build_and_device_specs_separate(tmp_path: Path) -> None:
    _project(tmp_path); sdk = _sdk(tmp_path); cfg = _config(tmp_path, sdk)
    cfg.runner_options["android"]["instrumentation"] = {"policy": "required"}
    specs = AndroidGradleRunner().build_specs(cfg, "instrumentation")
    assert any(item.metadata.get("instrumentation_build_owned_by_gradle_planner") for item in specs)
    assert any("devtool.android_instrumentation" in item.argv for item in specs)
