"""Side-effect-light Android project diagnostic snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..android_build import infer_java_requirement, inspect_project


@dataclass(frozen=True, slots=True)
class AndroidProjectDiagnostic:
    root: str
    display_name: str
    android: bool
    gradle: bool
    compile_sdk: int | None
    agp_version: str | None
    ndk_version: str | None
    cmake_version: str | None
    java_minimum: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_android_project(root: str | Path) -> AndroidProjectDiagnostic:
    """Return a serializable project snapshot without provisioning toolchains."""
    project = inspect_project(Path(root).expanduser().resolve())
    requirement = infer_java_requirement(project)
    minimum = getattr(requirement, "minimum", None)
    return AndroidProjectDiagnostic(
        root=str(project.root),
        display_name=project.name,
        android=bool(project.android),
        gradle=any((project.root / name).is_file() for name in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts")),
        compile_sdk=getattr(project, "compile_sdk", None),
        agp_version=getattr(project, "agp_version", None),
        ndk_version=getattr(project, "ndk_version", None),
        cmake_version=getattr(project, "cmake_version", None),
        java_minimum=minimum,
    )


__all__ = ["AndroidProjectDiagnostic", "inspect_android_project"]
