from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from devtool.android.capabilities import AndroidToolResolution
from devtool.android.device_ops import (
    AndroidDeviceOperationError,
    AndroidDeviceOperations,
    DeviceOperationResult,
    capture_failure_evidence,
)
from devtool.android.devices import AndroidDevice, AndroidDeviceError, AndroidDeviceManager
from devtool.android.evidence import AndroidReleaseEvidenceSession
from devtool.android.instrumentation import (
    AndroidInstrumentationOrchestrator,
    InstrumentationError,
    InstrumentationRequest,
    _choose_runner,
    _find_apks,
    request_from_config,
)
from devtool.android.release import AndroidReleaseOrchestrator
from devtool.android.signing import (
    AndroidSigningError,
    _run_secret_command,
    signing_profile,
    task_requires_release_signing,
)
from devtool.models import CommandSpec, Issue, OperationResult, ProjectConfig
from devtool.project_runners import AndroidGradleRunner


PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


def _config(tmp_path: Path, *, android: dict[str, Any] | None = None, release: dict[str, Any] | None = None, signing: dict[str, Any] | None = None) -> ProjectConfig:
    android_options: dict[str, Any] = {
        "release": release or {},
        "signing": signing if signing is not None else {"release": {"mode": "project", "policy": "optional"}},
    }
    if android:
        android_options.update(android)
    return ProjectConfig(
        repo_root=tmp_path,
        kind="android",
        runner="android",
        selected_target="app",
        variant="release",
        project_root=str(tmp_path),
        build_target=":app",
        runner_options={"android": android_options},
    )


def _device(serial: str = "device-1", *, state: str = "device", model: str = "Pixel_8", emulator: bool = False) -> AndroidDevice:
    return AndroidDevice(
        serial=serial,
        state=state,
        model=model,
        device="pixel",
        connection_type="usb",
        physical=not emulator,
        emulator=emulator,
    )


def _op(name: str, *, ok: bool = True, serial: str = "device-1", stdout: str = "", stderr: str = "", detail: str = "") -> DeviceOperationResult:
    return DeviceOperationResult(name, "passed" if ok else "failed", serial, 0 if ok else 1, 1, stdout, stderr, detail)


class InventoryManager(AndroidDeviceManager):
    def __init__(self, devices: list[AndroidDevice] | None = None, *, metadata: dict[str, Any] | None = None, compatible: bool | None = True) -> None:
        super().__init__(AndroidToolResolution("adb", "termux-adb", True, path="/fake/adb", compatible=compatible, metadata=metadata or {}), env={})
        self.devices = devices or []

    def list_devices(self, *, enrich: bool = False) -> list[AndroidDevice]:
        return list(self.devices)

    def command_prefix(self, serial: str = "") -> list[str]:
        return ["/fake/adb", "-s", serial or "device-1"]


def test_instrumentation_config_string_false_and_malformed_values(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        android={
            "instrumentation": {
                "policy": "required",
                "cleanup_app": "false",
                "keep_installed": "false",
                "capture_logcat_on_failure": "false",
                "capture_screenshot_on_failure": "false",
                "wait_timeout_seconds": "bad",
            }
        },
    )
    with pytest.raises(InstrumentationError, match="wait_timeout_seconds.*number"):
        request_from_config(cfg)

    cfg.runner_options["android"]["instrumentation"]["wait_timeout_seconds"] = "0"
    request = request_from_config(cfg)
    assert request.cleanup_app is False
    assert request.keep_installed is False
    assert request.capture_logcat_on_failure is False
    assert request.capture_screenshot_on_failure is False
    assert request.wait_timeout_seconds == 0

    cfg.runner_options["android"]["instrumentation"]["runner_args"] = ["not", "a", "table"]
    with pytest.raises(InstrumentationError, match="runner_args"):
        request_from_config(cfg)


def test_android_runner_instrumentation_cli_boolean_strings_and_runner_args(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        android={
            "instrumentation": {
                "policy": "required",
                "keep_installed": "false",
                "capture_logcat_on_failure": "false",
                "capture_screenshot_on_failure": "false",
                "runner_args": {"clearPackageData": True},
            }
        },
    )
    spec = AndroidGradleRunner().build_specs(cfg, "instrumentation")[0]
    assert "--keep-installed" not in spec.argv
    assert "--no-logcat-on-failure" in spec.argv
    assert "--no-screenshot-on-failure" in spec.argv
    assert "clearPackageData=True" in spec.argv

    cfg.runner_options["android"]["instrumentation"]["runner_args"] = "bad"
    with pytest.raises(SystemExit, match="runner_args"):
        AndroidGradleRunner().build_specs(cfg, "instrumentation")


def test_find_apks_requires_same_module_pair(tmp_path: Path) -> None:
    app = tmp_path / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    lonely_test = tmp_path / "other" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "other-debug-androidTest.apk"
    app.parent.mkdir(parents=True)
    lonely_test.parent.mkdir(parents=True)
    app.write_bytes(b"app")
    lonely_test.write_bytes(b"test")

    with pytest.raises(InstrumentationError, match="same-module"):
        _find_apks(tmp_path, "debug")

    paired_app = tmp_path / "other" / "build" / "outputs" / "apk" / "debug" / "other-debug.apk"
    paired_app.parent.mkdir(parents=True, exist_ok=True)
    paired_app.write_bytes(b"app2")
    assert _find_apks(tmp_path, "debug") == (paired_app, lonely_test)


def test_configured_instrumentation_runner_must_exist_and_match_target() -> None:
    listing = "instrumentation:com.example.test/androidx.test.runner.AndroidJUnitRunner (target=com.example.app)\n"
    assert _choose_runner(listing, configured="com.example.test/androidx.test.runner.AndroidJUnitRunner", target_package="com.example.app")[0].startswith("com.example.test")
    with pytest.raises(InstrumentationError, match="not installed"):
        _choose_runner(listing, configured="missing/Runner", target_package="")
    with pytest.raises(InstrumentationError, match="targets"):
        _choose_runner(listing, configured="com.example.test/androidx.test.runner.AndroidJUnitRunner", target_package="wrong.pkg")


class FakeInstrumentationOps:
    def __init__(self, *, runner_output: str, instrument_output: str = "", shell_fail: bool = False) -> None:
        self.runner_output = runner_output
        self.instrument_output = instrument_output
        self.shell_fail = shell_fail
        self.uninstalled: list[str] = []

    def select_device(self, **_: Any) -> AndroidDevice:
        return _device()

    def wait_for_device(self, **_: Any) -> AndroidDevice:
        return _device()

    def install(self, *_: Any, **__: Any) -> DeviceOperationResult:
        return _op("install")

    def uninstall(self, package: str, **_: Any) -> DeviceOperationResult:
        self.uninstalled.append(package)
        return _op("uninstall")

    def shell(self, argv: list[str], **_: Any) -> DeviceOperationResult:
        if argv[:3] == ["pm", "list", "instrumentation"]:
            if self.shell_fail:
                raise AndroidDeviceOperationError("runner discovery failed")
            return _op("shell", stdout=self.runner_output)
        return _op("shell", stdout=self.instrument_output)


def _apk_pair(root: Path) -> None:
    app = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    test = root / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk"
    app.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    app.write_bytes(b"app")
    test.write_bytes(b"test")


def test_required_instrumentation_zero_tests_fails(tmp_path: Path) -> None:
    _apk_pair(tmp_path)
    ops = FakeInstrumentationOps(
        runner_output="instrumentation:com.example.test/Runner (target=com.example.app)\n",
        instrument_output="INSTRUMENTATION_STATUS: numtests=0\nINSTRUMENTATION_STATUS_CODE: 0\n",
    )
    request = InstrumentationRequest(tmp_path, variant="debug", policy="required", runner="com.example.test/Runner", skip_build=True)
    result = AndroidInstrumentationOrchestrator(device_ops=ops).run(request)  # type: ignore[arg-type]
    assert result.status == "failed"
    assert result.returncode == 1
    assert "zero tests" in result.reason


def test_test_apk_cleanup_runs_when_configured_runner_discovery_fails(tmp_path: Path) -> None:
    _apk_pair(tmp_path)
    ops = FakeInstrumentationOps(runner_output="", shell_fail=True)
    request = InstrumentationRequest(
        tmp_path,
        variant="debug",
        policy="required",
        runner="com.example.test/Runner",
        target_package="com.example.app",
        skip_build=True,
        keep_installed=False,
    )
    result = AndroidInstrumentationOrchestrator(device_ops=ops).run(request)  # type: ignore[arg-type]
    assert result.status == "failed"
    assert "com.example.test" in ops.uninstalled


class FakeSmokeOps:
    def __init__(self, *, process_ok: bool = True, cleanup_ok: bool = True, old_package: str = "com.example.app", new_package: str = "com.example.app") -> None:
        self.process_ok = process_ok
        self.cleanup_ok = cleanup_ok
        self.old_package = old_package
        self.new_package = new_package
        self.installs: list[dict[str, Any]] = []

    def apk_metadata(self, apk: str | Path) -> dict[str, str]:
        name = Path(apk).name
        package = self.old_package if "old" in name else self.new_package
        return {"package": package, "activity": ".Main"}

    def install(self, apk: str | Path, **kwargs: Any) -> DeviceOperationResult:
        self.installs.append({"apk": str(apk), **kwargs})
        return _op("install")

    def launch(self, *_: Any, **__: Any) -> DeviceOperationResult:
        return _op("launch")

    def process_state(self, *_: Any, **__: Any) -> DeviceOperationResult:
        return _op("process", ok=self.process_ok, detail="process missing")

    def uninstall(self, *_: Any, **__: Any) -> DeviceOperationResult:
        return _op("uninstall", ok=self.cleanup_ok, detail="cleanup denied")


def test_install_and_upgrade_smoke_fail_closed(tmp_path: Path) -> None:
    apk = tmp_path / "new.apk"
    old = tmp_path / "old.apk"
    apk.write_bytes(b"apk")
    old.write_bytes(b"old")

    ops = FakeSmokeOps(process_ok=False)
    smoke = AndroidDeviceOperations.install_smoke(ops, apk, keep_installed=True)  # type: ignore[arg-type]
    assert smoke.status == "failed"
    assert ops.installs[-1]["test_only"] is False

    ops = FakeSmokeOps(cleanup_ok=False)
    smoke = AndroidDeviceOperations.install_smoke(ops, apk, keep_installed=False)  # type: ignore[arg-type]
    assert smoke.status == "failed"
    assert "cleanup" in smoke.detail

    ops = FakeSmokeOps(old_package="old.pkg", new_package="new.pkg")
    with pytest.raises(AndroidDeviceOperationError, match="old APK package"):
        AndroidDeviceOperations.upgrade_smoke(ops, old, apk, package="new.pkg")  # type: ignore[arg-type]


def test_process_death_smoke_requires_successful_kill() -> None:
    class Ops:
        def select_device(self, **_: Any) -> AndroidDevice:
            return _device()
        def shell(self, *_: Any, **__: Any) -> DeviceOperationResult:
            return _op("shell", ok=False, detail="am kill denied")
        def launch(self, *_: Any, **__: Any) -> DeviceOperationResult:
            return _op("launch")
    smoke = AndroidDeviceOperations.process_death_smoke(Ops(), "com.example.app")  # type: ignore[arg-type]
    assert smoke.status == "failed"
    assert "kill" in smoke.detail


def test_device_policy_preferred_is_explicit_and_no_permission_is_specific() -> None:
    manager = InventoryManager([_device("one"), _device("two")])
    with pytest.raises(AndroidDeviceError, match="preferred"):
        manager.select("preferred")
    with pytest.raises(AndroidDeviceError, match="Preferred Android device"):
        manager.select("preferred", preferred="missing")

    perm_manager = InventoryManager([], metadata={"termux_usb": {"termux_usb": "termux-usb", "termux_api_app": True, "list_status": "ready", "detail": "USB permission denied"}})
    doctor = perm_manager.doctor()
    assert doctor.code == "usb-permission-not-granted"
    assert doctor.status == "error"


def test_signing_booleans_legacy_policy_task_classifier_and_bad_command(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        signing={
            "release": {
                "mode": "devtool",
                "require_for_release": "true",
                "store_password_stdin": "false",
                "key_password_stdin": "false",
                "allow_prompt": "false",
            }
        },
    )
    profile = signing_profile(cfg)
    assert profile.policy == "release-required"
    assert profile.store_password_stdin is False
    assert profile.key_password_stdin is False
    assert profile.allow_prompt is False
    assert task_requires_release_signing("assembleRelease") is True
    assert task_requires_release_signing("bundleFreeRelease") is True
    assert task_requires_release_signing("releaseNotes") is False
    with pytest.raises(AndroidSigningError, match="malformed"):
        _run_secret_command("'unterminated", env={})


def _fake_context() -> Any:
    class Tool:
        path = "/fake/tool"
        available = True
        compatible = True
        rejection_reason = ""
        def as_dict(self) -> dict[str, Any]:
            return {"path": self.path, "available": True}
    class Context:
        def as_dict(self) -> dict[str, Any]:
            return {"tools": {}}
        def tool(self, name: str) -> Tool:
            return Tool()
    return Context()


def _release_executor(results: list[OperationResult]) -> Any:
    def execute(_config: ProjectConfig, specs: list[CommandSpec]) -> tuple[list[OperationResult], str]:
        # Return one prepared result per requested command to mimic run_specs.
        return results[: max(1, len(specs))], ""
    return execute


def test_android_release_zero_gates_zero_specs_and_error_diagnostics_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *_a, **_k: _fake_context())
    cfg = _config(tmp_path, release={"unit_tests": "false", "lint": "false", "build_artifacts": "false", "device_tests": "disabled", "install_smoke": "false", "upgrade_smoke": "false", "verify_signature": "false", "verify_certificate_identity": "false"})
    zero = AndroidReleaseOrchestrator(cfg, executor=_release_executor([])).run(dry_run=True).evidence
    assert zero["verdict"] == "failed"
    assert any(g["name"] == "release-plan" for g in zero["gates"])

    monkeypatch.setattr(AndroidGradleRunner, "build_specs", lambda self, config, action: [])
    cfg = _config(tmp_path, release={"unit_tests": True, "lint": "false", "build_artifacts": "false", "device_tests": "disabled", "install_smoke": "false", "upgrade_smoke": "false", "verify_signature": "false", "verify_certificate_identity": "false"})
    empty_phase = AndroidReleaseOrchestrator(cfg, executor=_release_executor([])).run(dry_run=True).evidence
    assert empty_phase["verdict"] == "failed"
    assert any("zero command specs" in g["detail"] for g in empty_phase["gates"])

    monkeypatch.setattr(AndroidGradleRunner, "build_specs", lambda self, config, action: [CommandSpec(action, ["gradle", action])])
    error_result = OperationResult("package", ["gradle"], 0, 0.0, "", [Issue("error", "gradle", "", "E", "boom")])
    cfg = _config(tmp_path, release={"unit_tests": "false", "lint": "false", "build_artifacts": True, "device_tests": "disabled", "install_smoke": "false", "upgrade_smoke": "false", "verify_signature": "false", "verify_certificate_identity": "false"})
    failed = AndroidReleaseOrchestrator(cfg, executor=_release_executor([error_result])).run(dry_run=True).evidence
    assert failed["verdict"] == "failed"
    assert any(g["name"] == "package" and g["status"] == "failed" for g in failed["gates"])


def test_android_release_requires_artifacts_and_honors_valid_string_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *_a, **_k: _fake_context())
    monkeypatch.setattr(AndroidGradleRunner, "build_specs", lambda self, config, action: [CommandSpec(action, ["gradle", action])])
    no_artifacts = OperationResult("package", ["gradle"], 0, 0.0, "ok", [], metadata={})
    cfg = _config(tmp_path, release={"unit_tests": "false", "lint": "false", "build_artifacts": True, "device_tests": "disabled", "install_smoke": "false", "upgrade_smoke": "false", "verify_signature": "false", "verify_certificate_identity": "false"})
    evidence = AndroidReleaseOrchestrator(cfg, executor=_release_executor([no_artifacts])).run(dry_run=True).evidence
    assert evidence["verdict"] == "failed"
    assert any(g["name"] == "release-artifacts" for g in evidence["gates"])

    payload = {"items": [{"type": "apk", "path": "app-release.apk", "valid": "false"}]}
    assert AndroidReleaseOrchestrator._release_artifacts(cfg, payload) == []


def test_invalid_release_signing_config_is_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devtool.android.release.resolve_android_execution_context", lambda *_a, **_k: _fake_context())
    monkeypatch.setattr(AndroidGradleRunner, "build_specs", lambda self, config, action: [CommandSpec(action, ["gradle", action])])
    cfg = _config(
        tmp_path,
        release={"unit_tests": "false", "lint": "false", "build_artifacts": "false", "device_tests": "disabled", "install_smoke": "false", "upgrade_smoke": "false", "verify_signature": True},
        signing={"release": {"mode": "devtool", "policy": "release-required", "keystore": str(tmp_path / "missing.jks"), "alias": "release", "store_password_env": "STORE"}},
    )
    evidence = AndroidReleaseOrchestrator(cfg, executor=_release_executor([])).run(dry_run=True).evidence
    assert evidence["verdict"] == "failed"
    assert any(g["name"] == "signing-config" and g["status"] == "failed" for g in evidence["gates"])


def test_release_evidence_with_zero_gates_never_passes(tmp_path: Path) -> None:
    session = AndroidReleaseEvidenceSession(tmp_path, "app", "release")
    assert session.passed is False


def test_screenshot_png_validation_and_redacted_failure_evidence(tmp_path: Path) -> None:
    class BadScreenshotOps:
        def select_device(self, **_: Any) -> AndroidDevice:
            return _device()
        def _run_bytes(self, *_: Any, **__: Any) -> tuple[AndroidDevice, bytes]:
            return _device(), b"not a png"
    with pytest.raises(AndroidDeviceOperationError, match="PNG"):
        AndroidDeviceOperations.screenshot(BadScreenshotOps(), tmp_path / "bad.png")  # type: ignore[arg-type]

    class EvidenceOps:
        def logcat(self, **_: Any) -> DeviceOperationResult:
            return _op("logcat", stdout="token=supersecret")
        def screenshot(self, path: str | Path, **_: Any) -> DeviceOperationResult:
            Path(path).write_bytes(PNG)
            return _op("screenshot", detail=str(path), stdout="", stderr="",). __class__("screenshot", "passed", "device-1", 0, 1, "", "", str(path), {"path": str(path)})
        def package_info(self, *_: Any, **__: Any) -> DeviceOperationResult:
            return _op("package", stdout="password=supersecret")
    evidence = capture_failure_evidence(EvidenceOps(), report_dir=tmp_path / "evidence", package="com.example.app")  # type: ignore[arg-type]
    assert evidence["screenshot_png_valid"] is True
    assert evidence["logcat_redacted"] is True
    assert evidence["package_redacted"] is True
    assert "supersecret" not in Path(evidence["logcat"]).read_text()
    assert "supersecret" not in Path(evidence["package"]).read_text()
