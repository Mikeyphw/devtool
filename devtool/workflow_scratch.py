from __future__ import annotations

"""R38-R2 opt-in per-node scratch/cache *environment routing*.

This is not a filesystem sandbox. Only tools honoring the environment keys
are isolated; commands can still access external paths explicitly. The owned
root and deletion authority belong exclusively to output_scopes (FC-01).
"""

import os
from pathlib import Path
import stat


# CommandSpec.env cannot shadow our routing keys. Caller/global environment is
# intentionally overridden for the subprocess only, never mutated in-process.
_SUBDIRS = {
    "TMPDIR": "tmp",
    "TMP": "tmp",
    "TEMP": "tmp",
    "XDG_CACHE_HOME": "cache/xdg",
    "PIP_CACHE_DIR": "cache/pip",
    "npm_config_cache": "cache/npm",
    "GOCACHE": "cache/go",
    "GRADLE_USER_HOME": "cache/gradle",
    "UV_CACHE_DIR": "cache/uv",
    "PYTHONPYCACHEPREFIX": "cache/pycache",
}


def reserved_environment_names() -> frozenset[str]:
    return frozenset({*(name.upper() for name in _SUBDIRS), "DEVTOOL_R38_SCRATCH_ROOT", "DEVTOOL_WORKFLOW_TMPDIR"})


def create_isolated_environment(root: Path, identity: tuple[int, int]) -> dict[str, str]:
    """Pin owned inode, mkdir through no-follow dirfds, refuse unsafe roots.

    Existing output-scopes registration has already minted an exclusive root,
    written/fsynced its ownership journal and holds the lease until cleanup.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("R38_SCRATCH_UNSUPPORTED_HOST")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root, flags)
    try:
        info = os.fstat(root_fd)
        if ((info.st_dev, info.st_ino) != tuple(identity) or
                not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077):
            raise ValueError("R38_SCRATCH_OWNERSHIP_MISMATCH")
        # Paths are fixed literals defined here (not user supplied). Walk through
        # the pinned root; no component can be a symlink, regular file or device.
        for relative in sorted(set(_SUBDIRS.values()) | {"cache", "tmp"}, key=lambda s: (s.count("/"), s)):
            fd = os.dup(root_fd)
            try:
                for part in Path(relative).parts:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, flags, dir_fd=fd)
                    os.close(fd)
                    fd = next_fd
                    st = os.fstat(fd)
                    if st.st_uid != os.getuid() or st.st_mode & 0o077:
                        raise ValueError("R38_SCRATCH_DIRECTORY_NOT_PRIVATE")
            finally:
                os.close(fd)
        return {**{key: str(root / rel) for key, rel in _SUBDIRS.items()},
                "DEVTOOL_R38_SCRATCH_ROOT": str(root)}
    finally:
        os.close(root_fd)
