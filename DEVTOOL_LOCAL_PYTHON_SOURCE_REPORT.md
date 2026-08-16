# Devtool local Python source dependency fix

## Problem

`uv sync --extra dev --extra tui` can fail for a local-only dependency when the
project metadata declares it as a normal registry requirement. The observed log
showed `bkx[interactive]` depending on `mikomn[cli]`, while `mikomn` is actually
a sibling source checkout at `~/Code/mikomn`. uv correctly looked for a registry
version because no committed uv source mapping was present.

## Fix

Devtool now detects conservative local sibling sources for uv-managed projects:

- source directories are searched beside the active repo and under common
  `~/Code`/`~/src` locations;
- the source must contain a valid `pyproject.toml`;
- the source package name must match the requested distribution name;
- existing `[tool.uv.sources]` declarations remain authoritative;
- unselected optional extras do not trigger local source installation;
- selected local sources are installed editable into the Devtool-managed
  environment before the current project;
- subsequent uv execution uses `uv run --no-sync` for the local-source plan so
  uv does not re-resolve the same unsourced registry requirement.

## Covered by tests

`tests/test_python_support.py` now covers:

- selected extra `mikomn[cli]` maps to a sibling editable install;
- unselected optional local dependencies keep the normal `uv sync` path;
- committed `[tool.uv.sources]` disables the sibling guess;
- sibling pyproject name mismatches are rejected;
- sibling pyprojects without a declared project name are rejected.

## Validation

Focused validation run:

```text
42 passed
```

Compile sanity:

```text
python3 -m compileall -q devtool tests
```
