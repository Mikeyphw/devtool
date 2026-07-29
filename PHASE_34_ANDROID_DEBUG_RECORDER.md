# Phase 34 Android Debug Recorder Foundation

Phase 34 adds the durable debug-event foundation used by the later Run Command Trace, Bridge Roundtrip Probe, Repo Manager Doctor, Debug Workbench, support-bundle v2, and debug-notification phases.

## Scope

This phase intentionally avoids UI-heavy workflows. It makes app failures observable first.

Implemented foundations:

- SQLite schema 10 `debug_events` table with indexes for time, category, run, repository, and fingerprint.
- `DebugEventRecord` model for timestamped event evidence.
- `DebugRecorder` helper for redacted event persistence and local in-app update broadcasts.
- `DebugIssueClassifier` for stable failure fingerprints and first-pass issue categories.
- Support bundle database stats include debug event counts.
- Devtool Live HUD version bumped to `1.17.0` / `versionCode 31`.

## Instrumented surfaces

Phase 34 records low-risk durable events from these surfaces:

- `TermuxCommandRunner`: run request, reject, intent send/accept/failure, watchdog waiting.
- `BridgeReceiver`: token rejection, unknown action, missing run id, stale update, accepted cancel.
- `RunStore`: run started, cancelled, diagnostic update, finished, interrupted.
- `RepositoryWorkspaceStore`: repo refresh, queue, start, success, cancel, failure, orphan, interrupted.
- `RootCommandBroker`: executor selected, chroot/Termux/root path, cancel, timeout, command failure.
- `ValidationController`: action request, stale response, identity mismatch, root bridge unavailable, command failure, pending action.
- `RootBridgeManager`: state changes and rejected root/validation/app commands.
- `TermuxResultService`: missing results, utility failures, delivered results.

## Event schema

Each event stores:

```text
event_id
timestamp_ms
severity
category
event_type
surface
run_id
repository_id
operation_id
validation_session_id
message
detail_json
fingerprint
redaction_state
```

## First issue categories

The classifier recognizes the early failure shapes needed by later phases:

```text
repo_runuser_failed
repo_chroot_unavailable
repo_shell_missing
repo_git_missing
path_missing
bridge_token_mismatch
termux_run_command_permission_missing
termux_missing
stale_bridge_update
timeout
cancelled
generic_failure
generic_event
```

## Validation

Phase-relevant checks passed:

```text
74 passed, 0 failed
```

Command used:

```text
python3 -m pytest tests/test_android_debug_phase34.py tests/test_android_app_source.py tests/test_android_repositories_phase3.py tests/test_android_repositories_phase4.py tests/test_android_repositories_phase5.py tests/test_android_repositories_phase6.py tests/test_android_repositories_phase7.py tests/test_android_validation_workbench_phase29_31.py tests/test_phase33_validation_release_seal.py
```

Pure Java smoke check also passed for:

```text
DebugIssueClassifier.java
DebugEventRecord.java
```

A full repository pytest run was attempted but was not completed in this container and surfaced unrelated baseline gaps in artifact/UI qualification files. Android Gradle compilation was not run in this container.

## Deferred to later phases

Phase 34 does not yet add the full visible Debug Workbench UI, wrapper sentinels, bridge roundtrip command, repo path repair UI, or debug notifications. Those are covered by phases 35 through 47.
