# Devtool 0.2.32

- Removed path-aware concurrent-run support and all concurrency CLI options.
- Added one simple non-waiting run lock per repository.
- Serialized apply, validate, rollback, commit, and tag operations.
- Prevented a rejected second run from replacing the live HUD with a false placeholder.
- Removed standalone Termux Android notifications and the `android-notify` mode.
- Removed legacy notification CLI flags and Termux notification fallback.
- Removed legacy Tasker CLI flags and broadcast integration; native app plug-in support remains.
- Simplified Android notifications, floating HUD, and root execution to one active run.
- Added stale Android run-record handling so an abandoned entry cannot block the app indefinitely.
- Updated completions, tests, and documentation for the simplified workflow.
