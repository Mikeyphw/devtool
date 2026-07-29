# Patch summary: Phase 42 Repo Queue + Orphan Recovery

This patch adds a safe repository operation recovery layer to the Android app.

## Highlights

- Adds `RepoQueueRecovery` for queue-health assessment and support-bundle summaries.
- Adds explicit `terminateOrphan`, `markInterrupted`, `retry`, and `queueHealth` entry points in `RepositoryWorkspaceStore`.
- Adds Repository Detail cards for queue health, orphan termination confirmation, mark-interrupted confirmation, and retry actions.
- Adds support bundle `repo_queue_health` diagnostics per repository.
- Adds debug classifier keys for queue health, orphan termination, mark-interrupted, and retry events.
- Adds Phase 42 source contracts.

## Safety

Recovery actions are metadata/process-control only. They do not run Git reset, Git clean, delete files, or print recovery breadcrumbs into Git stdout/stderr. Retry actions queue a new Git operation and refuse missing-payload commit/stash retries.
