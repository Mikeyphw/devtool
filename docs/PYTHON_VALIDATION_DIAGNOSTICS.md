# Python validation diagnostics

Devtool treats structured pytest/JUnit reports as the source of truth for Python test identity, totals, and failures.

## Diagnostic codes

- `PYTEST_FAILURE` — an assertion or other test-specific failure.
- `PYTHON_DEPENDENCY` — a failed test caused by a missing/required Python dependency.
- `PYTHON_IMPORT` — a Python import failure which is not clearly a missing distribution.
- `PYTHON_PLATFORM` — a runtime/platform incompatibility reported by a Python test.
- `PYTEST_WARNING` — a warning emitted in pytest's warnings summary.
- `PYTHON_UNDECLARED_IMPORT` — production Python imports a third-party module with no matching declared distribution.
- `PYTHON_PLATFORM_GLIBC` — production Python hard-codes a glibc SONAME such as `libc.so.6`.

Python JUnit failures are never labelled `GRADLE_TEST`. When a current pytest JUnit report exists, console `FAILED ...` lines are fallback context only and are not counted as additional failures.

## Warning and error counts

HUD, JSON, Rich summaries, and clipboard summaries consume the same structured `OperationResult.issues` collection. Pytest warning-summary lines are promoted into `PYTEST_WARNING` issues, while JUnit testcases are canonical error identities. This keeps counts consistent across output surfaces.

## Python source/dependency preflight

`python-source-audit` inventories:

- importable project source roots;
- production `.py` files outside the package tree;
- extensionless executable scripts with a Python shebang.

Dependency enforcement is intentionally narrower than inventory. By default, `dependency-scope = "project"`: package modules plus conventional project automation under `scripts/` and `tools/` are checked against project/build/optional requirement metadata and `requirements*.txt`. Heterogeneous auxiliary files such as `dotfiles/**/bin/*` are still scanned and reported in audit metadata, but they do not redefine the installable package dependency contract. This prevents captured host tools and pip-generated launchers from producing false project dependency failures.

Projects which intentionally manage every Python script from one dependency set can opt back into strict repository-wide enforcement:

```toml
[tool.devtool.python-audit]
dependency-scope = "repository"
```

`dependency-scope = "off"` disables dependency declaration enforcement while keeping version/staleness/source checks. Individual auxiliary paths can be promoted or excluded with `include`/`exclude` glob lists. The standalone source-audit command also accepts `--dependency-scope project|repository|off` for one run.

Small pip/setuptools-style console-script launchers are recognized as generated installation artifacts and are not dependency-enforced by default, even in repository scope. Set `include-generated-launchers = true` when those wrappers are intentionally owned source.

Imports explicitly marked `# type: ignore[import-not-found]` are treated as externally supplied system integrations. Imports guarded by `ImportError`/`ModuleNotFoundError` are treated as optional unless the source itself states that the dependency is required/installable.

Direct `ctypes.CDLL("libc.so.6")`/`ctypes.PyDLL("libc.so.6")` use is a portability error on dependency-enforced project surfaces. On auxiliary scripts it is retained as an advisory audit finding rather than failing an unrelated package validation. Prefer `ctypes.CDLL(None)` or `ctypes.util.find_library("c")`.

## Failure rendering

For Python test failures the failure panel uses canonical JUnit diagnostics and shows failure-category counts instead of selecting an arbitrary tail excerpt as the primary explanation. Raw output remains available in the operation log.
