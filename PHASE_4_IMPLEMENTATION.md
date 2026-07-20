# Android Repositories Phase 4: Full Git Parity

## Implemented

- Commit staged changes with bounded multi-line messages while preserving repository hooks and signing configuration.
- Fetch all remotes with prune and tags.
- Pull only through `--ff-only`; merge commits and rebases are never created by the Android app.
- Push without force. When the current branch has no upstream, the app selects `origin` or the first configured remote and uses `--set-upstream`.
- Enumerate local and remote branches through NUL-delimited `for-each-ref` output.
- Checkout local branches and create local tracking branches from remote branches.
- Create branches after both app-side validation and `git check-ref-format --branch` validation.
- Enumerate stashes with stable numeric `stash@{n}` references.
- Stash tracked, staged, and untracked files with an optional message; ignored files remain untouched.
- Pop a selected stash after validating that the reference still exists.
- Hard reset staged and unstaged tracked changes to `HEAD` only after the user types `DISCARD`; untracked files are never deleted.
- Add a Branches tab and synchronization controls to the Android repository detail surface.
- Record every operation as a repository activity success or failure event.

## Safety and execution model

- Git still executes as the Termux or chroot repository owner rather than UID 0.
- One read/mutation workflow is active per repository at a time.
- Network operations use bounded timeouts and `GIT_TERMINAL_PROMPT=0`.
- Pull is fast-forward-only and push never includes force flags.
- User-controlled paths, branch names, commit messages, and stash references are validated and shell-quoted.
- Activity summaries redact HTTPS user-info credentials and authorization-header values.
- Hard reset does not invoke `git clean`, so untracked files survive.

## Validation

- Phase 2, Phase 3, Phase 4, Android source, release-version, and UI contracts pass.
- Real Git-generated NUL branch and stash streams are consumed by JVM parser tests.
- Modified core and UI classes compile with `javac -Xlint:all -Werror` against Android API stubs.
- The apply artifact uses Devtool schema v2, an Android HUD target, atomic validation, rollback, and an embedded single-commit policy.
