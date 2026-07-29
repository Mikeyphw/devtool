# Changelog

## Phase 42: Repo Queue + Orphan Recovery

- Added `RepoQueueRecovery` queue-health diagnostics for queued, running, stale and orphaned Git operations.
- Added explicit safe recovery actions for terminating orphaned tagged processes and marking detached work interrupted.
- Added retry helpers for terminal Git operations with current metadata or after path repair, while refusing missing-payload commit/stash retries.
- Added Repo queue recovery cards in Repository Detail Sync and Metadata.
- Added support bundle `repo_queue_health` diagnostics and debug classifier keys.
- Added Phase 42 source contracts.

## Phase 41: Git Operation Trace

- Added `GitOperationTrace` as a durable repo-operation timeline for queued, started, execution-plan, path, Git binary, command-start, output, finish and failure milestones.
- Added executor preflight checks for repository path, Git binary availability and Git worktree status before mutating Git commands run.
- Added trace evidence to repository Activity events, debug events, synthetic structured logs and support bundles without writing trace lines to Git stdout/stderr.
- Added Copy Git trace actions for active and completed repository operations.
- Added debug classifier keys for Git trace milestones and before-Git failures.
- Added Phase 41 source contracts.

## Phase 40: Repository Path Resolver + Repair UI

- Added `RepositoryPathResolver` and `RepositoryPathResolution` for safe metadata-only repairs.
- Added path resolver cards in Repository Detail Overview and Metadata.
- Added support bundle path-resolution evidence per repository.

## Phase 39: Root/chroot Executor Planner

- Added shell and user-switch planning for zsh/bash/sh and runuser/su/direct-root fallbacks.

## Phase 38: Repo Manager Doctor

- Added repo doctor diagnostics for path namespace, shell, Git and user-switch failures.

## Phase 37: Bridge Roundtrip Probe

- Added authenticated bridge roundtrip diagnostics.

## Phase 36: Termux Wrapper Sentinels

- Added shell-side wrapper checkpoints for Termux launches.

## Phase 35: Run Command Trace

- Added run launch timeline checkpoints.

## Phase 34: Android Debug Recorder

- Added durable debug events.

## Phase 33: Validation Reporting Release Seal

- Documented the finalization integrity and Android toolchain gate for immutable validation reports.

## Phase 32: Validation Reporting Release Seal

- Documented immutable validation report behavior, original and final Git heads, distinct validation history cards and 16 mandatory failure scenarios.
