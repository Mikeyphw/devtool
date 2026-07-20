# Migrating to Devtool 1.1

The CLI command surface is compatible with 1.0.1. Slice 5 changes release and
frontend packaging behavior rather than repository workflow semantics.

## Android

The frontend version is 1.13.0 (`versionCode 26`). Debug builds now use the `.debug` application ID suffix, so they can coexist with a signed release build.
Release tasks require an explicit keystore and fail before compilation when the
four `DEVTOOL_RELEASE_*` values are incomplete. Existing 1.6.1 release installs
upgrade normally only when signed with the same certificate.

## Desktop

Control Center is 0.4.0. Packaging now uses `SOURCE_DATE_EPOCH`, emits a `.sha256`
file, and supports `--verify`. Both binaries support `--version`; the HUD also
supports `--exit-when-idle` for isolated smoke testing. Existing user settings
and history locations are unchanged.

## Release automation

Version edits must begin in `release/versions.json` and then be propagated to
Python, CMake, Gradle, Debian control metadata, and the manual page. The release
qualifier rejects drift. CI-created Android keys are ephemeral test credentials
and must never be used for published packages.
