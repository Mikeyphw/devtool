# Doctor, validation profiles, and build-artifact lifecycle

## Doctor

`devtool doctor` retains the existing project/toolchain diagnostics and now also
collects structured runtime, Android, device, and project health checks. Use
`devtool doctor --json` for the stable machine-readable payload.

The additional checks cover Python/pip/setuptools/wheel, Java/Gradle/CMake,
Android SDK/build-tools/NDK discovery, adb/device availability, optional root,
and repository/config/build-target state.

## Validation workflow profiles

`devtool validate --profile NAME` selects a workflow profile. Built-ins are:

- `minimal` — fast build-oriented validation with restore/tests skipped.
- `default` — normal repository validation behavior.
- `android-full` — Android-safe build/test/lint validation.
- `release` — full validation with artifact production/external verification required.
- `termux` — Android-safe split Gradle validation, build cache, parallel execution, and no instrumentation requirement.

Repository-specific profiles live in `.devtool/validation.toml` under
`[profiles.<name>]`. Nested `validation`, `gradle`, `android`, and `artifacts`
tables merge onto the current repository configuration. Explicit CLI resource
flags continue to take precedence.

`--profile` is exclusively the validation workflow selector and defaults to
`default`. `--resource-profile` is exclusively the resource-policy selector. The
former `--validation-profile`/resource alias is intentionally removed; there is no
deprecation bridge or silent remapping.

## Build artifact registry

Produced Android/Gradle artifacts are registered in
`.devtool/artifacts/registry.json` with an ID, path, type, size, SHA-256,
repository commit, timestamps, validity, and verification metadata.

Commands:

```text
devtool artifact list
devtool artifact inspect <id|path|name>
devtool artifact verify <id|path|name>
devtool artifact prune --older-than 30d
```

All commands support `--json`. `artifact prune` removes registry entries by
default. `--delete-files` may additionally delete files, but only when they are
inside the managed `.devtool/build-artifacts` directory.
