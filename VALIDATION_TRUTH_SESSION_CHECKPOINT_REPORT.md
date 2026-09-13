# Overlay 7 of 15 — Validation truth, sessions, checkpoints and changed-test scope

## Scope

This overlay closes the S07 validation truth bucket.  It hardens validation sessions so finalization and UI/API consumers can no longer infer success from stale files, friendly statuses, malformed state, broad fingerprints, or unsafe JSON payloads.

## Findings closed

- S07-01 — duplicate validation phase keys are rejected instead of collapsing phase maps.
- S07-02 — validation phases with error/fatal diagnostics fail even when the process return code is 0.
- S07-03 — narrow-domain passing phases update only their relevant input-domain fingerprints.
- S07-04 — retry of a cached failure invalidates the failed phase key and dependents, not every same-named phase.
- S07-05 — `validation_required = "false"` is parsed as false.
- S07-06 — workflow profile string booleans do not accidentally skip builds/tests.
- S07-07 — input fingerprinting ignores symlinks resolving outside the repository instead of crashing.
- S07-08 — invalid stale sealed reports are regenerated, while valid sealed reports remain immutable.
- S07-09 — malformed validation session schemas fail with `ValidationSessionError` instead of raw `ValueError`.
- S07-10 — malformed pending control requests no longer crash validation control handling.
- S07-11 — changed-test selection is scoped to the project root in multi-project repositories.
- S07-R2-01 — non-object validation state files fail with a controlled session error.
- S07-R2-02 — invalid control revision input no longer crashes `require_revision`.
- S07-R2-03 — malformed event limits are bounded and do not crash event/status APIs.
- S07-R2-04 — validation events and session context are JSON-safe.
- S07-R2-05 — non-finite progress percentages are ignored instead of crashing progress updates.
- S07-R2-06 — skipped required checkpoint phases do not satisfy finalization.
- S07-R2-07 — legacy `rollback_on_failure = "false"` is parsed as false.
- S07-R2-08 — repository identity is stable across reader environments.

## Implementation notes

- Added strict boolean and bounded numeric coercion for validation session, profile, progress, and test-selection state.
- Made session payload migration tolerant of malformed legacy fields while still failing closed on unsupported schema shapes.
- Added JSON sanitization for session persistence, validation events, control payloads, status payloads, and report writing.
- Added duplicate phase-key detection while preserving occurrence-based keys for repeated phase names.
- Changed retry invalidation to operate by canonical phase key, while preserving the public return value as the failed phase name for compatibility.
- Made final report writing verify existing sealed report integrity.  A valid content-hashed sealed report remains immutable; invalid or stale files with bad/missing hashes are regenerated.
- Scoped changed-path based test selection to `project_root`, so sibling project changes cannot inflate or hide the selected tests for the current target.

## Validation

Focused validation passed:

```text
18 passed — tests/test_rem07_validation_truth_sessions.py
161 passed — validation/session/control/report/progress/test-selection regression sweep
```

The final repository-wide validation remains assigned to Overlay 15 of 15.
