"""CLI helpers for Android execution context and device diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from ..models import ProjectConfig
from .operations import resolve_android_operations



def android_environment_payload(config: ProjectConfig, *, action: str = "build") -> dict[str, Any]:
    """Explain the canonical Android environment authority without device side effects."""
    from .environment import resolve_android_environment

    request, qualification, resolved = resolve_android_environment(
        config, action=action, device_required=False, fail_closed=False,
    )
    return {
        "schema_version": 1,
        "action": action,
        "request": request.as_dict(),
        "qualification": qualification.as_dict(),
        "environment": resolved.as_dict() if resolved is not None else None,
        "environment_id": resolved.environment_id if resolved is not None else "",
        "component_identities": dict(resolved.component_identities) if resolved is not None else {},
        "ok": qualification.ok and resolved is not None,
    }



def android_environment_diff_payload(
    config: ProjectConfig, *, left_action: str = "build", right_action: str = "instrumentation"
) -> dict[str, Any]:
    """Compare canonical Android component identities without reinterpreting them."""
    left = android_environment_payload(config, action=left_action)
    right = android_environment_payload(config, action=right_action)
    left_components = dict(left.get("component_identities") or {})
    right_components = dict(right.get("component_identities") or {})
    keys = sorted(set(left_components) | set(right_components))
    components = [
        {
            "component": key,
            "left": left_components.get(key, ""),
            "right": right_components.get(key, ""),
            "changed": left_components.get(key, "") != right_components.get(key, ""),
        }
        for key in keys
    ]
    return {
        "schema_version": 1,
        "left_action": left_action,
        "right_action": right_action,
        "left_environment_id": left.get("environment_id") or "",
        "right_environment_id": right.get("environment_id") or "",
        "changed": any(item["changed"] for item in components),
        "components": components,
        "left_qualification_id": (left.get("qualification") or {}).get("qualification_id", ""),
        "right_qualification_id": (right.get("qualification") or {}).get("qualification_id", ""),
    }


def android_qualification_payload(config: ProjectConfig, *, action: str = "build") -> dict[str, Any]:
    """Expose production qualification/freshness truth as a read-only projection."""
    payload = android_environment_payload(config, action=action)
    qualification = dict(payload.get("qualification") or {})
    return {
        "schema_version": 1,
        "action": action,
        "ok": bool(payload.get("ok")),
        "environment_id": payload.get("environment_id") or "",
        "qualification": qualification,
        "component_identities": dict(payload.get("component_identities") or {}),
        "repair_actions": list(qualification.get("repair_actions") or []),
        "diagnostics": list(qualification.get("diagnostics") or []),
    }


def android_action_doctor_payload(
    config: ProjectConfig, *, action: str = "build", provider: str = "", adb_path: str = ""
) -> dict[str, Any]:
    """Action-aware doctor composed from environment + Host-owned device doctor."""
    from .execution_snapshot import android_action_requires_device

    environment = android_environment_payload(config, action=action)
    needs_device = android_action_requires_device(action)
    device = None
    if needs_device:
        device = android_device_doctor_payload(config, provider=provider, adb_path=adb_path)
    return {
        "schema_version": 1,
        "action": action,
        "ok": bool(environment.get("ok")) and (not needs_device or bool((device or {}).get("device_doctor", {}).get("healthy"))),
        "device_required": needs_device,
        "environment": environment,
        "device": device,
        "authorities": {"environment": "ANDROID", "device": "HOST/DeviceLease" if needs_device else "not-required"},
    }


def android_advanced_instrumentation_plan_payload(
    config: ProjectConfig, *, classes: list[str] | None = None, methods: list[str] | None = None,
    annotations: list[str] | None = None, not_annotations: list[str] | None = None, size: str = "",
    runner_args: Mapping[str, str] | None = None, shard_count: int = 1, retry_failed_attempts: int = 1,
    quarantine: list[str] | None = None, orchestrator_qualification_id: str = "",
    orchestrator_target: str = "", ui_automator: bool = False, cross_app: bool = False,
) -> dict[str, Any]:
    """Read-only X04 semantic policy preview. DeviceLease binding remains execution-owned."""
    from .advanced_execution import (
        AndroidTestOrchestratorMode, ExoRetryPolicy, QuarantinePolicy, android_junit_shards,
        execution_profile, legacy_instrumentation_migration_diagnostics,
    )
    from .instrumentation import instrumentation_intent, request_from_config

    request = request_from_config(
        config, classes=classes or [], methods=methods or [], annotations=annotations or [],
        not_annotations=not_annotations or [], size=size, runner_args=runner_args, skip_build=True,
    )
    intent = instrumentation_intent(request)
    orchestrator = AndroidTestOrchestratorMode(
        enabled=bool(orchestrator_qualification_id),
        qualification_id=orchestrator_qualification_id,
        target_instrumentation=orchestrator_target if orchestrator_qualification_id else "",
    )
    retry = ExoRetryPolicy(
        mode="failed-only" if int(retry_failed_attempts) > 1 else "disabled",
        max_attempts=max(1, int(retry_failed_attempts)),
    )
    qids = tuple(quarantine or ())
    quarantine_policy = QuarantinePolicy(qids, mode="evidence-only" if qids else "disabled")
    profile = execution_profile(ui_automator=ui_automator, cross_app=cross_app)
    android_options = config.runner_options.get("android", {})
    android_options = dict(android_options) if isinstance(android_options, dict) else {}
    return {
        "schema_version": 1,
        "kind": "android-x04-instrumentation-policy-preview",
        "intent": intent.as_dict(),
        "shards": [item.as_dict() for item in android_junit_shards(intent, max(1, int(shard_count)))],
        "orchestrator": orchestrator.as_dict(),
        "retry": retry.as_dict(),
        "quarantine": quarantine_policy.as_dict(),
        "profile": profile.as_dict(),
        "device_binding": "pending-DeviceLease",
        "migration_diagnostics": list(legacy_instrumentation_migration_diagnostics(android_options)),
        "authorities": {
            "intent": "ANDROID-X03", "device_binding": "ANDROID-X02/HOST", "retry": "EXO",
            "shard_scheduling": "TEST-X03", "evidence": "RUN-V3",
        },
    }

def android_capabilities_payload(config: ProjectConfig, *, provider: str = "", adb_path: str = "") -> dict[str, Any]:
    operations = resolve_android_operations(config, adb_provider=provider or None, adb_path=adb_path or None)
    return operations.context.as_dict()


def android_devices_payload(
    config: ProjectConfig, *, provider: str = "", adb_path: str = "", enrich: bool = False,
    policy: str = "", serial: str = "", preferred: str = "", include_unusable: bool = False,
) -> dict[str, Any]:
    operations = resolve_android_operations(config, adb_provider=provider or None, adb_path=adb_path or None)
    selected: list[dict[str, Any]] = []
    selection_error = ""
    try:
        devices = operations.devices.list_devices(enrich=enrich)
        error = ""
        if policy or serial or preferred:
            android = config.runner_options.get("android", {}) if isinstance(config.runner_options.get("android", {}), dict) else {}
            adb_cfg = android.get("adb", {}) if isinstance(android.get("adb", {}), dict) else {}
            effective_policy = policy or str(adb_cfg.get("device_policy") or ("serial" if serial else "exactly-one"))
            effective_serial = serial or str(adb_cfg.get("serial") or "")
            effective_preferred = preferred or str(adb_cfg.get("preferred") or "")
            try:
                picked = operations.devices.select(effective_policy, serial=effective_serial, preferred=effective_preferred, include_unusable=include_unusable)
                selected = [item.as_dict() for item in picked]
            except Exception as exc:
                selection_error = str(exc)
    except Exception as exc:  # CLI boundary turns transport failures into structured output.
        devices = []
        error = str(exc)
    return {
        "schema_version": 1,
        "adb": operations.context.adb.as_dict(),
        "device_transport": operations.context.as_dict().get("device_transport", "none"),
        "devices": [item.as_dict() for item in devices],
        "selection": selected,
        "selection_error": selection_error,
        "error": error,
    }


def android_device_doctor_payload(config: ProjectConfig, *, provider: str = "", adb_path: str = "") -> dict[str, Any]:
    operations = resolve_android_operations(config, adb_provider=provider or None, adb_path=adb_path or None)
    report = operations.devices.doctor()
    return {
        "schema_version": 1,
        "context": operations.context.as_dict(),
        "device_doctor": report.as_dict(),
    }



def android_signing_payload(
    config: ProjectConfig,
    *,
    action: str,
    keystore: str = "",
    alias: str = "",
    artifact: str = "",
    other_artifact: str = "",
    destination: str = "",
    source: str = "",
    store_password_env: str = "",
    key_password_env: str = "",
    source_password_env: str = "",
    destination_password_env: str = "",
    overwrite: bool = False,
    distinguished_name: str = "CN=Devtool Android Release",
) -> dict[str, Any]:
    from dataclasses import replace
    import os
    from .signing import AndroidSigningManager, SigningProfile, signing_profile

    operations = resolve_android_operations(config)
    manager = AndroidSigningManager(operations.context)
    profile = signing_profile(config)
    if keystore:
        profile = replace(profile, keystore=keystore)
    if alias:
        profile = replace(profile, alias=alias)
    if store_password_env:
        profile = replace(profile, store_password_env=store_password_env)
    if key_password_env:
        profile = replace(profile, key_password_env=key_password_env)

    if action == "list":
        identities = manager.list_aliases(profile)
        return {"schema_version": 1, "action": action, "profile": profile.as_dict(), "identities": [item.as_dict() for item in identities]}
    if action == "inspect":
        identity = manager.inspect_profile(profile)
        return {"schema_version": 1, "action": action, "profile": profile.as_dict(), "identity": identity.as_dict()}
    if action == "verify":
        if artifact:
            verification = manager.verify_artifact(artifact, expected_sha256=profile.expected_cert_sha256)
            return {"schema_version": 1, "action": action, "profile": profile.as_dict(), "verification": verification.as_dict()}
        identity = manager.inspect_profile(profile)
        ok = identity.expired is not True and (not profile.expected_cert_sha256 or identity.sha256.lower() == profile.expected_cert_sha256.lower())
        return {"schema_version": 1, "action": action, "ok": ok, "profile": profile.as_dict(), "identity": identity.as_dict()}
    if action == "compare":
        if not artifact or not other_artifact:
            raise ValueError("signing compare requires two artifacts")
        return {"schema_version": 1, "action": action, **manager.compare_artifacts(artifact, other_artifact)}
    if action == "create":
        if not profile.keystore or not profile.alias:
            raise ValueError("signing create requires a keystore path and alias (via config or CLI)")
        if not profile.store_password_env:
            raise ValueError("signing create requires --store-password-env or android.signing.release.store_password_env")
        store = os.environ.get(profile.store_password_env, "")
        if not store:
            raise ValueError(f"Signing password environment variable {profile.store_password_env} is not set")
        key = os.environ.get(profile.key_password_env, "") if profile.key_password_env else store
        identity = manager.create(profile.keystore, alias=profile.alias, store_password=store, key_password=key, distinguished_name=distinguished_name, store_type=profile.store_type, overwrite=overwrite)
        return {"schema_version": 1, "action": action, "profile": profile.as_dict(), "identity": identity.as_dict()}
    if action == "import":
        if not source or not destination:
            raise ValueError("signing import requires source and destination keystores")
        if not source_password_env or not destination_password_env:
            raise ValueError("signing import requires --source-password-env and --destination-password-env")
        src_pw = os.environ.get(source_password_env, "")
        dst_pw = os.environ.get(destination_password_env, "")
        if not src_pw or not dst_pw:
            raise ValueError("Configured signing import password environment variables must be set")
        identities = manager.import_keystore(source, destination, source_password=src_pw, destination_password=dst_pw, alias=alias, overwrite=overwrite)
        return {"schema_version": 1, "action": action, "identities": [item.as_dict() for item in identities], "destination": destination}
    if action == "export-certificate":
        if not destination:
            raise ValueError("export-certificate requires --destination")
        path = manager.export_certificate(profile, destination)
        return {"schema_version": 1, "action": action, "destination": str(path), "profile": profile.as_dict()}
    raise ValueError(f"Unknown Android signing action: {action}")


def android_device_operation_payload(
    config: ProjectConfig,
    *,
    action: str,
    serial: str = "",
    policy: str = "",
    apk: str = "",
    package: str = "",
    activity: str = "",
    source: str = "",
    destination: str = "",
    permission: str = "",
    shell_args: list[str] | None = None,
    replace: bool = True,
    downgrade: bool = False,
    keep_data: bool = False,
    lines: int = 400,
) -> dict[str, Any]:
    from .device_ops import AndroidDeviceOperations
    ops = AndroidDeviceOperations.from_config(config)
    kwargs = {"serial": serial, "policy": policy}
    if action == "install":
        result = ops.install(apk, replace=replace, downgrade=downgrade, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "uninstall":
        result = ops.uninstall(package, keep_data=keep_data, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "launch":
        result = ops.launch(package, activity=activity, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "stop":
        result = ops.force_stop(package, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "restart":
        results = ops.restart(package, activity=activity, **kwargs)
        return {"schema_version": 1, "results": [item.as_dict() for item in results], "ok": all(item.ok for item in results)}
    if action == "clear-data":
        result = ops.clear_data(package, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "shell":
        result = ops.shell(shell_args or [], **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "push":
        result = ops.push(source, destination, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "pull":
        result = ops.pull(source, destination, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "logcat":
        result = ops.logcat(lines=lines, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "screenshot":
        result = ops.screenshot(destination, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "grant":
        result = ops.grant(package, permission, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    if action == "revoke":
        result = ops.revoke(package, permission, **kwargs)
        return {"schema_version": 1, "result": result.as_dict()}
    raise ValueError(f"Unknown Android device operation: {action}")


def android_smoke_payload(
    config: ProjectConfig,
    *,
    gate: str,
    apk: str = "",
    old_apk: str = "",
    package: str = "",
    activity: str = "",
    serial: str = "",
    policy: str = "",
    expected_cert_sha256: str = "",
) -> dict[str, Any]:
    from .device_ops import AndroidDeviceOperations
    ops = AndroidDeviceOperations.from_config(config)
    if gate == "install":
        result = ops.install_smoke(apk, package=package, activity=activity, serial=serial, policy=policy)
    elif gate == "upgrade":
        result = ops.upgrade_smoke(old_apk, apk, package=package, activity=activity, serial=serial, policy=policy)
    elif gate == "cold-start":
        result = ops.cold_start_smoke(package, activity=activity, serial=serial, policy=policy)
    elif gate == "restart":
        result = ops.restart_smoke(package, activity=activity, serial=serial, policy=policy)
    elif gate == "process-death":
        result = ops.process_death_smoke(package, activity=activity, serial=serial, policy=policy)
    elif gate == "restore-restart":
        result = ops.restore_restart_smoke(package, activity=activity, serial=serial, policy=policy)
    elif gate == "signed-artifact":
        result = ops.signed_artifact_smoke(apk, expected_sha256=expected_cert_sha256)
    else:
        raise ValueError(f"Unknown Android smoke gate: {gate}")
    return {"schema_version": 1, "result": result.as_dict()}




def _build_instrumentation_artifacts(config: ProjectConfig) -> list[dict[str, Any]]:
    """Build instrumentation producer artifacts once through canonical Gradle."""
    from ..project_runners import AndroidGradleRunner
    from ..runners import run_specs

    gradle_receipts: list[dict[str, Any]] = []
    planned = AndroidGradleRunner().build_specs(config, "instrumentation")
    build_specs = [spec for spec in planned if spec.metadata.get("gradle_execution_plan")]
    results, _ = run_specs(config, build_specs, verbose=False)
    for item in results:
        receipt = item.metadata.get("gradle_execution_receipt") if isinstance(item.metadata, dict) else None
        if isinstance(receipt, dict):
            gradle_receipts.append(dict(receipt))
    failed = [result for result in results if int(result.returncode or 0) != 0]
    if failed:
        first = failed[0]
        raise RuntimeError(
            f"Canonical instrumentation Gradle build failed in {first.name} with exit {first.returncode}"
        )
    return gradle_receipts


def _advanced_device_capacities(config: ProjectConfig, *, attempt_id: str):
    """Bind X04 execution to real X02 DeviceLease objects.

    ``device_policy=all`` is the explicit multi-device opt-in.  Every other
    policy resolves exactly one canonical lease, avoiding accidental fan-out
    merely because multiple ADB devices happen to be attached.
    """
    from .advanced_execution import DeviceCapacity
    from .environment import EnvironmentView, resolve_android_environment
    from .execution_snapshot import acquire_device_lease, device_requirements_from_config, resolve_android_transport

    view = EnvironmentView.from_config(config)
    _request, _qualification, environment = resolve_android_environment(
        config, action="instrumentation", env=view.values, device_required=True, fail_closed=True,
    )
    if environment is None:
        raise RuntimeError("Canonical Android instrumentation environment did not resolve")
    transport = resolve_android_transport(
        config, environment, action="instrumentation", env_view=view, device_required=True,
    )
    if transport is None:
        raise RuntimeError("Advanced Android instrumentation requires a device transport")
    operations = resolve_android_operations(config, env=view.values)
    android = config.runner_options.get("android", {})
    android = dict(android) if isinstance(android, Mapping) else {}
    adb = android.get("adb", {})
    adb = dict(adb) if isinstance(adb, Mapping) else {}
    policy = str(adb.get("device_policy") or "exactly-one").strip().lower()

    leases = []
    if policy == "all":
        devices = [item for item in operations.devices.list_devices(enrich=True) if str(item.state).lower() == "device"]
        if not devices:
            raise RuntimeError("Advanced Android device matrix requested but no usable devices are connected")
        for index, device in enumerate(devices):
            requirements = device_requirements_from_config(
                config, overrides={"device_policy": "serial", "serial": device.serial},
            )
            leases.append(acquire_device_lease(
                transport, requirements, env=view.values,
                attempt_id=f"{attempt_id}:device:{index}:{device.serial}", manager=operations.devices,
            ))
    else:
        requirements = device_requirements_from_config(config)
        leases.append(acquire_device_lease(
            transport, requirements, env=view.values, attempt_id=attempt_id, manager=operations.devices,
        ))
    return tuple(DeviceCapacity(lease, max_concurrency=1) for lease in leases)


def _advanced_worker_result(output: str) -> dict[str, Any]:
    marker = "DEVTOOL_ANDROID_ADVANCED_RESULT "
    rows = [line[len(marker):] for line in str(output or "").splitlines() if line.startswith(marker)]
    if not rows:
        return {}
    try:
        value = json.loads(rows[-1])
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _persist_advanced_matrix_evidence(
    config: ProjectConfig, *, parent_run_id: str, plan_id: str, payload: Mapping[str, Any],
) -> str:
    from ..core_runtime import private_run_root
    from ..run_store import AttachmentDescriptor, RunCatalog

    root = private_run_root(config.repo_root)
    path = root / "evidence" / parent_run_id / "android-advanced" / f"{plan_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    catalog = RunCatalog.open(root)
    row = dict(catalog.runs.get(parent_run_id, {"run_id": parent_run_id}))
    row.setdefault("command_id", "android.instrumentation")
    row["android_advanced_plan_id"] = plan_id
    row["android_advanced_evidence_path"] = str(path)
    catalog.add_run(row)
    ref = catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=parent_run_id, path=path, kind="android-advanced-matrix-evidence", privacy="private",
        media_type="application/json",
    ))
    catalog.save()
    return ref.ref_id


def android_advanced_instrumentation_payload(
    config: ProjectConfig, *, classes: list[str] | None = None, methods: list[str] | None = None,
    annotations: list[str] | None = None, not_annotations: list[str] | None = None, size: str = "",
    runner_args: Mapping[str, str] | None = None, shard_count: int = 1, retry_failed_attempts: int = 1,
    quarantine: list[str] | None = None, orchestrator_qualification_id: str = "",
    orchestrator_target: str = "", ui_automator: bool = False, cross_app: bool = False,
    skip_build: bool = False,
) -> dict[str, Any]:
    """Execute the X04 plan as real EXO/RUN child processes.

    The normal CLI owns planning and device binding.  Typed shard/orchestrator
    capabilities cross the process boundary only through a parent-bound private
    handoff; the worker cannot be configured into a competing public authority.
    """
    from ..core.execution_contract import RunId
    from ..core_runtime import private_run_root
    from ..invocation_origin import create_managed_run_handoff
    from ..models import CommandSpec
    from ..run_store import RunCatalog, RunRelation
    from ..runners import run_specs
    from .advanced_execution import (
        AndroidTestOrchestratorMode, ExoRetryPolicy, QuarantinePolicy,
        build_advanced_instrumentation_plan, create_advanced_worker_handoff, execution_profile,
        legacy_instrumentation_migration_diagnostics, matrix_evidence,
    )
    from .instrumentation import instrumentation_intent, request_from_config

    parent_run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip() or str(RunId.new(prefix="android-parent"))
    gradle_receipts = [] if skip_build else _build_instrumentation_artifacts(config)
    base_request = request_from_config(
        config, classes=classes or [], methods=methods or [], annotations=annotations or [],
        not_annotations=not_annotations or [], size=size, runner_args=runner_args, skip_build=True,
    )
    intent = instrumentation_intent(base_request)
    capacities = _advanced_device_capacities(config, attempt_id=parent_run_id)
    orchestrator = AndroidTestOrchestratorMode(
        enabled=bool(orchestrator_qualification_id), qualification_id=orchestrator_qualification_id,
        target_instrumentation=orchestrator_target if orchestrator_qualification_id else "",
    )
    retry = ExoRetryPolicy(
        mode="failed-only" if int(retry_failed_attempts) > 1 else "disabled",
        max_attempts=max(1, int(retry_failed_attempts)),
    )
    qids = tuple(quarantine or ())
    quarantine_policy = QuarantinePolicy(qids, mode="evidence-only" if qids else "disabled")
    profile = execution_profile(ui_automator=ui_automator, cross_app=cross_app)
    android_options = config.runner_options.get("android", {})
    android_options = dict(android_options) if isinstance(android_options, Mapping) else {}
    # One worker keeps RUN catalog writes serialized. Device identity still
    # rotates through the real lease matrix, and TEST-X03 owns wave scheduling.
    plan = build_advanced_instrumentation_plan(
        intent, device_capacities=capacities, shard_count=max(1, int(shard_count)), workers=1,
        orchestrator=orchestrator, retry=retry, quarantine=quarantine_policy, profile=profile,
        migration_diagnostics=legacy_instrumentation_migration_diagnostics(android_options),
    )

    catalog_root = private_run_root(config.repo_root)
    catalog = RunCatalog.open(catalog_root)
    parent_row = dict(catalog.runs.get(parent_run_id, {"run_id": parent_run_id}))
    parent_row.setdefault("command_id", "android.instrumentation")
    parent_row.setdefault("status", "running")
    parent_row["android_advanced_plan_id"] = plan.plan_id
    catalog.add_run(parent_row)
    catalog.save()

    child_by_id = {child.child_id: child for child in plan.children}
    final_children: list[dict[str, Any]] = []
    any_failed = False
    for wave_index, wave in enumerate(plan.execution_waves):
        for child_id in wave:
            child = child_by_id[child_id]
            attempts: list[dict[str, Any]] = []
            final: dict[str, Any] = {}
            for attempt_index in range(1, retry.max_attempts + 1):
                child_run_id = str(RunId.new(prefix="android-child"))
                relation = RunRelation(
                    relation_id="", parent_run_id=parent_run_id, child_run_id=child_run_id,
                    parent_node_id=child.child_id, relationship="android-instrumentation-attempt",
                    delegation_id=f"{plan.plan_id}:{child.child_id}:{attempt_index}",
                    provider_identity={
                        "provider_kind": "android-device", "serial": child.serial,
                        "device_lease_id": child.device_lease_id, "profile_id": child.profile_id,
                    },
                    delivery_health="starting",
                )
                catalog = RunCatalog.open(catalog_root)
                catalog.add_relation(relation)
                catalog.save()

                handoff_payload = {
                    "child_run_id": child_run_id, "child_id": child.child_id,
                    "parent_plan_id": plan.plan_id, "repo_root": str(config.repo_root),
                    "config_path": str(config.config_path or ""), "target": str(config.selected_target or ""),
                    "variant": str(config.variant or ""), "serial": child.serial,
                    "typed_runner_args": dict(child.runner_args),
                    "runner_options": {
                        key: dict(value) for key, value in config.runner_options.items()
                        if key in {"android", "gradle"} and isinstance(value, Mapping)
                    },
                    "request": {
                        "classes": list(base_request.classes), "methods": list(base_request.methods),
                        "annotations": list(base_request.annotations), "not_annotations": list(base_request.not_annotations),
                        "size": base_request.size, "runner_args": dict(base_request.runner_args),
                    },
                    "gradle_receipts": list(gradle_receipts),
                }
                handoff = create_advanced_worker_handoff(handoff_payload)
                # Also give the internal worker a standard managed-run handoff so
                # any nested canonical CLI/library surface sees the same RUN ID.
                managed = create_managed_run_handoff(child_run_id)
                spec = CommandSpec(
                    name=f"android-instrumentation:{child.child_id}:attempt-{attempt_index}",
                    argv=[sys.executable, "-m", "devtool.android.advanced_worker"],
                    env={
                        "DEVTOOL_ANDROID_ADVANCED_HANDOFF": handoff,
                        "DEVTOOL_MANAGED_RUN_HANDOFF": managed,
                    },
                    cwd=str(config.repo_root),
                    metadata={
                        "android_advanced_plan_id": plan.plan_id, "android_advanced_child_id": child.child_id,
                        "android_device_lease_id": child.device_lease_id, "android_device_serial": child.serial,
                        "android_shard_index": child.shard_index, "android_shard_count": child.shard_count,
                        "execution_provider": "android-device",
                    },
                )
                results, _raw = run_specs(
                    config, [spec], verbose=False, continue_on_error=False, event_run_id=child_run_id,
                    publish_workflow_plan=False,
                )
                op = results[-1] if results else None
                worker = _advanced_worker_result(op.output if op is not None else "")
                result_payload = dict(worker.get("result") or {}) if isinstance(worker.get("result"), Mapping) else {}
                ok = bool(result_payload.get("ok")) and op is not None and int(op.returncode or 0) == 0
                attempt = {
                    "attempt": attempt_index, "child_run_id": child_run_id, "ok": ok,
                    "returncode": int(op.returncode or 1) if op is not None else 1,
                    "worker": worker,
                }
                attempts.append(attempt)
                catalog = RunCatalog.open(catalog_root)
                updated = catalog.update_relation_state(
                    relation, delivery_health="succeeded" if ok else "failed",
                )
                child_row = dict(catalog.runs.get(child_run_id, {"run_id": child_run_id}))
                child_row.update({
                    "status": "succeeded" if ok else "failed",
                    "android_advanced_plan_id": plan.plan_id, "android_advanced_child_id": child.child_id,
                    "android_attempt": attempt_index, "android_device_lease_id": child.device_lease_id,
                })
                catalog.add_run(child_row)
                catalog.save()
                final = {
                    "child_id": child.child_id, "shard_id": child.shard_id, "serial": child.serial,
                    "device_lease_id": child.device_lease_id, "profile_id": child.profile_id,
                    "ok": ok, "receipt_id": str(result_payload.get("receipt_id") or ""),
                    "result": result_payload, "attempts": list(attempts),
                    "final_child_run_id": child_run_id, "relation_id": updated.relation_id,
                    "wave": wave_index,
                }
                if ok or retry.mode != "failed-only":
                    break
            if not final.get("ok"):
                any_failed = True
            final_children.append(final)

    evidence = matrix_evidence(plan, final_children)
    evidence["children"] = final_children
    evidence["process_spawned_children"] = sum(len(item.get("attempts") or []) for item in final_children)
    evidence["all_children_ok"] = not any_failed
    payload: dict[str, Any] = {
        "schema_version": 1, "kind": "android-x04-advanced-execution",
        "ok": bool(evidence.get("complete")) and not any_failed,
        "parent_run_id": parent_run_id, "plan": plan.as_dict(), "evidence": evidence,
        "gradle_receipts": gradle_receipts,
        "authorities": {
            "planning": "ANDROID-X04", "device": "ANDROID-X02/DeviceLease",
            "execution": "EXO/run_specs child process", "retry": "EXO child attempts",
            "scheduling": "TEST-X03", "durability": "RUN-V3",
        },
    }
    attachment_id = _persist_advanced_matrix_evidence(
        config, parent_run_id=parent_run_id, plan_id=plan.plan_id, payload=payload,
    )
    payload["evidence_attachment_id"] = attachment_id
    catalog = RunCatalog.open(catalog_root)
    parent_row = dict(catalog.runs.get(parent_run_id, {"run_id": parent_run_id}))
    parent_row.update({
        "status": "succeeded" if payload["ok"] else "failed",
        "android_advanced_plan_id": plan.plan_id, "android_advanced_evidence_attachment_id": attachment_id,
    })
    catalog.add_run(parent_row)
    catalog.save()
    return payload

def android_instrumentation_payload(
    config: ProjectConfig,
    *,
    classes: list[str] | None = None,
    methods: list[str] | None = None,
    annotations: list[str] | None = None,
    not_annotations: list[str] | None = None,
    size: str = "",
    runner_args: dict[str, str] | None = None,
    skip_build: bool = False,
) -> dict[str, Any]:
    from .instrumentation import AndroidInstrumentationOrchestrator, request_from_config

    # ENV-X03: even the dedicated ``devtool android instrumentation`` CLI
    # enters the canonical Gradle planner for its build half. The orchestrator
    # remains authoritative for device install/run/collection only.
    gradle_receipts: list[dict[str, Any]] = []
    if not skip_build:
        gradle_receipts = _build_instrumentation_artifacts(config)
        skip_build = True

    orchestrator = AndroidInstrumentationOrchestrator.from_config(config, gradle_receipts=gradle_receipts)
    request = request_from_config(
        config, classes=classes or [], methods=methods or [],
        annotations=annotations or [], not_annotations=not_annotations or [],
        size=size, runner_args=runner_args, skip_build=skip_build,
    )
    result = orchestrator.run(request)
    return {"schema_version": 1, "result": result.as_dict()}

def render_android_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


__all__ = ["android_environment_payload", "android_environment_diff_payload", "android_qualification_payload", "android_action_doctor_payload", "android_advanced_instrumentation_plan_payload", "android_capabilities_payload", "android_devices_payload", "android_device_doctor_payload", "android_signing_payload", "android_device_operation_payload", "android_smoke_payload", "android_instrumentation_payload", "android_advanced_instrumentation_payload", "render_android_payload"]
