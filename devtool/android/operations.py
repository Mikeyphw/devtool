"""AOPS02 host-side Android operational context facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..models import ProjectConfig
from .capabilities import AndroidExecutionContext, resolve_android_execution_context
from .devices import AndroidDeviceManager


@dataclass(slots=True)
class AndroidOperations:
    context: AndroidExecutionContext
    devices: AndroidDeviceManager


def resolve_android_operations(
    config: ProjectConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
    adb_provider: str | None = None,
    adb_path: str | None = None,
) -> AndroidOperations:
    context = resolve_android_execution_context(config, env=env, adb_provider=adb_provider, adb_path=adb_path)
    return AndroidOperations(context=context, devices=AndroidDeviceManager(context.adb, env=env))


__all__ = ["AndroidOperations", "resolve_android_operations"]
