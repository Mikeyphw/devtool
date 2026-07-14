# Test discovery and selection

Devtool exposes one selection model across .NET, Android/Gradle, JVM Gradle,
and Python projects.

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
```

The source catalog records a stable test ID, suite/class, project/module,
framework, type, tags, source path, line, and required execution environment.
Final executed totals still come from each framework's authoritative report.

## Saved profiles

```toml
[test_profiles.quick]
include = ["unit", "smoke"]
exclude_tags = ["slow", "network"]

[test_profiles.parser]
paths = ["tests/parser"]
tags = ["manifest"]

[test_profiles.changed]
selection = "changed"
base = "origin/main"
```

Named targets can declare their own profiles under
`[targets.<name>.test_profiles.<profile>]`.

Validation does not inherit an arbitrary ad-hoc test selection. It uses the
configured validation profile or an explicit `--test-profile`:

```toml
[validation]
test_profile = "full"
```

```bash
devtool validate --test-profile quick
devtool apply-overlay patch.zip --validate --test-profile full
```

## Native translation

- .NET selectors become `dotnet test --filter` expressions.
- Gradle class/method selectors become `--tests` arguments.
- Android instrumentation selectors become AndroidJUnitRunner `class`,
  `annotation`, and `notAnnotation` arguments.
- Pytest selectors become node IDs, `-k` expressions, and `-m` markers.

Reports preserve the distinction between discovered, selected, executed,
passed, failed, skipped, and excluded tests.
