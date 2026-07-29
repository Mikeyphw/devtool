# Changelog

## Phase 40: Repository Path Resolver + Repair UI

- Added `RepositoryPathResolver` and `RepositoryPathResolution`.
- Added safe repair actions for Termux/chroot/root path namespace mismatches.
- Added repository metadata repair through `RepositoryStore.applyPathRepair(...)`.
- Added a Repository Path Resolver card to repository Overview and Metadata.
- Added confirmation copy clarifying repairs do not delete files, modify Git history, reset worktrees or run Git.
- Added support-bundle `path_resolution` evidence per repository.
- Added debug classification and events for path repairs.
- Added Phase 40 source contracts.
