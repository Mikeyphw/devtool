#!/usr/bin/env python3
"""Compatibility facade for the Android standalone build engine.

Canonical implementation ownership now lives under :mod:`devtool.android`.
The private ``devtool.android._standalone`` module intentionally remains
self-contained because Devtool stages that file directly into chroot runtimes
as ``build-apk``.  This facade preserves all historical imports, including
private helper names used by compatibility tests and downstream integrations.
"""

from __future__ import annotations

import sys
from types import ModuleType

from .android import _standalone as _impl

# Preserve the historical module surface exactly, including private helpers.
# Do not use ``from ... import *``: Python intentionally omits underscore names
# and several existing consumers import those compatibility helpers directly.
for _name in dir(_impl):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_impl, _name)

def standalone_source_path():
    """Return the canonical self-contained builder source used for staging."""
    return _impl.Path(_impl.__file__).resolve()

def standalone_companion_path():
    """Return the package source for the staged ``termux_toolchains.py`` companion."""
    return standalone_source_path().parent.parent / "termux_toolchains.py"

__all__ = [name for name in dir(_impl) if not name.startswith("__")] + ["standalone_source_path", "standalone_companion_path"]


class _AndroidBuildFacadeModule(ModuleType):
    """Mirror runtime monkeypatches into the canonical standalone engine."""

    def __setattr__(self, name, value):
        if not name.startswith("__") and hasattr(_impl, name):
            setattr(_impl, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if not name.startswith("__") and hasattr(_impl, name):
            try:
                delattr(_impl, name)
            except AttributeError:
                pass
        super().__delattr__(name)


sys.modules[__name__].__class__ = _AndroidBuildFacadeModule

if __name__ == "__main__":
    raise SystemExit(_impl.standalone_main())
