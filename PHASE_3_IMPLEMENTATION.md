# Android Repositories Phase 3: Working Tree and Commits

## Implemented

- NUL-delimited Git porcelain v2 parsing with branch/upstream/ahead/behind metadata.
- Correct handling for spaces, untracked paths, rename source paths, deletions, submodules, and unmerged records.
- Mobile Changes view grouped into Conflicted, Renamed, Deleted, Staged, Modified, and Untracked sections.
- Working-tree, staged, and untracked-file diff views with selectable monospace output, wrapping, and copy.
- Owner-aware file stage, unstage, and tracked working-tree discard.
- Bulk stage-all and unstage-all operations.
- Per-repository mutation serialization and success/failure activity records.
- Latest 100 commits with HEAD, refs, authorship, timestamps, remote links, and copy actions.
- Commit detail view with stats/name status and correlated Devtool runs/artifacts.
- Repository Overview/Changes/Commits/Activity/Runs/Metadata horizontal mobile tabs.

## Safety boundaries

- Every file argument is validated as a repository-relative path and passed after `--`.
- Untracked files are never deleted by discard.
- Discard restores only the working-tree copy and preserves any staged version.
- Destructive tracked-file discard requires explicit confirmation showing the exact path.
- Git commands continue to execute as the Termux or chroot repository owner, not UID 0.
- Only one mutation is allowed per repository at a time.

## Deferred to Phase 4

Commit creation, checkout/create branch, fetch, fast-forward pull, push, stash/pop, and hard reset are not included in this phase.


## Validation

- 139 relevant Android, UI, and repository tests passed; the one deselected Compact HUD contract is an unrelated pre-existing desktop baseline failure.
- Phase 3 parser tests passed synthetic and real Git NUL streams, including spaces, renames, conflicts, and empty subjects.
- Core Git/database classes and all three Phase 3 activities compiled with `javac -Xlint:all -Werror` against Android API stubs.
- 59 Android XML files, 7 JSON files, 14 shell scripts, and Python bytecode compilation passed.
- A real APK build remains delegated to the configured `android_hud` target because this packaging environment does not contain the Android SDK/chroot toolchain.
