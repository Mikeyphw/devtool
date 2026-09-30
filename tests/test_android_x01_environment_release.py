from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest import mock

import pytest

from devtool.android.environment import ANDROID_HELPERS, resolve_android_environment
from devtool.android.evidence import AndroidReleaseEvidenceSession
from devtool.android.instrumentation import request_from_config
from devtool.android.release import AndroidReleaseOrchestrator
from devtool.android.variants import resolve_android_variant_lane
from devtool.cli import build_parser
from devtool.models import CommandSpec, OperationResult, ProjectConfig


def _project(root: Path, *, ndk: str = "") -> None:
    (root / "settings.gradle").write_text("rootProject.name='x'\n", encoding="utf-8")
    text = "plugins { id 'com.android.application' version '8.9.0' apply false }\nandroid { compileSdk 36\n buildToolsVersion '36.0.0'\n"
    if ndk:
        text += f" ndkVersion '{ndk}'\n"
    (root / "build.gradle").write_text(text + "}\n", encoding="utf-8")


def _sdk(root: Path, *, complete_build_tools: bool = True, helpers: tuple[str, ...] = ANDROID_HELPERS) -> Path:
    sdk = root / "sdk"
    platform = sdk / "platforms" / "android-36"
    platform.mkdir(parents=True)
    (platform / "android.jar").write_bytes(b"jar")
    bt = sdk / "build-tools" / "36.0.0"
    (bt / "lib").mkdir(parents=True)
    (bt / "source.properties").write_text("Pkg.Revision=36.0.0\n", encoding="utf-8")
    if complete_build_tools:
        (bt / "lib" / "d8.jar").write_bytes(b"d8")
    for name in helpers:
        path = bt / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return sdk


def _config(root: Path, sdk: Path | None = None, *, android: dict | None = None) -> ProjectConfig:
    options = dict(android or {})
    if sdk is not None:
        options.setdefault("sdk_root", str(sdk))
        options.setdefault("build_tools", {"version": "36.0.0"})
    return ProjectConfig(
        repo_root=root, project_root=str(root), kind="android", runner="android",
        selected_target="app", variant="debug", runner_options={"android": options},
    )


def test_explicit_environment_view_does_not_leak_ambient_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path)
    ambient = _sdk(tmp_path / "ambient")
    monkeypatch.setenv("ANDROID_SDK_ROOT", str(ambient))
    config = _config(tmp_path, None, android={"build_tools": {"version": "36.0.0"}})
    request, qualification, resolved = resolve_android_environment(config, env={"PATH": os.environ.get("PATH", "")}, fail_closed=False)
    assert request.environment_view_id
    assert qualification.status == "blocked"
    assert resolved is None
    assert str(ambient) not in qualification.sdk_root


def test_incomplete_build_tools_directory_never_qualifies(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path, complete_build_tools=False)
    _request, qualification, resolved = resolve_android_environment(_config(tmp_path, sdk), env={"PATH": os.environ.get("PATH", "")}, fail_closed=False)
    assert qualification.status == "blocked" and resolved is None
    assert not qualification.capabilities["build-tools"].ready


def test_all_canonical_helpers_have_provider_and_runtime_evidence(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    _request, qualification, resolved = resolve_android_environment(_config(tmp_path, sdk), env={"PATH": os.environ.get("PATH", "")})
    assert resolved is not None
    assert set(resolved.helpers) == set(ANDROID_HELPERS)
    for name in ANDROID_HELPERS:
        cap = qualification.capabilities[name]
        assert cap.ready and cap.provider and cap.path and cap.metadata.get("runtime")


def test_explicit_missing_ndk_cannot_fall_back_to_other_revision(tmp_path: Path) -> None:
    _project(tmp_path, ndk="26.1.0")
    sdk = _sdk(tmp_path)
    installed = sdk / "ndk" / "25.2.0"
    (installed / "build" / "cmake").mkdir(parents=True)
    (installed / "build" / "cmake" / "android.toolchain.cmake").write_text("", encoding="utf-8")
    (installed / "source.properties").write_text("Pkg.Revision=25.2.0\n", encoding="utf-8")
    bindir = installed / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "clang++").write_bytes(b"")
    (bindir / "clang").write_bytes(b"")
    config = _config(tmp_path, sdk)
    config.runner_options["android"]["ndk_version"] = "26.1.0"
    _request, qualification, resolved = resolve_android_environment(config, env={"PATH": os.environ.get("PATH", "")}, fail_closed=False)
    assert resolved is None
    assert qualification.capabilities["ndk"].requested == "26.1.0"
    assert qualification.capabilities["ndk"].selected == ""


def test_release_and_instrumentation_lanes_are_independent_and_shared(tmp_path: Path) -> None:
    config = _config(tmp_path, None, android={
        "release": {"variant": "release", "module": "app"},
        "instrumentation": {"variant": "demoDebug", "module": "mobile", "policy": "disabled"},
        "variants": {"demoDebug": {"build_type": "debug", "module": "mobile"}},
    })
    release = resolve_android_variant_lane(config, "release")
    instrumentation = resolve_android_variant_lane(config, "instrumentation")
    request = request_from_config(config)
    assert (release.name, release.module) == ("release", "app")
    assert (instrumentation.name, instrumentation.module) == ("demoDebug", "mobile")
    assert request.variant == instrumentation.name


def test_release_plan_rejects_conflicting_variant_bound_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, None, android={"release": {"variant": "release", "device_tests": "disabled"}})
    def build_specs(self, _config, action):
        task = ":app:assembleDebug" if action == "package" else f":app:{action}Release"
        return [CommandSpec(action, ["gradle", task], metadata={"gradle_tasks": [task]})]
    monkeypatch.setattr("devtool.project_runners.AndroidGradleRunner.build_specs", build_specs)
    with pytest.raises(ValueError, match="conflicts with variant-bound task"):
        AndroidReleaseOrchestrator(config).plan(overrides={"unit_tests": False, "lint": False, "device_tests": "disabled"})


def test_release_artifact_materialization_and_hash_drift_fail_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "app-release.apk"
    artifact.write_bytes(b"actual")
    config = _config(tmp_path)
    config.variant = "release"
    bad = {"items": [{"valid": True, "type": "apk", "variant": "release", "path": str(artifact), "sha256": "0" * 64}]}
    assert AndroidReleaseOrchestrator._release_artifacts(config, bad) == []
    good_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    good = {"items": [{"valid": True, "type": "apk", "variant": "release", "path": str(artifact), "sha256": good_sha}]}
    selected = AndroidReleaseOrchestrator._release_artifacts(config, good)
    assert selected and selected[0][1]["current_sha256"] == good_sha and selected[0][1]["materialized"] is True


def test_release_evidence_defaults_to_private_run_and_collision_fails(tmp_path: Path) -> None:
    session = AndroidReleaseEvidenceSession(tmp_path, "app", "release", run_id="run-x", session_id="session-x")
    session.add_gate("package", "passed")
    path = session.seal()
    assert ".devtool/private/run-v3/run-x/android-release" in str(path)
    with pytest.raises(FileExistsError):
        session.seal()


def test_failed_device_readiness_blocks_smoke_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, None, android={"release": {
        "variant": "release", "device_tests": "required", "unit_tests": False, "lint": False,
        "install_smoke": True, "upgrade_smoke": False, "verify_signature": False, "verify_certificate_identity": False,
    }})
    artifact = tmp_path / "app-release.apk"; artifact.write_bytes(b"apk")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    monkeypatch.setattr("devtool.project_runners.AndroidGradleRunner.build_specs", lambda self, config, action: [CommandSpec(action, ["fake", action])])
    fake_context = mock.Mock(); fake_context.as_dict.return_value = {"tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **k: fake_context)
    ops = mock.Mock(); ops.select_device.side_effect = RuntimeError("no qualified device")
    monkeypatch.setattr("devtool.android.release.AndroidDeviceOperations.from_config", lambda *a, **k: ops)
    def executor(_config, specs):
        result = OperationResult(specs[0].name, specs[0].argv, 0, 0.0, "ok", [], metadata={})
        if specs[0].name == "package":
            result.metadata["artifacts"] = {"items": [{"valid": True, "type": "apk", "variant": "release", "path": str(artifact), "sha256": digest}]}
        return [result], ""
    outcome = AndroidReleaseOrchestrator(config, executor=executor).run()
    assert not outcome.passed
    assert any(g["name"] == "device-readiness" and g["status"] == "failed" for g in outcome.evidence["gates"])
    ops.install_smoke.assert_not_called()



def test_build_environment_never_probes_adb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    monkeypatch.setattr("devtool.android.capabilities.resolve_adb", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ADB must not be probed")))
    _request, qualification, resolved = resolve_android_environment(
        _config(tmp_path, sdk), action="release", env={"PATH": os.environ.get("PATH", "")}, device_required=False
    )
    assert qualification.ok and resolved is not None


def test_release_evidence_explicit_output_is_projection_of_private_run(tmp_path: Path) -> None:
    session = AndroidReleaseEvidenceSession(tmp_path, "app", "release", run_id="run-export", session_id="session-export")
    session.add_gate("package", "passed")
    exported = tmp_path / "release-export.json"
    returned = session.seal(exported, register=False)
    canonical = tmp_path / ".devtool" / "private" / "run-v3" / "run-export" / "android-release" / "session-export.final.json"
    assert returned == exported
    assert canonical.is_file() and exported.is_file()
    assert canonical.read_bytes() == exported.read_bytes()


def test_generated_config_and_cli_expose_lane_semantics() -> None:
    from devtool.config import minimal_global_config_template
    text = minimal_global_config_template()
    assert '# variant = "debug" # independent instrumentation lane' in text
    assert 'variant = "release" # independent release lane' in text
    parser = build_parser()
    nested = parser.parse_args(["--target", "app", "android", "instrumentation", "--variant", "demoDebug", "--module", "mobile", "--skip-build"])
    assert nested.instrumentation_variant == "demoDebug" and nested.instrumentation_module == "mobile"
    release = parser.parse_args(["--target", "app", "android", "release", "--variant", "playRelease", "--module", "app"])
    assert release.release_variant == "playRelease" and release.release_module == "app"
