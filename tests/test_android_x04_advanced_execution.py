from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from devtool.android.advanced_execution import (
    AndroidTestOrchestratorMode,
    DeviceCapacity,
    ExoRetryPolicy,
    QuarantinePolicy,
    android_junit_shards,
    build_advanced_instrumentation_plan,
    execution_profile,
    legacy_instrumentation_migration_diagnostics,
    matrix_evidence,
    plan_projection,
)
from devtool.android.execution_snapshot import DeviceLease
from devtool.android.instrumentation import InstrumentationRequest, instrumentation_intent
from devtool import cli as devtool_cli_module
from devtool.android import cli as android_cli


def _intent():
    return instrumentation_intent(
        InstrumentationRequest(
            project=__import__("pathlib").Path("."),
            policy="required",
            classes=["demo.A", "demo.B"],
            methods=["demo.C#works"],
        )
    )


def _lease(serial: str, *, emulator: bool = False, connection: str | None = None) -> DeviceLease:
    connection_type = connection or ("emulator" if emulator else "usb")
    return DeviceLease(
        lease_id=f"lease-{serial}", attempt_id="attempt-1", requirements_id="req-1",
        transport_id="transport-1", serial=serial, transport_serial="1", api_level="36",
        abi="arm64-v8a", connection_type=connection_type, model="Pixel", boot_completed="1",
        freshness_id=f"fresh-{serial}", device={"serial": serial, "emulator": emulator, "physical": not emulator},
    )


def test_android_junit_shards_are_typed_deterministic_and_emit_standard_args():
    intent = _intent()
    first = android_junit_shards(intent, 3)
    second = android_junit_shards(intent, 3)
    assert [x.as_dict() for x in first] == [x.as_dict() for x in second]
    assert [dict(x.runner_args) for x in first] == [
        {"numShards": "3", "shardIndex": "0"},
        {"numShards": "3", "shardIndex": "1"},
        {"numShards": "3", "shardIndex": "2"},
    ]
    assert all(x.selectors == ("demo.A", "demo.B", "demo.C#works") for x in first)


def test_orchestrator_mode_is_fail_closed_on_unqualified_or_missing_target():
    with pytest.raises(ValueError, match="qualified capability"):
        AndroidTestOrchestratorMode(enabled=True)
    with pytest.raises(ValueError, match="target instrumentation"):
        AndroidTestOrchestratorMode(enabled=True, qualification_id="qual-1")
    mode = AndroidTestOrchestratorMode(enabled=True, qualification_id="qual-1", target_instrumentation="demo.test/Runner")
    assert mode.enabled and mode.qualification_id == "qual-1"


def test_physical_device_cannot_claim_emulator_snapshot_reuse():
    with pytest.raises(ValueError, match="forbidden for physical"):
        DeviceCapacity(_lease("USB-1"), max_concurrency=1, emulator_snapshot="clean-api36")
    cap = DeviceCapacity(_lease("emulator-5554", emulator=True), max_concurrency=2, emulator_snapshot="clean-api36")
    assert cap.emulator and not cap.physical


def test_matrix_children_are_devicelease_bound_and_respect_per_device_slots():
    intent = _intent()
    capacities = (
        DeviceCapacity(_lease("USB-1"), max_concurrency=1),
        DeviceCapacity(_lease("emulator-5554", emulator=True), max_concurrency=2, emulator_snapshot="clean"),
    )
    plan = build_advanced_instrumentation_plan(intent, device_capacities=capacities, shard_count=5, workers=3)
    assert len(plan.children) == 5
    assert {x.device_lease_id for x in plan.children} == {"lease-USB-1", "lease-emulator-5554"}
    assert all(x.parent_plan_id == plan.plan_id for x in plan.children)
    emulator_children = [x for x in plan.children if x.serial == "emulator-5554"]
    assert {x.concurrency_slot for x in emulator_children}.issubset({0, 1})
    assert all(x.emulator_snapshot == "clean" for x in emulator_children)
    # TEST-X03 scheduler owns wave/resource safety; same serial cannot run in one wave.
    by_id = {x.child_id: x for x in plan.children}
    for wave in plan.execution_waves:
        serials = [by_id[c].serial for c in wave]
        assert len(serials) == len(set(serials))


def test_retry_is_exo_owned_and_quarantine_never_converts_failure_to_pass():
    retry = ExoRetryPolicy(mode="failed-only", max_attempts=3)
    assert retry.as_dict()["owner"] == "EXO"
    quarantine = QuarantinePolicy(("demo.A#flaky",), reason="tracked-flake")
    disposition = quarantine.disposition("demo.A#flaky", "failed")
    assert disposition["quarantined"] is True
    assert disposition["effective_status"] == "failed"


def test_ui_automator_profile_reuses_same_evidence_foundation():
    profile = execution_profile(ui_automator=True, cross_app=True)
    assert profile.family == "ui-automator" and profile.cross_app
    assert "instrumentation-receipt" in profile.evidence_kinds
    assert "device-lease" in profile.evidence_kinds


def test_matrix_evidence_fails_closed_when_child_receipt_is_missing():
    plan = build_advanced_instrumentation_plan(
        _intent(), device_capacities=(DeviceCapacity(_lease("USB-1")),), shard_count=2, workers=1,
    )
    evidence = matrix_evidence(plan, ({"child_id": plan.children[0].child_id, "status": "passed"},))
    assert evidence["complete"] is False
    assert evidence["missing_children"] == [plan.children[1].child_id]
    full = matrix_evidence(plan, tuple({"child_id": child.child_id, "status": "passed"} for child in plan.children))
    assert full["complete"] is True


def test_plan_projection_is_canonical_and_names_external_authorities():
    plan = build_advanced_instrumentation_plan(
        _intent(), device_capacities=(DeviceCapacity(_lease("USB-1")),), shard_count=1, workers=1,
    )
    projection = plan_projection(plan)
    assert projection["plan_id"] == plan.plan_id
    assert projection["plan"]["authorities"] == {
        "instrumentation": "ANDROID-X03",
        "device_lease": "ANDROID-X02/HOST",
        "scheduling": "TEST-X03",
        "retry": "EXO",
        "evidence": "RUN-V3",
    }


def test_legacy_instrumentation_keys_emit_migration_diagnostics_without_mutation():
    options = {"test_runner": "demo/Runner", "instrumentation": {"policy": "required"}}
    before = repr(options)
    diagnostics = legacy_instrumentation_migration_diagnostics(options)
    assert diagnostics[0]["legacy"] == "android.test_runner"
    assert diagnostics[0]["replacement"] == "android.instrumentation.test_runner"
    assert repr(options) == before


def test_environment_diff_compares_component_identities(monkeypatch: pytest.MonkeyPatch):
    def fake(_config, *, action="build"):
        value = "same" if action == "build" else "changed"
        return {
            "ok": True, "environment_id": f"env-{action}",
            "component_identities": {"android.platform": "p", "android.lanes": value},
            "qualification": {"qualification_id": f"q-{action}"},
        }
    monkeypatch.setattr(android_cli, "android_environment_payload", fake)
    payload = android_cli.android_environment_diff_payload(SimpleNamespace(), left_action="build", right_action="instrumentation")
    assert payload["changed"] is True
    rows = {x["component"]: x for x in payload["components"]}
    assert rows["android.platform"]["changed"] is False
    assert rows["android.lanes"]["changed"] is True


def test_action_doctor_does_not_probe_device_for_build(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(android_cli, "android_environment_payload", lambda *_a, **_k: {"ok": True, "environment_id": "env"})
    monkeypatch.setattr(android_cli, "android_device_doctor_payload", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("device probe")))
    payload = android_cli.android_action_doctor_payload(SimpleNamespace(), action="build")
    assert payload["ok"] is True and payload["device_required"] is False and payload["device"] is None


def test_cli_exposes_x04_read_only_ux_and_advanced_plan_flags():
    parser = devtool_cli_module.build_parser()
    args = parser.parse_args(["android", "env", "diff", "--left-action", "build", "--right-action", "instrumentation"])
    assert args.android_command == "env" and args.android_env_action == "diff"
    args = parser.parse_args(["android", "qualification", "show", "--action", "release"])
    assert args.android_command == "qualification" and args.android_qualification_action == "show"
    args = parser.parse_args(["android", "doctor", "--for", "instrumentation"])
    assert args.android_doctor_for == "instrumentation"
    args = parser.parse_args(["android", "instrumentation", "--plan", "--shards", "4", "--retry-failed-attempts", "2", "--quarantine", "demo.A#flaky", "--ui-automator"])
    assert args.plan and args.shards == 4 and args.retry_failed_attempts == 2 and args.ui_automator


def test_advanced_preview_keeps_device_binding_pending_and_exo_retry(monkeypatch: pytest.MonkeyPatch):
    request = InstrumentationRequest(project=__import__("pathlib").Path("."), policy="required")
    monkeypatch.setattr("devtool.android.instrumentation.request_from_config", lambda *_a, **_k: request)
    config = SimpleNamespace(runner_options={"android": {}})
    payload = android_cli.android_advanced_instrumentation_plan_payload(
        config, shard_count=2, retry_failed_attempts=3, quarantine=["demo.A#flaky"], ui_automator=True, cross_app=True,
    )
    assert payload["device_binding"] == "pending-DeviceLease"
    assert payload["retry"] == {"mode": "failed-only", "max_attempts": 3, "owner": "EXO"}
    assert payload["profile"]["family"] == "ui-automator"
    assert len(payload["shards"]) == 2

def test_stale_android_backup_fixture_is_retired():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    assert not (root / "tests" / "test_android_connected_task_skip.py.bak-20260821-162146").exists()

def test_raw_advanced_runner_args_are_rejected_in_favor_of_typed_plan():
    request = InstrumentationRequest(project=__import__("pathlib").Path("."), runner_args={"numShards": "4"})
    with pytest.raises(Exception, match="typed sharding/orchestrator"):
        instrumentation_intent(request)


def test_advanced_preview_preserves_explicit_test_selection(monkeypatch: pytest.MonkeyPatch):
    config = SimpleNamespace(runner_options={"android": {}})
    # Use the real request_from_config shape via a tiny fake so selection flow is explicit.
    def fake(_config, **kwargs):
        return InstrumentationRequest(
            project=__import__("pathlib").Path("."), policy="required",
            classes=list(kwargs.get("classes") or []), methods=list(kwargs.get("methods") or []),
            annotations=list(kwargs.get("annotations") or []), not_annotations=list(kwargs.get("not_annotations") or []),
            size=str(kwargs.get("size") or ""), runner_args=dict(kwargs.get("runner_args") or {}),
        )
    monkeypatch.setattr("devtool.android.instrumentation.request_from_config", fake)
    payload = android_cli.android_advanced_instrumentation_plan_payload(
        config, classes=["demo.Selected"], methods=["demo.Selected#works"], shard_count=2,
    )
    assert payload["intent"]["classes"] == ["demo.Selected"]
    assert payload["intent"]["methods"] == ["demo.Selected#works"]
    assert all("demo.Selected" in shard["selectors"] for shard in payload["shards"])
