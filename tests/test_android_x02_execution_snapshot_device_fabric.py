from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from devtool.android.devices import AndroidDevice
import devtool.android.environment as android_environment_module
from devtool.android.environment import (
    ANDROID_HELPERS,
    EnvironmentView,
    android_environment_reuse_components,
    inspect_qualify_repair_android_environment,
    resolve_android_environment,
)
from devtool.android.execution_snapshot import (
    AndroidTransport,
    acquire_adbd_mutation_lease,
    acquire_device_lease,
    build_android_execution_snapshot,
    build_artifact_pair,
    consume_destination_android_environment,
    device_requirements_from_config,
    operations_for_device_lease,
    persist_android_execution_snapshot,
    restore_adbd_mutation_lease,
)
from devtool.bootstrap import _local_freshness_matches, bootstrap_capability_freshness
from devtool.core_runtime import private_run_root
from devtool.models import ProjectConfig
from devtool.run_store import RunCatalog
from devtool.termux_toolchains import RuntimeIdentity


def _project(root: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name='x'\n", encoding="utf-8")
    (root / "build.gradle").write_text(
        "plugins { id 'com.android.application' version '8.9.0' apply false }\n"
        "android { compileSdk 36\n buildToolsVersion '36.0.0'\n}\n",
        encoding="utf-8",
    )


def _sdk(root: Path, *, missing: str = "") -> Path:
    sdk = root / "sdk"
    platform = sdk / "platforms" / "android-36"
    platform.mkdir(parents=True)
    (platform / "android.jar").write_bytes(b"jar")
    bt = sdk / "build-tools" / "36.0.0"
    (bt / "lib").mkdir(parents=True)
    (bt / "source.properties").write_text("Pkg.Revision=36.0.0\n", encoding="utf-8")
    (bt / "lib" / "d8.jar").write_bytes(b"d8")
    for name in ANDROID_HELPERS:
        if name == missing:
            continue
        path = bt / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return sdk


def _config(root: Path, sdk: Path, *, adb: dict | None = None) -> ProjectConfig:
    android = {
        "sdk_root": str(sdk),
        "build_tools": {"version": "36.0.0"},
        "release": {"variant": "release", "module": "app"},
    }
    if adb is not None:
        android["adb"] = dict(adb)
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="android",
        runner="android",
        selected_target="app",
        variant="debug",
        runner_options={"android": android},
    )


def _resolved(root: Path, sdk: Path, *, gradle: dict | None = None):
    _request, qualification, resolved = resolve_android_environment(
        _config(root, sdk),
        action="release",
        env={"PATH": os.environ.get("PATH", "")},
        gradle_metadata=gradle or {},
        device_required=False,
    )
    assert qualification.ok and resolved is not None
    return resolved


def test_environment_identity_and_reuse_components_ignore_sdk_relocation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    left = _sdk(tmp_path / "left")
    right = _sdk(tmp_path / "right")
    gradle = {
        "gradle_execution_receipt": {
            "context_digest": "ctx",
            "plan_digest": "plan",
            "jdk_identity": "jdk-21",
            "jdk_home": "/some/controller/path/jdk",
            "launcher_identity": "gradle-real-9.7.1",
        }
    }
    a = _resolved(project, left, gradle=gradle)
    b = _resolved(project, right, gradle=gradle)
    assert a.environment_id == b.environment_id
    assert android_environment_reuse_components(a) == android_environment_reuse_components(b)
    assert a.path_provenance["sdk"] != b.path_provenance["sdk"]
    assert android_environment_reuse_components(a, domains=["android.platform", "android.gradle"]) == {
        "android.platform": a.component_identities["android.platform"],
        "android.gradle": a.component_identities["android.gradle"],
    }


def test_gradle_jdk_authority_participates_semantically_but_home_is_provenance(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    one = _resolved(tmp_path, sdk, gradle={"gradle_jdk_identity": "jdk-A", "gradle_jdk_home": "/a", "gradle_launcher_identity": "g"})
    relocated = _resolved(tmp_path, sdk, gradle={"gradle_jdk_identity": "jdk-A", "gradle_jdk_home": "/b", "gradle_launcher_identity": "g"})
    changed = _resolved(tmp_path, sdk, gradle={"gradle_jdk_identity": "jdk-B", "gradle_jdk_home": "/b", "gradle_launcher_identity": "g"})
    assert one.component_identities["android.gradle"] == relocated.component_identities["android.gradle"]
    assert one.environment_id == relocated.environment_id
    assert changed.component_identities["android.gradle"] != one.component_identities["android.gradle"]


def test_exact_artifact_pair_is_content_and_plan_bound_not_path_bound(tmp_path: Path) -> None:
    project = tmp_path / "project"; project.mkdir(); _project(project)
    sdk = _sdk(tmp_path / "android")
    environment = _resolved(project, sdk)
    a = tmp_path / "one" / "app-release.apk"; a.parent.mkdir(); a.write_bytes(b"apk")
    b = tmp_path / "two" / "app-release.apk"; b.parent.mkdir(); b.write_bytes(b"apk")
    digest = hashlib.sha256(b"apk").hexdigest()
    receipt_a = {"receipt_id": "receipt-path-a", "plan_digest": "plan-X", "artifact_refs": [str(a)], "producer_artifact_refs": {":app:assembleRelease": [str(a)]}}
    receipt_b = {"receipt_id": "receipt-path-b", "plan_digest": "plan-X", "artifact_refs": [str(b)], "producer_artifact_refs": {":app:assembleRelease": [str(b)]}}
    raw_a = {"valid": True, "type": "apk", "path": str(a), "sha256": digest, "producer_task": ":app:assembleRelease"}
    raw_b = {"valid": True, "type": "apk", "path": str(b), "sha256": digest, "producer_task": ":app:assembleRelease"}
    pair_a = build_artifact_pair(repo_root=project, module="app", variant_identity="release-lane", environment=environment, app=raw_a, gradle_receipt=receipt_a, source_identity="src", config_identity="cfg")
    pair_b = build_artifact_pair(repo_root=project, module="app", variant_identity="release-lane", environment=environment, app=raw_b, gradle_receipt=receipt_b, source_identity="src", config_identity="cfg")
    assert pair_a.pair_id == pair_b.pair_id
    assert pair_a.gradle_receipt_id != pair_b.gradle_receipt_id
    assert pair_a.app.path != pair_b.app.path


def test_artifact_pair_hash_drift_and_receipt_mismatch_fail_closed(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    environment = _resolved(tmp_path, sdk)
    apk = tmp_path / "app-release.apk"; apk.write_bytes(b"actual")
    raw = {"type": "apk", "path": str(apk), "sha256": "0" * 64, "producer_task": ":app:assembleRelease"}
    with pytest.raises(ValueError, match="hash drift"):
        build_artifact_pair(repo_root=tmp_path, module="app", variant_identity="v", environment=environment, app=raw)
    raw["sha256"] = hashlib.sha256(apk.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="not linked"):
        build_artifact_pair(
            repo_root=tmp_path, module="app", variant_identity="v", environment=environment, app=raw,
            gradle_receipt={"plan_digest": "p", "artifact_refs": ["some-other.apk"]},
        )


class _Manager:
    def __init__(self, device: AndroidDevice) -> None:
        self.device = device
        self.select_calls = 0
        self.list_calls = 0

    def select(self, *args, **kwargs):
        self.select_calls += 1
        return [self.device]

    def list_devices(self, *, enrich: bool = False):
        self.list_calls += 1
        return [self.device]


def _transport(environment_id: str) -> AndroidTransport:
    return AndroidTransport(
        transport_id="transport-1", destination_id="local", provider="sdk", adb_path="/fake/adb",
        adb_version="1.0.41", runtime="termux", runtime_arch="arm64", sdk_environment_id=environment_id,
    )


def test_device_requirements_and_lease_pin_one_serial_across_operations(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    config = _config(tmp_path, sdk, adb={"device_policy": "serial", "serial": "ABC", "min_api": 34, "abi": "arm64-v8a", "transport": "usb"})
    requirements = device_requirements_from_config(config)
    device = AndroidDevice("ABC", "device", transport_id="7", connection_type="usb", api_level="35", abi="arm64-v8a", boot_completed="1")
    manager = _Manager(device)
    lease = acquire_device_lease(_transport("env"), requirements, env={}, attempt_id="run-1", manager=manager)
    assert lease.serial == "ABC" and lease.attempt_id == "run-1"
    ops = operations_for_device_lease(_transport("env"), lease, env={})
    ops.manager.select = lambda *a, **k: (_ for _ in ()).throw(AssertionError("lease must prevent reselection"))
    assert ops.select_device().serial == "ABC"
    with pytest.raises(Exception, match="bound to"):
        ops.select_device(serial="OTHER")


def test_device_requirement_mismatch_fails_before_lease(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    config = _config(tmp_path, sdk, adb={"device_policy": "serial", "serial": "ABC", "min_api": 36})
    manager = _Manager(AndroidDevice("ABC", "device", api_level="35", abi="arm64-v8a", boot_completed="1"))
    with pytest.raises(RuntimeError, match="below required API"):
        acquire_device_lease(_transport("env"), device_requirements_from_config(config), manager=manager, env={})


def test_execution_snapshot_is_private_run_evidence_with_exact_lineage(tmp_path: Path) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    environment = _resolved(tmp_path, sdk)
    apk = tmp_path / "app-release.apk"; apk.write_bytes(b"apk")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    receipt = {
        "receipt_id": "gr-1", "plan_digest": "plan-1", "context_digest": "ctx-1", "jdk_identity": "jdk-21",
        "launcher_identity": "launcher-1", "run_ref": "run-1", "rer_ref": "rer-1", "exo_run_ref": "exo-1",
        "artifact_refs": [str(apk)], "producer_artifact_refs": {":app:assembleRelease": [str(apk)]},
    }
    catalog = RunCatalog.open(private_run_root(tmp_path)); catalog.add_run({"run_id": "run-1", "target": "app", "status": "running"})
    pair = build_artifact_pair(
        repo_root=tmp_path, module="app", variant_identity="lane", environment=environment,
        app={"type": "apk", "path": str(apk), "sha256": digest, "producer_task": ":app:assembleRelease"},
        gradle_receipt=receipt, run_catalog=catalog, run_id="run-1", source_identity="src", config_identity="cfg",
    )
    catalog.save()
    snapshot = build_android_execution_snapshot(
        target="app", action="release", environment=environment, gradle_receipts=[receipt], artifact_pair=pair,
        source_identity="src", config_identity="cfg",
    )
    path, attachment = persist_android_execution_snapshot(tmp_path, "run-1", snapshot)
    assert path.is_file() and str(path).startswith(str(private_run_root(tmp_path)))
    assert snapshot.gradle_receipt_ids == ("gr-1",) and snapshot.rer_refs == ("rer-1",) and snapshot.exo_refs == ("exo-1",)
    assert RunCatalog.open(private_run_root(tmp_path)).attachments[attachment].sha256


def test_destination_environment_consumes_returned_snapshot_without_controller_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANDROID_SDK_ROOT", "/controller/should-not-be-used")
    result = consume_destination_android_environment(
        {"destination_id": "ssh:tablet", "android_environment": {"environment_id": "remote-env", "component_identities": {"android.platform": "p"}, "sdk": {"root": "/remote/sdk"}}},
        destination_id="ssh:tablet",
    )
    assert result.environment_id == "remote-env" and result.environment["sdk"]["root"] == "/remote/sdk"
    assert "controller" not in result.returned_snapshot_id


def test_root_loopback_adbd_mutation_is_host_owned_and_restorable() -> None:
    transport = AndroidTransport("t", "local", "sdk", "/adb", "1", "termux", "arm64", "env", mode="root-loopback")
    calls: list[tuple[str, int]] = []
    lease, acquire = acquire_adbd_mutation_lease(
        transport, serial="ABC", requested_port=5555,
        mutator=lambda serial, port: calls.append((serial, port)) or {"previous_port": 0, "observed_port": 5555, "ok": True, "provider_receipt": {"authority": "host"}},
    )
    restored = restore_adbd_mutation_lease(
        lease, restorer=lambda serial, port: calls.append((serial, port)) or {"observed_port": port, "ok": True, "provider_receipt": {"authority": "host"}},
    )
    assert acquire.ok and restored.ok and calls == [("ABC", 5555), ("ABC", 0)]
    assert lease.provider_receipt["authority"] == "host"


def test_bootstrap_capability_freshness_detects_stale_runtime(tmp_path: Path) -> None:
    gradle = tmp_path / "gradle"; gradle.write_text("v1", encoding="utf-8")
    report = {"gradle": str(gradle)}
    stored = bootstrap_capability_freshness(report)
    receipt = {"resolved": report, "capability_freshness": stored}
    assert _local_freshness_matches(receipt) == (True, "fresh")
    gradle.write_text("v2-changed", encoding="utf-8")
    ok, detail = _local_freshness_matches(receipt)
    assert not ok and "gradle" in detail


def test_inspect_repair_requalify_has_structured_receipt_and_no_cleanup_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path, missing="aidl")
    config = _config(tmp_path, sdk)
    # Make the provider set hermetic. Native Termux intentionally prefers
    # $PREFIX/bin helpers before SDK Build Tools, so a real device that already
    # has the `aidl` package installed would otherwise be qualified and would
    # correctly skip the repair callback. A synthetic empty Termux prefix makes
    # this test exercise the missing-capability repair path on every host.
    prefix = tmp_path / "termux-prefix"
    (prefix / "bin").mkdir(parents=True)
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix)
    monkeypatch.setattr(android_environment_module, "detect_runtime_identity", lambda _env=None: runtime)

    repaired: list[str] = []
    def repairer(request, qualification, view):
        path = sdk / "build-tools" / "36.0.0" / "aidl"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8"); path.chmod(0o755)
        repaired.append("aidl")
        return {"authority": "bootstrap", "installed": ["aidl"], "cleanup_owned": False}
    result = inspect_qualify_repair_android_environment(
        config, action="build", env={"PATH": ""}, repairer=repairer,
    )
    assert result.repaired and result.final_qualification.ok and result.resolved is not None
    assert repaired == ["aidl"] and result.repair_receipt["cleanup_owned"] is False


def test_execution_context_prefers_qualified_termux_helper_over_sdk_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import devtool.android.capabilities as capabilities_module
    from devtool.android.capabilities import resolve_android_execution_context

    _project(tmp_path)
    sdk = _sdk(tmp_path)
    prefix = tmp_path / "termux-prefix"
    helper = prefix / "bin" / "apksigner"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nprintf '1.2.3\\n'\n", encoding="utf-8")
    helper.chmod(0o755)
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix)
    monkeypatch.setattr(capabilities_module, "detect_runtime_identity", lambda _env=None: runtime)

    context = resolve_android_execution_context(
        _config(tmp_path, sdk, adb={"provider": "none"}),
        env={"PATH": "", "ANDROID_SDK_ROOT": str(sdk)},
    )
    assert context.tool("build-tools").provider == "sdk"
    assert context.tool("apksigner").provider == "termux"
    assert context.tool("apksigner").path == str(helper)


def test_inspect_repair_requalify_respects_existing_termux_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    sdk = _sdk(tmp_path, missing="aidl")
    config = _config(tmp_path, sdk)
    prefix = tmp_path / "termux-prefix"
    helper = prefix / "bin" / "aidl"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o755)
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix)
    monkeypatch.setattr(android_environment_module, "detect_runtime_identity", lambda _env=None: runtime)

    calls: list[str] = []
    result = inspect_qualify_repair_android_environment(
        config, action="build", env={"PATH": ""},
        repairer=lambda *_args: calls.append("repair") or {"cleanup_owned": False},
    )
    assert result.first_qualification.ok and result.final_qualification.ok
    assert not result.repaired and calls == []
    assert result.resolved is not None
    assert result.resolved.helpers["aidl"].provider == "termux"


def test_bootstrap_destination_snapshot_is_path_independent_but_keeps_provenance(tmp_path: Path) -> None:
    from devtool.bootstrap import BootstrapDestination, android_environment_snapshot_from_bootstrap
    destination = BootstrapDestination("ssh:tablet", "ssh", name="tablet", expected_arch="arm64")
    base = {
        "architecture": "arm64", "platform": "android-36", "build_tools": "36.0.0", "ndk": "27.0.0", "cmake": "3.22.1",
        "gradle_version": "9.7.1", "gradle_provider": "managed", "java_version": 21, "java_provider": "system",
        "native_provider": "native", "sdk_provider": "managed", "network_evidence": {"bootstrap_network": "deny"},
    }
    one = android_environment_snapshot_from_bootstrap(destination, base | {"sdk_root": "/one/sdk", "java_home": "/one/jdk", "gradle": "/one/gradle", "build_tool_paths": {"aapt2": "/one/aapt2", "aidl": "/one/aidl", "zipalign": "/one/zipalign", "split-select": "/one/split-select"}})
    two = android_environment_snapshot_from_bootstrap(destination, base | {"sdk_root": "/two/sdk", "java_home": "/two/jdk", "gradle": "/two/gradle", "build_tool_paths": {"aapt2": "/two/aapt2", "aidl": "/two/aidl", "zipalign": "/two/zipalign", "split-select": "/two/split-select"}})
    assert one["environment_id"] == two["environment_id"]
    assert one["component_identities"] == two["component_identities"]
    assert one["path_provenance"] != two["path_provenance"]
    assert one["network_evidence"]["bootstrap_network"] == "deny"


def test_staged_android_runtime_mechanical_seal_matches_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "devtool/android/_standalone.py").read_bytes() == (root / "devtool/resources/oci/android-arm64/build-apk").read_bytes()


def test_production_release_projects_artifact_pair_and_execution_snapshot_into_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.release import AndroidReleaseOptions, AndroidReleaseOrchestrator
    from devtool.models import CommandSpec, OperationResult
    _project(tmp_path)
    sdk = _sdk(tmp_path)
    config = _config(tmp_path, sdk)
    config.variant = "release"
    apk = tmp_path / "app-release.apk"; apk.write_bytes(b"apk")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    from devtool.gradle_planning import GradleExecutionReceipt
    gradle_plan = {
        "plan_digest": "plan-release",
        "context": {"context_digest": "ctx-release"},
        "policy": {"policy_digest": "policy-release"},
        "request": {"selection_identity": "selection-release", "expected_artifact_producers": [":app:assembleRelease"]},
        "requested_roots": [":app:assembleRelease"],
        "optimized_roots": [":app:assembleRelease"],
    }
    spec = CommandSpec("package", ["fake", "package"], metadata={"gradle_execution_plan": gradle_plan})
    options = AndroidReleaseOptions(
        variant="release", module="app", unit_tests=False, lint=False, device_tests="disabled",
        install_smoke=False, upgrade_smoke=False, cold_start_smoke=False, restart_smoke=False,
        restore_restart_smoke=False, process_death_smoke=False, build_artifacts=True,
        verify_signature=False, verify_certificate_identity=False,
    )
    orchestrator = AndroidReleaseOrchestrator(config, env={"PATH": os.environ.get("PATH", "")})
    monkeypatch.setattr(orchestrator, "plan", lambda **kwargs: (config, options, [("package", [spec])]))
    receipt = GradleExecutionReceipt.from_plan_metadata(
        gradle_plan, progress={"observed_tasks": [":app:assembleRelease"],
                               "task_outcomes": {":app:assembleRelease": "SUCCESS"}},
        success=True, process_executed=True, artifact_refs=[str(apk)],
        producer_artifact_refs={":app:assembleRelease": [str(apk)]},
        run_ref="run-release",
    ).as_dict()
    def fake_run_specs(*args, **kwargs):
        return [OperationResult(
            "package", ["fake", "package"], 0, 0.01, "ok", [], metadata={
                "artifacts": {"items": [{
                    "valid": True, "type": "apk", "variant": "release", "path": str(apk),
                    "sha256": digest, "producer_task": ":app:assembleRelease",
                }]},
                "gradle_execution_receipt": receipt,
            }
        )], ""
    monkeypatch.setattr(orchestrator, "_run_specs", fake_run_specs)
    outcome = orchestrator.run()
    assert outcome.passed
    assert outcome.evidence["execution"]["artifact_pair"]["app"]["sha256"] == digest
    snap_ref = outcome.evidence["execution"]["android_execution_snapshot"]
    assert snap_ref["snapshot_id"] and snap_ref["run_attachment_id"]
    assert Path(snap_ref["path"]).is_file()
