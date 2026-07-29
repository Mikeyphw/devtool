# Phase 40: Repository Path Resolver + Repair UI

Phase 40 adds a safe metadata repair layer for Android repo-manager records. It detects path/environment mismatches before Git runs and offers explicit non-destructive repairs.

## What changed

- Added `RepositoryPathResolution` and safe `RepairAction` metadata.
- Added `RepositoryPathResolver` for namespace/path-family classification.
- Detects Termux paths saved as chroot/root repositories.
- Detects `/home/<user>/...` paths saved as Termux/root repositories.
- Detects root paths saved as native Termux repositories.
- Detects inconsistent `~`/`$HOME` expansion for the selected executor.
- Detects when a discovered Git root differs from the stored path.
- Added repository metadata repair through `RepositoryStore.applyPathRepair(...)`.
- Repair rekeys the repository record and linked runs/events/operations through the existing database rekey path.
- Added a Repository Path Resolver card to Overview and Metadata.
- Added confirmation dialogs that explicitly say repairs only change Android metadata and do not delete files, modify Git history, reset worktrees, or run Git.
- Added support-bundle `path_resolution` JSON for each repository.
- Added debug classification and debug events for path repair application.

## Safe repair actions

- Reclassify as Termux.
- Reclassify as chroot.
- Reclassify as root.
- Normalize Termux path to `~/...`.
- Use discovered Git root.
- Update stored namespace-visible path.

## Safety

Phase 40 does not execute Git for repair and does not perform destructive filesystem actions. Repairs only update Android repository metadata: environment, chroot name, Linux user, canonical path, host path and Git-root anchor.

## Validation

Selected source-contract validation passed:

```text
104 passed, 0 failed, 0 skipped
```

A pure Java smoke compile passed for the new resolver/model classes with lightweight stubs. Android Gradle compilation was not run in this container.
