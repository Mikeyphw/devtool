# Android Logs and Repositories: Implemented Phase 0–2

## Delivered foundation

- SQLite/WAL database replacing the bounded SharedPreferences run array.
- One-time migration of existing Android run history.
- Structured log entries with FTS4 search.
- Root log-source registry and inode/offset file cursors.
- Environment-aware repository identities and transactional Git-root rekeying.
- Run, repository activity, artifact, commit, transaction-backup, and authoritative-log correlation.
- Central bounded root command broker with Termux UID and chroot-owner execution.

## Delivered structured Logs

- Root-backed incremental indexing of Termux XDG logs, Tasker/HUD JSONL, supported chroots, bridge logs, and the phone mirror.
- Rotation, truncation, partial-line, rewritten-JSONL, and bounded-read handling.
- Zero-error-safe severity classification and project/tool/phase normalization.
- Search plus status, project type, and severity filters.
- Exact-run Timeline, Problems, Raw, and Metadata viewer.
- Notification and `devtool://logs/run/<id>` deep links.
- Direct run-to-repository navigation.

## Delivered Repositories port

- Fifth Android navigation destination: Repos.
- Saved repository registry and temporary live repositories from active Devtool runs.
- Root-aware Termux, chroot, and root picker/discovery.
- Read-only Git status, branch, upstream, remote, ahead/behind, canonical root, and project inspection.
- Overview, Activity, Runs, and Metadata views.
- Use in Run, refresh, copy path, rooted browse, and repository-owner-aware shell actions.
- Search plus saved, modified, clean, live, missing, Termux, and chroot filters.

## Intentionally deferred

This slice does not mutate repositories. Changed-file/diff inspection, commit history, stage/unstage, commit, checkout, fetch, pull, push, stash, and reset belong to the next phases.

## Validation performed

- 150 Android/UI source and behavior contracts passed; one pre-existing Compact HUD contract was excluded after reproducing it in the pristine baseline.
- The complete new Java foundation and UI activity surface compiled with Android API stubs.
- All Android Java sources parsed without syntax diagnostics.
- Root source-list shell generation and zero-count classifier checks passed.
- Python compileall passed.
- 14 shell scripts passed `bash -n`.
- 59 Android XML files and 7 JSON files parsed successfully.

A real APK was not assembled in this environment because the Android SDK/chroot build toolchain was unavailable here. Devtool validation is embedded in the overlay and will run against the configured `android_hud` target when applied.
