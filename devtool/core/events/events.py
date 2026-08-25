from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DevtoolEvent:
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BuildStarted(DevtoolEvent):
    project: str = ""


@dataclass(slots=True)
class BuildFinished(DevtoolEvent):
    success: bool = True


@dataclass(slots=True)
class TaskStarted(DevtoolEvent):
    task: str = ""


@dataclass(slots=True)
class TaskFinished(DevtoolEvent):
    task: str = ""
    success: bool = True


@dataclass(slots=True)
class ArtifactCreated(DevtoolEvent):
    path: str = ""


@dataclass(slots=True)
class WarningDetected(DevtoolEvent):
    message: str = ""


@dataclass(slots=True)
class OverlayApplied(DevtoolEvent):
    artifact: str = ""
