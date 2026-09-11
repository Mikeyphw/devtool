# Test discovery and selection

Devtool exposes one structured test-selection model across .NET, Android/Gradle,
JVM Gradle, and Python projects. Python/pytest also has first-class exact-node
selection so validation can run only the pytest tests you explicitly request,
analogous to selecting explicit Gradle tasks.

## Common test selectors

```bash
devtool test --list-tests
devtool test --select unit
devtool test --class ManifestParserTests
devtool test --method ManifestParserTests.ValidManifest
devtool test --tag fast --exclude-tag network
devtool test --path tests/parser
devtool test --failed
devtool test --changed --changed-from origin/main
devtool test --profile quick
devtool --variant release test --profile quick
```

The source catalog records a stable test ID, suite/class, project/module,
framework, type, tags, source path, line, and required execution environment.
Final executed totals still come from each framework's authoritative report.

## Exact pytest selection

Use repeatable `--pytest-test` arguments when you need precise pytest selection.
A selector can be a test file, class node, function, method, or any executable
pytest node ID, including parametrized node IDs.

```bash
# Whole file
devtool validate --pytest-test tests/test_auth.py

# One function
devtool validate \
  --pytest-test tests/test_auth.py::test_login

# One class and one method
devtool validate \
  --pytest-test tests/test_api.py::TestUsers \
  --pytest-test tests/test_sync.py::TestSync::test_recovery

# Parametrized node IDs are preserved verbatim
devtool test \
  --pytest-test 'tests/test_math.py::test_add[1-2-3]'
```

When at least one `--pytest-test` is present, the selection is **exclusive by
default**. Devtool suppresses configured/default/affected pytest targets and
passes only the explicit pytest selectors to pytest. This prevents a command
that asks for one test from accidentally running the entire suite.

For example:

```bash
devtool validate --pytest-test tests/test_auth.py::test_login
```

plans pytest conceptually as:

```text
python -m pytest tests/test_auth.py::test_login
```

not:

```text
python -m pytest tests/ tests/test_auth.py::test_login
```

If you intentionally want explicit selectors **plus** configured/default test
targets, opt in:

```bash
devtool validate \
  --pytest-test tests/test_auth.py::test_login \
  --pytest-include-defaults
```

### Pytest filters and extra argv

Pytest-specific filters are structured command arguments rather than shell
text:

```bash
devtool validate \
  --pytest-test tests/test_auth.py \
  --pytest-marker 'not slow' \
  --pytest-marker 'integration' \
  --pytest-k 'login or logout' \
  --pytest-arg=--maxfail=1 \
  --pytest-arg=-x
```

Repeated `--pytest-marker` expressions are combined with logical `and`.
`--pytest-k` is combined with any generic native Python test filter. Repeated
`--pytest-arg` values are appended as individual argv entries; Devtool does not
construct a shell command from them.

## Native pytest discovery

For interactive selection, Devtool asks pytest for its executable collection:

```text
pytest --collect-only -q
```

This is important for parametrized tests, generated tests, plugins, and other
cases where parsing Python source alone cannot know the final pytest node IDs.
If runtime collection cannot run, Devtool falls back to its source catalog and
reports why the fallback was used.

The terminal validation cockpit exposes:

- exact pytest test multi-selection;
- explicit refresh of pytest collection;
- marker expressions;
- `-k` expressions;
- additional pytest argv;
- an explicit toggle for including configured/default targets.

The GTK Control Center exposes the same dedicated pytest selector fields and
uses the same command model rather than inventing separate test semantics.

`devtool test --list-tests` includes discovery-source metadata in machine output
so clients can distinguish native pytest collection from the source fallback.

## Configuration defaults

Repository defaults can be declared under `[python.pytest]`:

```toml
[python]
test_framework = "pytest"

[python.pytest]
tests = ["tests/unit"]
markers = ["not slow"]
k = ""
args = ["--maxfail=1"]
include_defaults = false
```

A saved test profile can override them:

```toml
[test_profiles.quick]
pytest_tests = [
  "tests/unit/test_auth.py::test_login",
  "tests/unit/test_api.py::TestUsers::test_create",
]
pytest_markers = ["not slow"]
pytest_k = ""
pytest_args = ["--maxfail=1"]
pytest_include_defaults = false
```

Generic profiles remain supported:

```toml
[test_profiles.parser]
paths = ["tests/parser"]
tags = ["manifest"]

[test_profiles.changed]
selection = "changed"
base = "origin/main"
```

Named targets can declare their own profiles under
`[targets.<name>.test_profiles.<profile>]`.

Validation does not inherit an arbitrary ad-hoc generic test selection. It uses
the configured validation profile or an explicit `--test-profile`:

```toml
[validation]
test_profile = "full"
```

```bash
devtool validate --test-profile quick
devtool apply-overlay patch.zip --validate --test-profile full
```

## Artifact-driven pytest selection

Schema-v2 overlay validation may request exact pytest selection as structured
metadata:

```json
{
  "schema_version": 2,
  "type": "overlay",
  "validation": {
    "pytest_tests": [
      "tests/test_api.py::test_create",
      "tests/test_auth.py::test_login"
    ],
    "pytest_markers": ["not slow"],
    "pytest_k": "",
    "pytest_args": ["--maxfail=1"],
    "pytest_include_defaults": false
  }
}
```

These values are preserved when artifact validation is deferred and resumed.
Explicit CLI pytest-selection flags take precedence over the corresponding
artifact fields.

The selection precedence is:

```text
explicit CLI pytest selection
        ↓
artifact-declared pytest selection
        ↓
named test/validation profile
        ↓
[python.pytest] repository defaults
        ↓
normal pytest defaults
```

## Diagnostics

Devtool recognizes pytest collection failures separately from generic command
failures. Exact-selector mistakes and empty collections therefore produce
structured diagnostics such as `PYTEST_SELECTOR_NOT_FOUND` and
`PYTEST_NO_TESTS` instead of only surfacing pytest's numeric exit status.

The planned command metadata records whether exact pytest mode is active,
which selectors were requested, whether defaults were suppressed, and the
selection source. Machine/IPC consumers can therefore report the same
selection semantics as the terminal and desktop UIs.

## Native translation

- .NET selectors become `dotnet test --filter` expressions.
- Gradle class/method selectors become `--tests` arguments.
- Android instrumentation selectors become AndroidJUnitRunner `class`,
  `annotation`, and `notAnnotation` arguments.
- Pytest selectors become node IDs, `-k` expressions, and `-m` markers.

Reports preserve the distinction between discovered, selected, executed,
passed, failed, skipped, and excluded tests.

When a variant is selected, test command planning records it in command
metadata and applies the runner's variant behavior before translating test
selectors. For example, Android `--variant release` infers
`testReleaseUnitTest`, while .NET `--variant release` adds `-c Release`.
