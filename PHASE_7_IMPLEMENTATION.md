# Phase 7 Implementation: Support Bundles and Field Diagnostics

Phase 7 adds an Android-side support bundle that captures the state needed to debug the Logs, Repositories, root bridge, retention and Git-operation systems without copying raw logs or leaking credential material.

## Delivered

- Added `SupportBundle` to produce a redacted JSON diagnostic bundle.
- Added `SupportRedactor` for reusable secret scrubbing.
- Exposed a Settings diagnostics card with:
  - `Copy support bundle`
  - `Share support bundle`
  - privacy switch for `Include redacted recent commands`
- Commands are omitted by default and replaced with `<disabled>`.
- When command inclusion is enabled, command text is still redacted before export.
- Raw stdout, stderr and launch logs are not included.
- The bundle includes:
  - app package and version metadata
  - Android SDK/release/manufacturer/model
  - root bridge state, label, version and redacted detail
  - run/default/backend/retention settings
  - SQLite schema and database counts
  - busy state for root log indexing and repository refresh
  - active run summary
  - bounded recent run summaries
  - bounded repository summaries
  - bounded recent operation summaries per repository
- Redaction covers:
  - current bridge token
  - HTTPS user/password segments
  - Authorization Bearer/Basic values
  - common token/password/secret/key assignments
  - private-key blocks

## Safety posture

The bundle is generated locally and copied or shared only after the user taps an explicit action. It avoids authoritative log bodies and never includes bridge-token or credential values. The command field remains opt-in because shell commands can contain paths, flags, URLs or local project details that may be sensitive even after redaction.

## Version

Android frontend version: `1.14.0`

Android versionCode: `27`

Database schema remains `6`; this phase does not require a data migration.

## Validation

- Phase 7 source contracts passed.
- Support redactor JVM harness passed with `-Xlint:all -Werror`.
- Android, repository, Tasker and release metadata slice passed: 156 tests.
- XML and JSON resource parsing passed where local tools were available.
- Python compilation passed.
- Full pytest run was started and progressed well beyond the Android slice, but did not finish before the tool timeout; no additional Phase 7 failure was observed before timeout.
