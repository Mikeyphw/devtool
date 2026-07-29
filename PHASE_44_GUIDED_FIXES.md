# Phase 44: Guided Fixes

Adds guided fix cards to the Android Debug Workbench. Guided fixes translate retained debug evidence into a safe sequence:

1. what happened
2. what it means
3. evidence
4. safe next action

The implementation is intentionally non-destructive. It can deep-link to existing surfaces, run the existing bridge roundtrip probe, copy diagnoses, or open settings/log/repository/validation screens. Git reset/clean/delete style actions are not available from the guided-fix layer.

## Covered issue families

- Termux RUN_COMMAND permission missing
- Termux wrapper never started
- bridge token mismatch
- missing workdir/path namespace mismatch
- missing devtool command binary
- run started with no progress
- repository path wrong namespace
- repository runuser/user-switch failure
- repository shell missing
- repository Git missing
- orphaned/stale repository operation
- stale validation revision
- blocked notifications

## Files

- `DebugGuidedFix.java`
- `DebugWorkbenchActivity.java`
- `SupportBundle.java`
- `tests/test_android_debug_phase44.py`
