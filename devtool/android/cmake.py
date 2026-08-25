"""CMake requirement and host compatibility helpers."""

from ._standalone import (
    cmake_is_ready,
    ensure_host_cmake_compatible,
    installed_cmake_versions,
    resolve_cmake_requirement,
    select_required_cmake_version,
)

__all__ = [name for name in globals() if not name.startswith("_")]
