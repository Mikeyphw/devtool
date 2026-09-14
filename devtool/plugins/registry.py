from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any

from .api import PLUGIN_API_VERSION, PluginCheck, PluginContext, PluginDescriptor
from .builtin import BUILTIN_PLUGINS

ENTRY_POINT_GROUP = "devtool.plugins"


def _descriptor_from_external(descriptor: PluginDescriptor, *, source: str) -> PluginDescriptor:
    name = descriptor.name
    if not isinstance(name, str) or not name.strip():
        raise TypeError("plugin descriptor name must be a non-empty string")
    description = descriptor.description
    if not isinstance(description, str):
        raise TypeError("plugin descriptor description must be a string")
    version = descriptor.version
    if not isinstance(version, str):
        raise TypeError("plugin descriptor version must be a string")
    capabilities = descriptor.capabilities
    if not isinstance(capabilities, tuple) or any(not isinstance(item, str) for item in capabilities):
        raise TypeError("plugin descriptor capabilities must be a tuple of strings")
    if not isinstance(descriptor.api_version, int) or isinstance(descriptor.api_version, bool) or descriptor.api_version != PLUGIN_API_VERSION:
        raise TypeError(f"unsupported plugin API version {descriptor.api_version}; expected {PLUGIN_API_VERSION}")
    return PluginDescriptor(
        name.strip(),
        description,
        version=version,
        api_version=descriptor.api_version,
        source=source,
        capabilities=capabilities,
        doctor=descriptor.doctor,
    )


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
            plugins.append(_descriptor_from_external(descriptor, source=f"entry-point:{point.value}"))
        except Exception as exc:
            errors.append({"plugin": getattr(point, "name", "<unknown>"), "error": str(exc)})
    return plugins, errors


def discover_plugins() -> tuple[list[PluginDescriptor], list[dict[str, str]]]:
    external, errors = _external_descriptors()
    by_name = {item.name: item for item in BUILTIN_PLUGINS}
    builtin_names = set(by_name)
    for item in external:
        if item.name in builtin_names:
            errors.append({"plugin": item.name, "error": "external plugin cannot override built-in plugin"})
            continue
        if item.name in by_name:
            errors.append({"plugin": item.name, "error": "duplicate plugin name"})
            continue
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
    try:
        checks = list(plugin.doctor(context)) if plugin.doctor is not None else []
    except Exception as exc:
        checks = [PluginCheck("doctor", False, f"{type(exc).__name__}: {exc}", required=True)]
    required_failed = [item for item in checks if item.required and not item.ok]
    return {
        "plugin": plugin.to_dict(),
        "ok": not required_failed,
        "checks": [item.to_dict() for item in checks],
    }
