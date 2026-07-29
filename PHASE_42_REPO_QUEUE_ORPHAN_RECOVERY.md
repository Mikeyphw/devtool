# Phase 42: Repo Queue + Orphan Recovery

Phase 42 makes the repository manager recoverable when Git operations are queued forever, running without clear ownership, or left behind as orphaned root/chroot processes.

## Scope

- Detect queued, running, stale and orphaned Git operation states.
- Surface a repository queue-health card in Sync and Metadata.
- Add safe confirmation flows for terminating tagged orphan processes and marking detached work interrupted.
- Allow retry of terminal operations using current repository metadata, including after path repair.
- Refuse unsafe/incomplete retries for operations whose original payload is not recoverable, such as commit or stash messages.
- Export queue recovery state in the support bundle.

## Safety contract

The phase does not run repair Git commands, reset/clean worktrees, delete files, mutate Git history, or inject trace/recovery text into Git command output.
