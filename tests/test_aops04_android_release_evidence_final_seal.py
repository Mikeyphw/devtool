from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from devtool.android.evidence import AndroidReleaseEvidenceSession
from devtool.android.release import AndroidReleaseOrchestrator, audit_android_release_convergence, release_options
from devtool.cli import build_parser
from devtool.models import CommandSpec, OperationResult, ProjectConfig


def _config(root: Path, *, release: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root, kind="android", runner="android", project_root=str(root), selected_target="app", variant="debug",
        runner_options={"android": {"release": dict(release or {}), "signing": {"release": {"mode": "project", "policy": "optional"}}}},
    )


def test_release_options_default_to_release_grade_gates(tmp_path: Path) -> None:
    options = release_options(_config(tmp_path))
    assert options.variant == "release"
    assert options.unit_tests and options.lint and options.build_artifacts
    assert options.device_tests == "required"
    assert options.install_smoke and options.upgrade_smoke
    assert options.verify_signature and options.verify_certificate_identity


def test_release_options_accept_explicit_development_policy(tmp_path: Path) -> None:
    options = release_options(_config(tmp_path, release={"device_tests": "when-device-present", "upgrade_smoke": False}))
    assert options.device_tests == "when-device-present"
    assert options.upgrade_smoke is False


def test_release_cli_is_first_class_and_android_alias_exists() -> None:
    parser = build_parser()
    top = parser.parse_args(["--target", "app", "release", "--device-tests", "required", "--no-upgrade-smoke"])
    assert top.command == "release" and top.device_tests == "required" and top.no_upgrade_smoke
    nested = parser.parse_args(["--target", "app", "android", "release", "--device-tests", "disabled"])
    assert nested.android_command == "release" and nested.device_tests == "disabled"


def test_evidence_session_is_hashed_immutable_and_registry_integrated(tmp_path: Path) -> None:
    session = AndroidReleaseEvidenceSession(tmp_path, "app", "release", run_id="run-1", session_id="session-1")
    session.execution = {"tools": {"adb": {"provider": "termux-adb"}}}
    session.signing = {"expected_cert_sha256": "aa" * 32}
    session.add_gate("package", "passed", metadata={"tests": 1})
    session.add_artifacts({"items": [{"path": "app-release.apk", "sha256": "bb" * 32, "valid": True}]})
    path = session.seal()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "passed"
    assert len(payload["evidence_sha256"]) == 64
    original = path.read_bytes()
    session.add_gate("late", "failed")
    with pytest.raises(FileExistsError):
        session.seal()
    assert path.read_bytes() == original
    registry = json.loads((tmp_path / ".devtool/artifacts/registry.json").read_text(encoding="utf-8"))
    assert any(item.get("metadata", {}).get("release_session_id") == "session-1" for item in registry["items"])


def test_orchestrator_plans_existing_android_runner_services_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={"device_tests": "disabled", "install_smoke": False, "upgrade_smoke": False})
    calls: list[str] = []
    def build_specs(self, config, action):
        calls.append(action)
        return [CommandSpec(action, ["fake", action], cwd=str(tmp_path), metadata={"runner": "android"})]
    monkeypatch.setattr("devtool.project_runners.AndroidGradleRunner.build_specs", build_specs)
    _, options, phases = AndroidReleaseOrchestrator(config).plan()
    assert options.variant == "release"
    assert [name for name, _ in phases] == ["unit-tests", "lint", "package"]
    assert calls == ["test", "lint", "package"]
    assert all(spec.metadata.get("android_release_pipeline") for _, specs in phases for spec in specs)


def test_orchestrator_seals_evidence_from_shared_runner_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={
        "device_tests": "disabled", "install_smoke": False, "upgrade_smoke": False,
        "verify_signature": False, "verify_certificate_identity": False,
    })
    def build_specs(self, config, action):
        return [CommandSpec(action, ["fake", action], cwd=str(tmp_path), metadata={"runner": "android"})]
    monkeypatch.setattr("devtool.project_runners.AndroidGradleRunner.build_specs", build_specs)
    fake_context = mock.Mock()
    fake_context.as_dict.return_value = {"runtime": {"environment": "test"}, "tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **k: fake_context)
    def executor(config, specs):
        result = OperationResult(specs[0].name, specs[0].argv, 0, 0.01, "ok", [], metadata={})
        if specs[0].name == "package":
            artifact = tmp_path / "build" / "app-release.aab"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"release")
            result.metadata["artifacts"] = {"items": [{"path": str(artifact), "type": "aab", "valid": True, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
        return [result], "ok"
    outcome = AndroidReleaseOrchestrator(config, executor=executor).run()
    assert outcome.passed
    assert Path(outcome.evidence_path).is_file()
    assert [item["name"] for item in outcome.evidence["gates"]][:3] == ["unit-tests", "lint", "package"]
    assert outcome.evidence["artifacts"][0]["type"] == "aab"


def test_failed_shared_runner_gate_fails_release_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={"device_tests": "disabled", "install_smoke": False, "upgrade_smoke": False, "verify_signature": False})
    monkeypatch.setattr("devtool.project_runners.AndroidGradleRunner.build_specs", lambda self, config, action: [CommandSpec(action, ["fake"], cwd=str(tmp_path))])
    fake_context = mock.Mock(); fake_context.as_dict.return_value = {"tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **k: fake_context)
    def executor(config, specs):
        code = 1 if specs[0].name == "lint" else 0
        return [OperationResult(specs[0].name, ["fake"], code, 0.01, "boom" if code else "ok", [])], ""
    outcome = AndroidReleaseOrchestrator(config, executor=executor).run()
    assert not outcome.passed
    assert outcome.evidence["verdict"] == "failed"
    assert any(item["name"] == "lint" and item["status"] == "failed" for item in outcome.evidence["gates"])


def test_release_qualification_is_a_consumer_not_an_android_implementation() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "devtool/release_qualification.py").read_text(encoding="utf-8")
    assert "AndroidReleaseOrchestrator" in source
    assert 'shutil.which("adb")' not in source
    assert 'connectedDebugAndroidTest' not in source
    # Android commands are configuration fed to the orchestrator, not _run() calls.
    assert '_run("android-release"' not in source
    assert '_run("android-instrumentation"' not in source


def test_aops04_convergence_audit_passes_repository() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = audit_android_release_convergence(root)
    assert payload["status"] == "pass", payload
    assert not payload["failed"]


def test_release_evidence_never_contains_configured_secret_values(tmp_path: Path) -> None:
    session = AndroidReleaseEvidenceSession(tmp_path, "app", "release", session_id="secret-test")
    session.signing = {"mode": "devtool", "store_password_env": "APP_STORE", "key_password_env": "APP_KEY"}
    session.add_gate("package", "passed")
    payload = json.dumps(session.to_dict())
    assert "actual-store-password" not in payload
    assert "actual-key-password" not in payload


def test_config_template_documents_android_release_policy() -> None:
    from devtool.config import minimal_global_config_template
    text = minimal_global_config_template()
    assert "[android.release]" in text
    assert 'device_tests = "required"' in text
    assert "verify_certificate_identity = true" in text


def test_completion_exposes_release_surfaces() -> None:
    from devtool.completion import ZSH_COMPLETION
    assert "'release:run the canonical Android release pipeline'" in ZSH_COMPLETION
    assert "release|rel)" in ZSH_COMPLETION
    android_actions = next(line for line in ZSH_COMPLETION.splitlines() if "_values 'android action'" in line)
    for action in ("capabilities", "devices", "doctor", "environment", "signing", "smoke", "release"):
        assert action in android_actions


def test_aops04_report_and_final_seal_contract_are_present() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "AOPS04_ANDROID_RELEASE_EVIDENCE_FINAL_SEAL_REPORT.md").is_file()
    seal = (root / "devtool/final_seal.py").read_text(encoding="utf-8")
    assert "android.operations-roadmap" in seal
    assert "AOPS04_ANDROID_RELEASE_EVIDENCE_FINAL_SEAL_REPORT.md" in seal


def _package_executor(tmp_path: Path, items: list[dict]):
    normalized: list[dict] = []
    for raw in items:
        item = dict(raw)
        path = Path(str(item.get("path") or ""))
        if path.is_file():
            item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        normalized.append(item)
    def executor(config, specs):
        result = OperationResult(specs[0].name, specs[0].argv, 0, 0.01, "ok", [], metadata={})
        if specs[0].name == "package":
            result.metadata["artifacts"] = {"items": normalized}
        return [result], "ok"
    return executor


def _patch_release_plumbing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "devtool.project_runners.AndroidGradleRunner.build_specs",
        lambda self, config, action: [CommandSpec(action, ["fake", action], cwd=str(tmp_path), metadata={"runner": "android"})],
    )
    fake_context = mock.Mock()
    fake_context.as_dict.return_value = {"runtime": {"environment": "test"}, "tools": {}}
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *a, **k: fake_context)


def test_aab_only_release_signature_is_verified_without_requiring_apk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.signing import SigningIdentity, SigningVerification

    config = _config(tmp_path, release={
        "device_tests": "disabled",
        "install_smoke": False,
        "upgrade_smoke": False,
        "verify_signature": True,
        "verify_certificate_identity": True,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    aab = tmp_path / "build" / "app-release.aab"
    aab.parent.mkdir(parents=True)
    aab.write_bytes(b"bundle")
    identity = SigningIdentity(source=str(aab), sha256="a" * 64, subject="CN=Release")

    class FakeSigningManager:
        def __init__(self, *args, **kwargs):
            pass
        def verify_artifact(self, artifact, *, expected_sha256=""):
            assert Path(artifact) == aab
            return SigningVerification("passed", str(artifact), True, identity=identity, expected_sha256=expected_sha256, detail="verified", tool="jarsigner")

    monkeypatch.setattr("devtool.android.release.AndroidSigningManager", FakeSigningManager)
    outcome = AndroidReleaseOrchestrator(
        config,
        executor=_package_executor(tmp_path, [{"path": str(aab), "type": "aab", "valid": True, "sha256": "b" * 64}]),
    ).run()
    assert outcome.passed
    gate = next(item for item in outcome.evidence["gates"] if item["name"] == "signature-verification")
    assert gate["status"] == "passed"
    assert outcome.evidence["signing"]["certificate_sha256"] == "a" * 64
    assert not any(item["name"] == "device-smoke" for item in outcome.evidence["gates"])


def test_mixed_release_artifact_signers_fail_identity_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.signing import SigningIdentity, SigningVerification

    config = _config(tmp_path, release={
        "device_tests": "disabled",
        "install_smoke": False,
        "upgrade_smoke": False,
        "verify_signature": True,
        "verify_certificate_identity": True,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    apk = tmp_path / "build" / "app-release.apk"
    aab = tmp_path / "build" / "app-release.aab"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"apk")
    aab.write_bytes(b"aab")

    class FakeSigningManager:
        def __init__(self, *args, **kwargs):
            pass
        def verify_artifact(self, artifact, *, expected_sha256=""):
            path = Path(artifact)
            sha = "a" * 64 if path.suffix == ".apk" else "b" * 64
            identity = SigningIdentity(source=str(path), sha256=sha, subject="CN=Release")
            return SigningVerification("passed", str(path), True, identity=identity, expected_sha256=expected_sha256, detail="verified", tool="fake")

    monkeypatch.setattr("devtool.android.release.AndroidSigningManager", FakeSigningManager)
    outcome = AndroidReleaseOrchestrator(
        config,
        executor=_package_executor(tmp_path, [
            {"path": str(apk), "type": "apk", "valid": True, "sha256": "1" * 64},
            {"path": str(aab), "type": "aab", "valid": True, "sha256": "2" * 64},
        ]),
    ).run()
    assert not outcome.passed
    gate = next(item for item in outcome.evidence["gates"] if item["name"] == "signature-verification")
    assert gate["status"] == "failed"
    assert "different certificate identities" in gate["detail"]


def test_upgrade_smoke_without_previous_apk_is_optional_documented_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={
        "device_tests": "disabled",
        "install_smoke": False,
        "upgrade_smoke": True,
        "verify_signature": False,
        "verify_certificate_identity": False,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    apk = tmp_path / "build" / "app-release.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"apk")
    outcome = AndroidReleaseOrchestrator(
        config,
        executor=_package_executor(tmp_path, [{"path": str(apk), "type": "apk", "valid": True, "sha256": "3" * 64}]),
    ).run()
    assert outcome.passed
    gate = next(item for item in outcome.evidence["gates"] if item["name"] == "upgrade-smoke")
    assert gate["status"] == "skipped" and gate["required"] is False
    assert "previous_apk" in gate["detail"]


def test_aab_only_release_reports_device_smoke_specific_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={
        "device_tests": "disabled",
        "install_smoke": True,
        "upgrade_smoke": False,
        "verify_signature": False,
        "verify_certificate_identity": False,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    aab = tmp_path / "build" / "app-release.aab"
    aab.parent.mkdir(parents=True)
    aab.write_bytes(b"aab")
    fake_ops = mock.Mock()
    fake_device = mock.Mock()
    fake_device.as_dict.return_value = {"serial": "serial-1", "state": "device"}
    fake_ops.select_device.return_value = fake_device
    monkeypatch.setattr("devtool.android.release.AndroidDeviceOperations.from_config", lambda *a, **k: fake_ops)
    outcome = AndroidReleaseOrchestrator(
        config,
        executor=_package_executor(tmp_path, [{"path": str(aab), "type": "aab", "valid": True, "sha256": "4" * 64}]),
    ).run()
    assert not outcome.passed
    gate = next(item for item in outcome.evidence["gates"] if item["name"] == "device-smoke")
    assert gate["status"] == "failed"
    assert "installable APK" in gate["detail"]


def test_release_config_projects_release_specific_task_bindings(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.runner_options["tasks"] = {
        "build": [":app:assembleDebug"],
        "test": [":app:testDebugUnitTest"],
        "lint": [":app:lintDebug"],
        "instrumentation": [":app:connectedDebugAndroidTest"],
        "release_build": [":app:assembleRelease"],
        "release_test": [":app:testReleaseUnitTest"],
        "release_lint": [":app:lintRelease"],
    }
    orchestrator = AndroidReleaseOrchestrator(config)
    activated = orchestrator._config(release_options(config))
    tasks = activated.runner_options["tasks"]
    assert tasks["build"] == [":app:assembleRelease"]
    assert tasks["package"] == [":app:assembleRelease"]
    assert tasks["test"] == [":app:testReleaseUnitTest"]
    assert tasks["lint"] == [":app:lintRelease"]
    assert tasks["instrumentation"] == [":app:connectedDebugAndroidTest"]


def test_failed_instrumentation_blocks_signature_and_install_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={
        "device_tests": "required",
        "install_smoke": True,
        "upgrade_smoke": False,
        "verify_signature": True,
        "verify_certificate_identity": False,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    apk = tmp_path / "build" / "app-release.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"apk")

    class FakeSigningManager:
        def __init__(self, *args, **kwargs):
            pass
        def verify_artifact(self, artifact, *, expected_sha256=""):
            raise AssertionError("signature verification must not run after instrumentation failure")

    class FakeOps:
        @classmethod
        def from_config(cls, *args, **kwargs):
            raise AssertionError("device operations must not start after instrumentation failure")

    monkeypatch.setattr("devtool.android.release.AndroidSigningManager", FakeSigningManager)
    monkeypatch.setattr("devtool.android.release.AndroidDeviceOperations", FakeOps)

    def executor(_config, specs):
        name = specs[0].name
        if name == "instrumentation":
            return [OperationResult(name, specs[0].argv, 1, 0.01, "Instrumentation reported 1 failed test(s).", [])], ""
        metadata = {}
        if name == "package":
            metadata["artifacts"] = {"items": [{
                "path": str(apk), "type": "apk", "variant": "release",
                "valid": True, "sha256": "a" * 64,
            }]}
        return [OperationResult(name, specs[0].argv, 0, 0.01, "ok", [], metadata=metadata)], ""

    outcome = AndroidReleaseOrchestrator(config, executor=executor).run()
    assert not outcome.passed
    gates = {item["name"]: item for item in outcome.evidence["gates"]}
    assert gates["instrumentation"]["status"] == "failed"
    assert "signature-verification" not in gates
    assert "install-smoke" not in gates


def test_release_evidence_excludes_wrong_variant_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, release={
        "device_tests": "disabled",
        "install_smoke": False,
        "upgrade_smoke": False,
        "verify_signature": False,
        "verify_certificate_identity": False,
    })
    _patch_release_plumbing(monkeypatch, tmp_path)
    debug_apk = tmp_path / "build" / "app-debug.apk"
    debug_apk.parent.mkdir(parents=True)
    debug_apk.write_bytes(b"debug")

    outcome = AndroidReleaseOrchestrator(
        config,
        executor=_package_executor(tmp_path, [{
            "path": str(debug_apk), "type": "apk", "variant": "debug",
            "valid": True, "sha256": "d" * 64,
        }]),
    ).run()
    assert not outcome.passed
    assert outcome.evidence["artifacts"] == []
    gate = next(item for item in outcome.evidence["gates"] if item["name"] == "release-artifacts")
    assert gate["status"] == "failed"
