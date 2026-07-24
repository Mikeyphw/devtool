# Phase 16: Python Chroot Venv Bootstrap Guard

This phase tightens the Python chroot environment fix from Phase 15.

## Problem

Python validation specs can be planned on the Termux host and then executed inside the Android chroot. A host-side existence check for the project venv can incorrectly skip the `python -m venv` phase even though the venv does not exist inside the chroot. The next restore phase then fails with:

```text
env: /home/termux/.local/share/devtool/python/venvs/.../bin/python: No such file or directory
```

## Fix

- Python chroot plans now always emit an idempotent venv bootstrap phase before restore commands.
- The bootstrap interpreter preserves the requested Python executable name when possible, such as `python3.14`, instead of always collapsing Termux host paths to `python3`.
- The Python environment plan records the selected execution environment so restore planning can distinguish host/native and chroot behavior.
- Regression tests cover stale host-side venv paths and Python version preservation.

## Contract

When `execution_environment = chroot` for a Python project, Devtool must not trust host-side filesystem checks to decide whether the guest venv exists. Venv creation/restoration must happen in the same execution environment as pip, tests, and source audit.
