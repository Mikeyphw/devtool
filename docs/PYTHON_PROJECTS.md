# Python project support

Devtool treats the Python environment as part of validation evidence, not as an
ambient shell detail. Project inspection, normal validation, and embedded
artifact validators share the same manager, interpreter, dependency groups,
source roots, and environment fingerprint.

## Detection and workspaces

Python roots are detected from `pyproject.toml`, setup metadata, requirements,
lock files, tox, nox, and pytest configuration. Nested roots become named
targets when `devtool init` sees a Python monorepo. uv workspace members remain
owned by the workspace root.

Manager selection order:

1. `[python].manager`
2. Existing uv, Poetry, PDM, Pipenv, or Hatch lock/configuration
3. uv when installed
4. A Devtool-owned virtual environment

Accepted managers are `auto`, `uv`, `poetry`, `pdm`, `pipenv`, `hatch`, and
`venv`. Unknown values fail instead of falling through to pip accidentally.
Malformed `pyproject.toml` files report their parse location and stop planning.

## Core configuration

```toml
[project]
kind = "python"
root = "."
python = "python3.13"
package_command = "" # honored when set

[python]
manager = "auto"
test_framework = "auto" # pytest | unittest | tox | nox
environment = ".devtool/venvs/main"
groups = ["test", "dev"]
extras = []
locked = true
offline = false
install_project = true
lint = ["ruff", "ruff-format", "mypy"]
validator_dependencies = []
pre_commit = "auto"
source_import_isolation = false # optional source/editable import probe
cli_contract = false
isolation = true
disable_network = false # when true, tests run in a Linux unshare network namespace
matrix = ["3.11", "3.12", "3.13"]
matrix_mode = "active" # full enables the matrix
fresh_dist = true
clean_categories = ["artifacts", "caches"]
```

`requires-python`, `.python-version`, the explicitly configured interpreter,
and existing project environments are checked together. An incompatible
interpreter fails before dependency installation.

## Environment truth and caches

The environment fingerprint includes:

- interpreter path and version
- manager and manager version
- lock and dependency-file hashes
- selected groups and extras
- platform, architecture, and execution environment

State is written beneath:

```text
.devtool/state/python/environment.json
.devtool/state/python/installed-distributions.json
```

Pip, uv, Poetry, PDM, Ruff, mypy, tox, bytecode, Cargo, CMake, and native build
caches are separated by desktop, Termux, and chroot execution context. Native
cache identity also includes Python ABI and architecture.

Devtool never uses global `sudo pip`.

## Validation pipeline

The full pipeline can include:

```text
dependency synchronization
reproducible environment snapshot
source/version/generated-output audit
generated-source commands
tests and optional Python matrix
changed-line and total coverage gates
lint, formatting, typing, and security tools
pre-commit hooks
database migration checks
notebook validation or execution
service start/readiness/check/termination
pip dependency integrity
clean installed-wheel import isolation
optional source/editable import probes
console-script contract checks
wheel and sdist build
clean wheel installs and import/CLI smoke checks
sdist-to-wheel rebuild
optional reproducible-build comparison
optional SBOM generation
```

Pytest writes JUnit and coverage XML under `.devtool/reports`. Ruff, mypy,
Pyright, Pylint, Bandit, and other module tools run through the selected Python
environment and emit structured Devtool summaries. A venv command is always
`<venv>/bin/python -m <tool>` or the tool's venv executable, never the invalid
`python ruff ...` form.

### Test frameworks and selection

`test_framework = "auto"` detects pytest, unittest, tox, or nox from project
configuration and dependencies. The configured value always wins.

Pytest selectors use node IDs, `-k`, and `-m`. Those switches are never added
to tox, nox, unittest, or arbitrary custom test commands. Set
`test_adapter = "pytest"` only when a custom command intentionally accepts
pytest syntax.

### Affected-file validation

Artifact inventory is used to scope Python validation:

- documentation-only changes skip Python build, lint, and tests
- changed Python files receive file-scoped lint
- source modules map to nearby `test_<module>.py` tests
- unmapped source changes retain the full test suite
- `pyproject.toml`, lock files, requirements, `conftest.py`, package
  `__init__.py`, build metadata, generators, and shared test configuration force
  full validation

```toml
[python.coverage]
minimum_total = 85
minimum_changed_lines = 95
```

Changed-line coverage is calculated from coverage XML and the configured Git
base.

## Embedded Python artifact validators

A `python3` artifact validator runs from the repository root with the project
Python environment. It can declare groups, extras, and validator-only
requirements:

```json
{
  "validation": {
    "script": {
      "path": ".devtool-artifact/validate.py",
      "runner": "python3",
      "sha256": "...",
      "timeout_seconds": 900,
      "python": {
        "manager": "auto",
        "groups": ["test"],
        "extras": ["test"],
        "dependencies": ["pytest>=8", "packaging>=24"],
        "sync_dependencies": true,
        "install_project": true,
        "offline": false
      }
    }
  }
}
```

Validator-only dependencies use a cached fingerprinted environment. Missing
imports are reported with the selected interpreter and a declaration hint.
Undeclared packages are not guessed from a traceback and silently installed.

## Packaging and release confidence

```toml
[python.package]
verify_all_wheels = true
rebuild_sdist = true
import_smoke = true
verify_cli = true
install_dependencies = false
reproducible = true
import_modules = ["devtool"]
console_scripts = ["devtool"]
```

The package gate clears stale `dist/` output at execution time, validates archive
structure, wheel metadata, tags, and every hash and size declared by `RECORD`,
installs every wheel in a clean virtual environment, understands namespace
packages, verifies configured imports and console scripts, rebuilds wheels from
sdists, revalidates and installs those rebuilt wheels, and checks name/version
metadata parity. Optional reproducibility builds occur from two clean source copies with
`SOURCE_DATE_EPOCH` fixed.

The source audit also detects standard-library shadowing, static version drift
between metadata/source/Git tags/changelog, and stale common generated outputs.

## Native extensions, services, databases, and notebooks

Native backends such as maturin, setuptools-rust, scikit-build-core,
meson-python, Cython, pybind11, and cffi are detected. `devtool doctor` reports
missing Rust, CMake, Ninja, or compiler tools.

```toml
[python.notebooks]
validate = true
execute = false

[python.database]
enabled = "auto"
# commands = ["alembic upgrade head", "alembic downgrade -1"]

[python.service]
enabled = true
start = "python -m demo.server"
url = "http://127.0.0.1:8000/health"
check = "python tools/api_smoke.py"
timeout = 60
```

Automatic database checks are non-destructive: Alembic uses `alembic check`,
and Django uses `makemigrations --check --dry-run`. Explicit migration
round-trips remain project configuration. Service validation owns the process group, waits for HTTP or TCP readiness,
runs a check, terminates the process group before collecting its remaining
output, and cannot deadlock on a still-running stdout pipe. Notebook execution
uses disposable copies and never rewrites checked-in notebooks.

## Security and SBOM

```toml
[python.security]
enabled = true
tools = ["pip_audit", "bandit"]
sbom = true
```

Security tools execute only when enabled or explicitly configured. The SBOM is
written to `.devtool/reports/python-sbom.cdx.json` and includes the environment
fingerprint. Additional license, secret, provenance, or organization-specific
checks can be supplied through `security_commands`.

## Safe cleanup

```bash
devtool clean
```

Cleanup is target-aware. It does not recurse into nested projects, fixtures,
vendored trees, Git metadata, or unrelated directories merely named `build`.
Categories are `artifacts`, `caches`, `environments`, and `all`.
