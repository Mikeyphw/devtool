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


def render_android_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


__all__ = ["android_capabilities_payload", "android_devices_payload", "android_device_doctor_payload", "render_android_payload"]
