# Release hardening

Devtool 1.1 retains the shared hardening rules to every runner and frontend.

## Event integrity

Gradle events include the active devtool run ID and a monotonically increasing
sequence. Unknown event kinds, events for another run, negative counters, and
completed counts larger than totals are rejected. Rejection counts are included
in the progress snapshot for diagnostics.

## Secret redaction

Persistent logs redact common secret-bearing environment variables, bearer
authorization values, and command-line secret flags before writing to disk.
This is a safety net, not permission to print credentials deliberately.

## Process cleanup

- .NET validation stops the .NET build server at the outer operation boundary.
- Gradle operations call the project wrapper's `--stop` command when available,
  otherwise the configured Gradle executable.
- Cancellation remains scoped to the current run's process tree.

## Supported release checks

The repository release gate includes:

- Python compilation and the complete automated test suite;
- shell syntax checks for installers and build entry points;
- Java compilation of command-model/importer classes that do not require the
  Android framework;
- source-contract checks for Android and Qt frontend wiring;
- archive integrity validation for the distributed repository ZIP.

A real Android SDK build, physical-device instrumentation run, and Qt link build
still require their respective external toolchains and are reported explicitly
when unavailable.

## Native release qualification

Slice 5 promotes native toolchains to first-class CI gates. Qt builds compile,
link, and launch both QML applications offscreen. Android release tasks require
explicit signing, run lint and JVM tests, and verify the resulting APK signature.
Connected instrumentation provides install/launch coverage. Debian and source
archives use `SOURCE_DATE_EPOCH`, checksums, and reproducibility checks. See
`RELEASE_QUALIFICATION.md` for commands and skip/failure semantics.
