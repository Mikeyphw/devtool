from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

PLUGIN_API_VERSION = 1


@dataclass(frozen=True, slots=True)
class PluginContext:
    repo_root: Path
    project_type: str = "auto"


@dataclass(frozen=True, slots=True)
class PluginCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DoctorCallable = Callable[[PluginContext], list[PluginCheck]]


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    name: str
    description: str
    version: str = "builtin"
    source: str = "builtin"
    capabilities: tuple[str, ...] = ()
    doctor: DoctorCallable | None = field(default=None, compare=False, repr=False)
    api_version: int = PLUGIN_API_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "api_version": self.api_version,
            "source": self.source,
            "capabilities": list(self.capabilities),
        }


class DevtoolPlugin(Protocol):
    def descriptor(self) -> PluginDescriptor: ...
