from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from devtool.android.device_ops import DeviceOperationResult
from devtool.android.devices import AndroidDevice
from devtool.android.instrumentation import (
    AndroidInstrumentationOrchestrator,
    InstrumentationRequest,
    _execution_classification,
    instrumentation_intent,
    resolve_instrumentation_plan,
)
from devtool.gradle_reports import collect_instrumentation_output


def _op(name: str, *, ok: bool = True, stdout: str = "", detail: str = "") -> DeviceOperationResult:
    return DeviceOperationResult(name, "passed" if ok else "failed", "device-1", 0 if ok else 1, 1, stdout, "", detail)


def _apk_pair(root: Path) -> None:
    app = root / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    test = root / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk"
    app.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    app.write_bytes(b"app")
    test.write_bytes(b"test")


class Ops:
    def __init__(self, instrument_output: str, *, instrument_ok: bool = True, cleanup_ok: bool = True) -> None:
        self.instrument_output = instrument_output
        self.instrument_ok = instrument_ok
        self.cleanup_ok = cleanup_ok
        self.uninstalls: list[str] = []
        self.pm_paths: list[str] = []

    def select_device(self, **_: Any) -> AndroidDevice:
        return AndroidDevice(serial="device-1", state="device", connection_type="usb", physical=True)

    def install(self, *_: Any, **__: Any) -> DeviceOperationResult:
        return _op("install")

    def shell(self, argv: list[str], **_: Any) -> DeviceOperationResult:
        if argv[:3] == ["pm", "list", "instrumentation"]:
            return _op("shell", stdout="instrumentation:demo.test/Runner (target=demo.app)\n")
        if argv[:2] == ["pm", "path"]:
            self.pm_paths.append(argv[-1])
            return _op("shell", stdout=f"package:/data/app/{argv[-1]}.apk\n")
        return _op("shell", ok=self.instrument_ok, stdout=self.instrument_output, detail="instrument failed")

    def uninstall(self, package: str, **_: Any) -> DeviceOperationResult:
        self.uninstalls.append(package)
        return _op("uninstall", ok=self.cleanup_ok, detail="cleanup denied")


def _orchestrator(tmp_path: Path, ops: Ops) -> AndroidInstrumentationOrchestrator:
    orchestrator = AndroidInstrumentationOrchestrator(ops)  # type: ignore[arg-type]
    environment = SimpleNamespace(environment_id="env-1")
    lease = SimpleNamespace(lease_id="lease-1", serial="device-1")
    pair = SimpleNamespace(pair_id="pair-1")
    device = AndroidDevice(serial="device-1", state="device", connection_type="usb", physical=True)
    orchestrator._canonical_authority = lambda request: (device, environment, SimpleNamespace(transport_id="transport-1"), lease)  # type: ignore[method-assign]
    orchestrator._artifact_pair = lambda request, app, test, env: pair  # type: ignore[method-assign]
    return orchestrator


def test_instrumentation_intent_is_semantic_and_stable(tmp_path: Path) -> None:
    request = InstrumentationRequest(
        tmp_path, variant="debug", policy="required", runner="demo.test/Runner",
        classes=["demo.UiTest"], methods=["demo.UiTest.testA"], runner_args={"clearPackageData": "true"},
        cleanup_app=True,
    )
    left = instrumentation_intent(request)
    right = instrumentation_intent(request)
    assert left.intent_id == right.intent_id
    assert left.as_dict()["runner_args"] == {"clearPackageData": "true"}
    changed = instrumentation_intent(InstrumentationRequest(tmp_path, variant="debug", policy="required", classes=["other.Test"]))
    assert changed.intent_id != left.intent_id


def test_resolved_plan_binds_environment_artifacts_lease_and_exact_command(tmp_path: Path) -> None:
    intent = instrumentation_intent(InstrumentationRequest(tmp_path, policy="required"))
    plan = resolve_instrumentation_plan(
        intent, environment_id="env-1", artifact_pair_id="pair-1", device_lease_id="lease-1",
        destination_id="local", runner="demo.test/Runner", target_package="demo.app", test_package="demo.test",
        command=["am", "instrument", "-w", "demo.test/Runner"],
    )
    assert plan.intent_id == intent.intent_id
    assert plan.command[-1] == "demo.test/Runner"
    other = resolve_instrumentation_plan(
        intent, environment_id="env-1", artifact_pair_id="pair-2", device_lease_id="lease-1",
        destination_id="local", runner="demo.test/Runner", target_package="demo.app", test_package="demo.test",
        command=["am", "instrument", "-w", "demo.test/Runner"],
    )
    assert other.plan_id != plan.plan_id


def test_process_crash_is_setup_failure_not_failed_test() -> None:
    output = """INSTRUMENTATION_STATUS: numtests=1
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_RESULT: shortMsg=Process crashed.
"""
    counts, failed_ids, kind, reason = _execution_classification(output, 1)
    assert kind == "setup-crash"
    assert "crashed" in reason.lower()
    assert counts["executed"] == 0
    assert counts["failed"] == 0
    assert failed_ids == []


def test_orchestrator_emits_plan_receipt_and_destination_revalidated_cleanup(tmp_path: Path) -> None:
    _apk_pair(tmp_path)
    ops = Ops("INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\nINSTRUMENTATION_CODE: -1\n")
    result = _orchestrator(tmp_path, ops).run(InstrumentationRequest(tmp_path, policy="required", runner="demo.test/Runner", skip_build=True))
    assert result.status == "passed"
    assert result.intent_id and result.plan_id and result.receipt_id
    assert result.artifact_pair_id == "pair-1"
    assert result.device_lease_id == "lease-1"
    assert result.cleanup_status == "passed"
    assert ops.pm_paths == ["demo.test"]
    receipt_path = tmp_path / ".devtool" / "private" / "android" / "instrumentation" / f"{result.receipt_id}.json"
    payload = json.loads(receipt_path.read_text())
    assert payload["plan_id"] == result.plan_id
    assert payload["cleanup_authority"]["refreshed_before_mutation"] is True
    assert payload["cleanup_results"][0]["destination"] == "android-device:device-1"


def test_cleanup_failure_is_secondary_to_test_failure(tmp_path: Path) -> None:
    _apk_pair(tmp_path)
    output = """INSTRUMENTATION_STATUS: numtests=1
INSTRUMENTATION_STATUS: class=demo.UiTest
INSTRUMENTATION_STATUS: test=fails
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS_CODE: -2
"""
    ops = Ops(output, cleanup_ok=False)
    result = _orchestrator(tmp_path, ops).run(InstrumentationRequest(tmp_path, policy="required", runner="demo.test/Runner", skip_build=True))
    assert result.status == "failed"
    assert result.failure_kind == "test-failure"
    assert result.failed == 1
    assert result.cleanup_status == "failed"
    assert "cleanup failed" in result.reason


def test_cleanup_failure_becomes_primary_only_after_successful_execution(tmp_path: Path) -> None:
    _apk_pair(tmp_path)
    ops = Ops("INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\nINSTRUMENTATION_CODE: -1\n", cleanup_ok=False)
    result = _orchestrator(tmp_path, ops).run(InstrumentationRequest(tmp_path, policy="required", runner="demo.test/Runner", skip_build=True))
    assert result.status == "failed"
    assert result.failure_kind == "cleanup"
    assert result.failed == 0
    assert result.cleanup_status == "failed"


def test_process_crash_reporter_says_setup_crash_not_failed_test(tmp_path: Path) -> None:
    payload = {
        "framework": "android-instrumentation", "status": "failed", "failure_kind": "setup-crash",
        "reason": "Process crashed", "executed": 0, "failed": 0, "returncode": 1,
    }
    collection = collect_instrumentation_output(
        "DEVTOOL_INSTRUMENTATION_RESULT " + json.dumps(payload), repo_root=tmp_path,
    )
    assert collection.metadata["failed"] == 0
    assert len(collection.issues) == 1
    assert "setup crash" in collection.issues[0].message.lower()
    assert "0 failed test" not in collection.issues[0].message.lower()
