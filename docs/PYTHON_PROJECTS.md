# Python project support

Python projects are detected from `pyproject.toml`, setup metadata, dependency
files, lock files, and common test-runner configuration.

Devtool respects existing managers in this order:

1. Explicit `[python].manager` configuration.
2. Existing uv, Poetry, PDM, Pipenv, or Hatch lock/configuration metadata.
3. uv when installed.
4. A devtool-owned virtual environment and pip.

```toml
[project]
kind = "python"
root = "."

[python]
manager = "auto"
environment = ".devtool/venvs/main"
lint = ["ruff", "mypy"]
import_smoke = true
```

Supported commands:

```bash
devtool restore
devtool test
devtool lint
devtool build
devtool package
devtool clean
devtool validate
```

The default validation order is:

```text
dependency synchronization
tests
configured lint/type checks
PEP 517 wheel and sdist build
package archive and metadata verification
clean-target wheel installation
optional import smoke test
```

Pytest emits JUnit XML under `.devtool/reports`, which supplies authoritative
selected/executed/pass/fail counts. `unittest`, tox, and nox projects can use
explicit test commands while retaining the common runner and reporting model.

Supported lint adapters include Ruff, Ruff formatting, Black, isort, mypy,
Pyright, Pylint, and Bandit. Devtool only runs configured tools or tools already
declared in `pyproject.toml`.
