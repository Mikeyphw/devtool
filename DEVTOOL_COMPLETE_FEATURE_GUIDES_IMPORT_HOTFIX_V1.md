# Devtool complete feature guides import hotfix v1

## Problem

`tests/test_complete_feature_guides.py` imported the guide generator with
`from tools.generate_complete_feature_guides import ...`.  The generator file
was present and executable, but on some Python/pytest environments (observed on
Termux with Python 3.14) the repository's `tools/` directory was not resolved
as that namespace package, so test collection failed with
`ModuleNotFoundError: No module named 'tools.generate_complete_feature_guides'`.

The generator itself was healthy: `python3 tools/generate_complete_feature_guides.py --check`
still reported all 136 command parsers current.

## Fix

The test now loads `tools/generate_complete_feature_guides.py` explicitly from
its repository-relative file path with `importlib.util.spec_from_file_location`.
The loaded module is registered in `sys.modules` before execution so dataclass
and module metadata behavior remains normal.

This deliberately does **not** add `tools/__init__.py` or change the production
Python package surface merely to satisfy a test import.

## Validation

- `python3 -m pytest -q tests/test_complete_feature_guides.py` -> 4 passed.
- `python3 tools/generate_complete_feature_guides.py --check` -> current, 136 command parsers.
