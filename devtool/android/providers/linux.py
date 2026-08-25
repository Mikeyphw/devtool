"""Ordinary Linux host provider helpers."""

from ...android_build import (
    certificate_store_available,
    executable_provider,
    normalized_machine,
    running_under_termux_python,
)


def is_linux_host() -> bool:
    """Return whether the active host is Linux outside the Termux Python runtime."""
    import sys
    return sys.platform.startswith("linux") and not running_under_termux_python()


__all__ = [
    "certificate_store_available",
    "executable_provider",
    "is_linux_host",
    "normalized_machine",
    "running_under_termux_python",
]
