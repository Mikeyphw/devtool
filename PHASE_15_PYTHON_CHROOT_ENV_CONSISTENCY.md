# Phase 15: Python chroot environment consistency

This phase fixes Python validation when Android/Termux auto-chroot policy includes Python projects.

## Problem

The Termux-host backend could decide to run validation commands inside `chrootctl`, while Python environment planning still happened with the host/default execution environment. That could produce command phases where `venv` appeared to start, but later restore phases tried to execute a chroot-local venv Python that had not been created in that same namespace.

Typical failure:

```text
env: /home/termux/.local/share/devtool/python/venvs/<repo>/<target>/bin/python: No such file or directory
```

## Changes

- Validation spec planning now resolves the effective execution backend before Python restore/test specs are built.
- When chroot is selected, Python plans use `execution_environment = "chroot"` before command construction, not after wrapping.
- Chroot Python bootstrap avoids embedding Termux-host interpreter paths such as `/data/data/com.termux/files/usr/bin/python3.14`.
- Venv restore now installs PEP 517 `build-system.requires` into the project venv before editable install.
- Editable installs from `pyproject.toml` use `pip install --no-build-isolation -e .` so the chroot-local venv owns the build instead of a silent temporary isolated environment.
- Python restore phase labels distinguish `restore:test-tools`, `restore:build-system`, and `restore:project`.

## Contract

When a Python project is routed through Android/Termux chroot policy, every Python validation phase must use the same chroot-local environment path and the same effective execution backend. Devtool must not create a host/chroot split-brain Python validation plan.
