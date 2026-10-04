# REMED-R04A Validation Schema 6 Cleanup Hotfix

## Scope

This hotfix addresses the post-apply cleanup failure observed after `REMED-R04A presentation evidence truth v3` completed validation and committed successfully.

The failure was not in the R04A source payload. The artifact validator passed and `devtool verify-apply` passed. The failure occurred when the post-apply `cleanup-life` step attempted to read cleanup protection authority and encountered an active validation session with `schema_version: 6`.

## Production failure

`cleanup-life` failed closed with:

```text
CleanupAuthorityUnavailableError: Cleanup protection authority is unreadable; refusing destructive cleanup: validation: ValidationSessionError: Unsupported validation session schema: 6
```

This is correct fail-closed destructive behavior, but the validation session reader was too strict: current Devtool schema is `7`, and the durable migration path accepted schemas `1..5` while rejecting schema `6` even though the migration code can safely upgrade schema-6 payloads by filling the current fields.

## Fix

- Extend durable validation session migration to accept `schema_version: 6` as a supported legacy schema.
- Preserve fail-closed behavior for genuinely malformed/missing schemas.
- Add a regression test proving a schema-6 active validation session can be loaded and used by cleanup ownership authority under `strict_authorities=True`.

## Validation

```text
python -m compileall -q devtool/validation_sessions.py tests/test_remed_r04a_validation_schema6_cleanup.py
git diff --check -- devtool/validation_sessions.py tests/test_remed_r04a_validation_schema6_cleanup.py
python -m pytest -q tests/test_rem07_validation_truth_sessions.py tests/test_remed_r04a_validation_schema6_cleanup.py
```

Result:

```text
19 passed
```

## Current overlay gaps

No R04A source payload gap remains from this failure. The remaining current-overlay gap is to apply this cleanup hotfix in the real Termux repository and confirm the original artifact cleanup path no longer fails on schema-6 validation sessions.
