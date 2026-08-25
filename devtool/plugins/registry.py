from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any

from .api import PluginContext, PluginDescriptor
from .builtin import BUILTIN_PLUGINS

ENTRY_POINT_GROUP = "devtool.plugins"


def _external_descriptors() -> tuple[list[PluginDescriptor], list[dict[str, str]]]:
    plugins: list[PluginDescriptor] = []
    errors: list[dict[str, str]] = []
    try:
        points = metadata.entry_points()
        selected = points.select(group=ENTRY_POINT_GROUP) if hasattr(points, "select") else points.get(ENTRY_POINT_GROUP, [])
    except Exception as exc:
        return [], [{"plugin": "<entry-points>", "error": str(exc)}]
    for point in selected:
        try:
            loaded = point.load()
            instance = loaded() if isinstance(loaded, type) else loaded
            descriptor = instance.descriptor() if hasattr(instance, "descriptor") else instance
            if not isinstance(descriptor, PluginDescriptor):
                raise TypeError("entry point must expose PluginDescriptor or descriptor()")
            plugins.append(PluginDescriptor(
                descriptor.name,
                descriptor.description,
                version=descriptor.version,
                source=f"entry-point:{point.value}",
                capabilities=descriptor.capabilities,
                doctor=descriptor.doctor,
            ))
        except Exception as exc:
            errors.append({"plugin": getattr(point, "name", "<unknown>"), "error": str(exc)})
    return plugins, errors


def discover_plugins() -> tuple[list[PluginDescriptor], list[dict[str, str]]]:
    external, errors = _external_descriptors()
    by_name = {item.name: item for item in BUILTIN_PLUGINS}
    for item in external:
        by_name[item.name] = item
    return [by_name[name] for name in sorted(by_name)], errors


def find_plugin(name: str) -> PluginDescriptor | None:
    plugins, _errors = discover_plugins()
    wanted = str(name).strip().casefold()
    return next((item for item in plugins if item.name.casefold() == wanted), None)


def doctor_plugin(name: str, *, repo_root: Path, project_type: str = "auto") -> dict[str, Any]:
    plugin = find_plugin(name)
    if plugin is None:
        raise KeyError(f"Unknown devtool plugin: {name}")
    context = PluginContext(repo_root.resolve(), project_type=project_type)
    checks = list(plugin.doctor(context)) if plugin.doctor is not None else []
    required_failed = [item for item in checks if item.required and not item.ok]
    return {
        "plugin": plugin.to_dict(),
        "ok": not required_failed,
        "checks": [item.to_dict() for item in checks],
    }
