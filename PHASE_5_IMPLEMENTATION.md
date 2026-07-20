# Android Logs and Repositories Phase 5

Phase 5 connects the Android repository workflow to the structured Logs system and hardens root Git execution across process death.

## Durable operation queue

Every stage, unstage, discard, commit, fetch, pull, push, branch, stash, and reset action is inserted into `repository_operations` before execution. Operations run FIFO per repository, while the shared worker pool permits unrelated repositories to proceed concurrently. Queue and active state survives UI recreation.

Each operation receives a synthetic Devtool run with `project_type=git`, repository identity, command description, timestamps, exit state, and structured log entries. Repository activity, global Logs, repository Sync, notifications, and run metadata all use the same operation/run IDs.

## Cancellation and root orphan recovery

The owner-aware root broker accepts a cancellation token and polls it while waiting for `su`. Active operations export a validated `DEVTOOL_ANDROID_OPERATION_ID` marker into the root, Termux, or chroot process environment. Cancellation terminates all matching marked processes by inspecting `/proc/*/environ`, then records a cancelled synthetic run.

On app startup, persisted queued/running/orphaned operations are reconciled with the root process table. Missing processes become interrupted. Surviving processes become explicit orphaned operations and remain terminable from the repository Sync tab. This prevents a detached Git command from becoming invisible after Android kills the frontend.

## Sync and diagnostics

The repository detail screen adds a Sync destination with:

- active and queued operations plus exact structured-log links;
- cancellation or orphan termination;
- bounded local incoming/outgoing commit previews based on existing upstream refs;
- passive remote transport and credential diagnostics;
- recent completed Git operations.

Diagnostics never contact the remote. They inspect local Git/SSH availability, `SSH_AUTH_SOCK`, askpass configuration, credential helpers, readable standard identity files, and known-host state. Identity paths are not copied into structured logs.

## Cross-feature navigation

- Repositories can open Logs with a persisted repository filter.
- Git operation logs open their repository directly on Sync.
- Repository cards display active Git-operation totals.
- Completion notifications reopen the exact repository and Sync tab.
- Commit-created runs are updated with the resulting HEAD for artifact/run correlation.

## Persistence

`DevtoolDatabase` version 5 adds `repository_operations`, indexed by repository/state and linked to repositories with cascading cleanup. Repository re-keying updates operation records alongside runs and events.

## Version

Android frontend: **1.12.0**, `versionCode 25`.
