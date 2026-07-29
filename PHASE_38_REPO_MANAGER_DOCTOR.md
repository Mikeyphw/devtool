# Phase 38: Repo Manager Doctor

Phase 38 adds a first-pass repository diagnostics layer for the Android app. It targets failures where the repository manager reports missing paths, wrong namespace paths, missing Git/shell support, or `runuser` errors before Git actually starts.

## What changed

- Added `RepositoryDoctor` with a read-only probe runner.
- Added `RepositoryDoctorResult` with issue classification, summaries, recommendations and copyable plain-text output.
- Added a separate `RootCommandBroker.repositoryDoctor(...)` path so chroot diagnostics can inspect the chroot before the normal repo executor attempts user switching.
- Added `chrootDoctorShell(...)`, which enters the configured chroot with `/bin/sh -c` and probes `runuser`, `su`, `git`, shell availability and repo path state.
- Added a `Repo Manager Doctor` card to repository overview and metadata screens.
- Added `Run Doctor` and `Copy diagnosis` actions.
- Added `repository_doctor` evidence to support bundles for each repository.
- Extended `DebugIssueClassifier` with repo-doctor issue keys.

## Covered diagnoses

- Repository path exists in the wrong namespace.
- Repository path missing in the selected executor namespace.
- Directory exists but is not a Git root.
- Git missing in Termux/chroot/root environment.
- No usable shell reported.
- Chroot user missing.
- `runuser` present but unable to switch to the configured user before Git starts.
- No working `runuser`/`su` user-switch path.
- `git -C` fails even though Git and the path are present.

## Safety

The probe is read-only. It checks command availability, path state and Git inspection with `git status --short`; it does not mutate repositories, branches, remotes or files.

## Validation

Selected source-contract validation passed:

```text
95 passed, 0 failed, 0 skipped
```

Android Gradle compilation was not run in this container.
