from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.android.instrumentation import (
    AndroidInstrumentationOrchestrator,
    InstrumentationRequest,
    _execution_classification,
    gradle_receipts_from_current_run,
)
from devtool.android.device_ops import DeviceOperationResult
from devtool.android.devices import AndroidDevice
from devtool.core_runtime import private_run_root
from devtool.models import ProjectConfig
from devtool.project_runners import AndroidGradleRunner


def _op(name: str, *, ok: bool = True, stdout: str = "", detail: str = "") -> DeviceOperationResult:
    return DeviceOperationResult(name, "passed" if ok else "failed", "device-1", 0 if ok else 1, 1, stdout, "", detail)


class _Ops:
    def __init__(self, output: str, *, cleanup_ok: bool = True) -> None:
        self.output = output
        self.cleanup_ok = cleanup_ok
        self.serials: list[str] = []

    def select_device(self, **_: object) -> AndroidDevice:
        return AndroidDevice(serial="device-1", state="device", connection_type="usb", physical=True)

    def install(self, *_: object, serial: str = "", **__: object) -> DeviceOperationResult:
        self.serials.append(serial)
        return _op("install")

    def shell(self, argv: list[str], *, serial: str = "", **_: object) -> DeviceOperationResult:
        self.serials.append(serial)
        if argv[:3] == ["pm", "list", "instrumentation"]:
            return _op("shell", stdout="instrumentation:demo.test/Runner (target=demo.app)\n")
        if argv[:2] == ["pm", "path"]:
            return _op("shell", stdout=f"package:/data/app/{argv[-1]}.apk\n")
        return _op("shell", stdout=self.output)

    def uninstall(self, package: str, *, serial: str = "", **_: object) -> DeviceOperationResult:
        self.serials.append(serial)
        return _op("uninstall", ok=self.cleanup_ok, detail="cleanup denied")


def _apks(root: Path) -> None:
    app = root / "app/build/outputs/apk/debug/app-debug.apk"
    test = root / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
    app.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    app.write_bytes(b"app")
    test.write_bytes(b"test")


def test_current_run_gradle_receipt_is_recovered_from_authoritative_journal(tmp_path: Path) -> None:
    run_id = "android-g2-run"
    run_dir = private_run_root(tmp_path) / run_id
    run_dir.mkdir(parents=True)
    receipt = {"receipt_id": "gradle-r1", "plan_digest": "plan-1", "success": True, "process_executed": True}
    payload = {
        "type": "TaskFinished", "event_type": "node.finished", "run_id": run_id,
        "sequence": 3, "task": "instrumentation-build", "success": True, "status": "succeeded",
        "metadata": {"gradle_execution_receipt": receipt},
    }
    (run_dir / "execution-events.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert gradle_receipts_from_current_run(tmp_path, run_id=run_id) == (receipt,)


def test_project_runner_passes_real_target_into_canonical_instrumentation_subprocess(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name='g2'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("android { compileSdk 36 }\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path, project_root=str(tmp_path), kind="android", runner="android",
        selected_target="phone", variant="debug",
        runner_options={
            "android": {"instrumentation": {"variant": "demoDebug", "module": "mobile", "policy": "required"},
                        "variants": {"demoDebug": {"build_type": "debug", "module": "mobile"}}},
            "gradle": {"command": "gradle"},
        },
    )
    specs = AndroidGradleRunner().build_specs(config, "instrumentation")
    device = next(spec for spec in specs if "devtool.android_instrumentation" in spec.argv)
    idx = device.argv.index("--target")
    assert device.argv[idx + 1] == "phone"
    assert device.argv[device.argv.index("--variant") + 1] == "demoDebug"
    assert any(":mobile:assembleDemoDebug" in spec.argv for spec in specs[:-1])


def test_destination_and_single_device_lease_flow_through_plan_receipt_and_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _apks(tmp_path)
    output = "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n"
    ops = _Ops(output)
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android", runner="android", selected_target="phone")
    orchestrator = AndroidInstrumentationOrchestrator(ops, config=config)  # type: ignore[arg-type]
    environment = SimpleNamespace(environment_id="env-1", component_identities={}, path_provenance={})
    transport = SimpleNamespace(transport_id="transport-1", destination_id="ssh:tablet")
    lease = SimpleNamespace(lease_id="lease-1", serial="device-1")
    pair = SimpleNamespace(pair_id="pair-1")
    device = AndroidDevice(serial="device-1", state="device", connection_type="usb", physical=True)
    orchestrator._canonical_authority = lambda request: (device, environment, transport, lease)  # type: ignore[method-assign]
    orchestrator._artifact_pair = lambda request, app, test, env: pair  # type: ignore[method-assign]
    monkeypatch.setattr("devtool.android.execution_snapshot.build_android_execution_snapshot", lambda **kw: SimpleNamespace(snapshot_id="snap-1", as_dict=lambda: {}))
    result = orchestrator.run(InstrumentationRequest(tmp_path, policy="required", runner="demo.test/Runner", skip_build=True))
    receipt = json.loads((tmp_path / ".devtool/private/android/instrumentation" / f"{result.receipt_id}.json").read_text())
    assert receipt["destination_id"] == "ssh:tablet"
    assert result.device_lease_id == "lease-1"
    assert all(serial == "device-1" for serial in ops.serials)


def test_process_crash_and_real_test_failure_remain_distinct() -> None:
    crash = "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: -2\nINSTRUMENTATION_RESULT: shortMsg=Process crashed.\n"
    counts, ids, kind, _ = _execution_classification(crash, 1)
    assert kind == "setup-crash" and counts["executed"] == 0 and counts["failed"] == 0 and ids == []
    failed = "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS: class=demo.T\nINSTRUMENTATION_STATUS: test=f\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: -2\n"
    counts, ids, kind, _ = _execution_classification(failed, 1)
    assert kind == "test-failure" and counts["failed"] == 1 and ids == ["demo.T.f"]
