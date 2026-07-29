# Phase 39: Root/chroot Executor Planner

Phase 39 hardens the Android repo-manager execution path by selecting a usable shell and user-switch strategy before Git operations run.

## What changed

- Added `RootExecutionPlan` to model the selected executor, shell, user-switch method, rejected shell candidates and rejected user-switch candidates.
- Replaced the normal chroot Git executor's hardcoded `zsh -lc` path with a planner probe.
- Added chroot shell selection in priority order: `zsh -> bash -> sh`, with `/bin/sh` as the last fallback marker.
- Added user-switch selection in priority order: `runuser -> su -> direct-root fallback`.
- Added debug events for selected plans, planner probe failures and fallback selection.
- Extended Repo Manager Doctor evidence with `selected_shell`, `user_switch_method`, `direct_root_fallback`, `rejected_shell_candidates` and `rejected_user_switch_candidates`.
- Extended support-bundle repo doctor JSON automatically through the stored doctor result.
- Extended debug issue classification for root execution plan fallbacks and planner probe failures.

## Safety

The planner probe is read-only. It checks shell and user-switch availability with no-op commands before the actual Git command is executed. Planner breadcrumbs are recorded as Android debug events and repo-doctor evidence rather than being printed into Git stdout, so `status -z`, diff parsing and other Git outputs stay clean.

## Covered failures

- `zsh` missing inside the configured chroot.
- `runuser` missing or failing for the configured Linux user.
- `su` missing or failing after `runuser` is rejected.
- chroot planner timeout or unreachable chrootctl path.
- direct-root fallback being selected because user switching could not be proven.

## Validation

Selected source-contract validation passed:

```text
99 passed, 0 failed, 0 skipped
```

Android Gradle compilation was not run in this container.
