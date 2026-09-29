"""CLI helpers for Android execution context and device diagnostics."""

from __future__ import annotations

import json
from typing import Any

from ..models import ProjectConfig
from .operations import resolve_android_operations


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
    if not skip_build:
        from ..project_runners import AndroidGradleRunner
        from ..runners import run_specs

        planned = AndroidGradleRunner().build_specs(config, "instrumentation")
        build_specs = [spec for spec in planned if spec.metadata.get("gradle_execution_plan")]
        results, _ = run_specs(config, build_specs, verbose=False)
        failed = [result for result in results if int(result.returncode or 0) != 0]
        if failed:
            first = failed[0]
            raise RuntimeError(
                f"Canonical instrumentation Gradle build failed in {first.name} "
                f"with exit {first.returncode}"
            )
        skip_build = True

    orchestrator = AndroidInstrumentationOrchestrator.from_config(config)
    request = request_from_config(
        config, classes=classes or [], methods=methods or [],
        annotations=annotations or [], not_annotations=not_annotations or [],
        size=size, runner_args=runner_args, skip_build=skip_build,
    )
    result = orchestrator.run(request)
    return {"schema_version": 1, "result": result.as_dict()}

def render_android_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


__all__ = ["android_capabilities_payload", "android_devices_payload", "android_device_doctor_payload", "android_signing_payload", "android_device_operation_payload", "android_smoke_payload", "android_instrumentation_payload", "render_android_payload"]
