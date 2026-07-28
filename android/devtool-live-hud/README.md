# Devtool Android Frontend

A portrait-first GUI frontend and live HUD for devtool.

## Current model

- Concurrent runs keep independent notification IDs and floating-HUD cards.
- Two or more active runs receive a grouped summary notification.
- Cancelling or dismissing one run does not remove the others.
- Completed runs remain in the structured Logs database.
- Authoritative Termux, chroot, bridge, and mirror logs are indexed incrementally through root.
- Saved and temporary live repositories are available from the Repos destination.
- Abandoned nonterminal run records expire after 12 hours.

## Integrations

- Rootless command execution uses Termux `RUN_COMMAND`.
- Live progress uses the authenticated `android-bridge` receiver.
- Tasker support is provided only through the native app plug-in.
- Optional root execution uses the non-Zygisk Devtool Root Bridge module.

The CLI no longer provides Tasker flags or a separate Termux notification mode. The app owns the live notification and floating overlay experience.

## Build

```bash
./build.sh
```

Or from the repository root:

```bash
./build-android-hud.sh
```

## Live operation pipeline

The active run card, floating overlay, and Android 16 progress notification render the operation sequence supplied by devtool. Extract, restore, build, test, artifact validation, commit, and rollback are represented independently, and warning/error totals update while the command is still running.

## Notification progress and foreground execution

The notification separates two kinds of progress:

- The progress bar is the overall devtool pipeline and never resets when the
  active phase changes.
- The text beside the active phase uses the exact producer counter, such as
  `14/17 projects` or `2/3 test assemblies`, matching the terminal output.

Expanded notifications show the active workspace, ordered pipeline phases,
live test totals, warning/error health, elapsed time, and context-sensitive
Open log, Copy summary/error, Open Termux, Show HUD, or Dismiss actions.

Commands started by the app explicitly request `--hud-mode android-bridge`.
The Run and Settings screens also provide **Open Termux in foreground while
running**. When foreground mode is disabled, the app records a persistent
execution log containing RunCommand acceptance, shell startup, bridge sequence
updates, watchdog warnings, and final result delivery. Open a run in History to
view or copy that log.

If a command remains on Preparing, the Termux shell sends an authenticated
startup acknowledgement immediately. After seven seconds without a real
producer update, the notification explains that the bridge has not connected
and directs the user to the execution log or visible Termux session.

## Debug and release bridge discovery

The local installer normally installs the debug application ID
`io.github.mikeceead.devtoolhud.debug`, while signed releases use
`io.github.mikeceead.devtoolhud`. Devtool probes both packages and always uses
the receiver's fully qualified Java class,
`io.github.mikeceead.devtoolhud.BridgeReceiver`, so desktop HUD flags translated
to `android-bridge` work from manually entered Termux commands as well as runs
started inside the app.

Installing or updating the bridge token now writes package and component marker
files beside `android-live-hud.token`. Advanced overrides are available through
`DEVTOOL_ANDROID_HUD_PACKAGE`, `DEVTOOL_ANDROID_HUD_COMPONENT`, and
`DEVTOOL_ANDROID_HUD_ACTION_PREFIX`. Bridge discovery failures are printed to
the invoking terminal and recorded in
`~/.local/state/devtool/android-hud-bridge-error.log`.


## Structured Logs and Repositories

Logs uses SQLite/FTS search and a root-backed incremental file indexer. The app reads Devtool XDG logs, Tasker/HUD JSONL, supported chroots, bridge logs, and the phone mirror without modifying source files. Each run opens in Timeline, Problems, Raw, and Metadata views.

Repos ports the desktop repository registry and Git surface with environment-aware Termux/chroot/root identities, working-tree and commit views, branches, guarded mutations, durable operation queues, and cross-linked structured Logs. Phase 6 adds tablet/foldable split panes, queue reordering, retention controls, and passive credential-remediation guidance. See `../../docs/ANDROID_LOGS_REPOSITORIES.md`.

## Staged Run and Logs workflow

Run is organized into Project, Operation, Tests, and Review stages with a fixed
bottom action bar. The visual builder includes all common lifecycle operations,
shows the exact command before launch, and confirms apply, restore, clean, and
commit actions.

The Tests stage can launch a `--list-tests` discovery run. Completed discovery
output is parsed into a searchable checklist while profiles, selectors, tags,
paths, classes, methods, and instrumentation remain available for manual use.

Logs supports full-text structured search plus status, project, and severity filters, compact run cards, exact-run details, command rerun, per-run deletion, and guarded clear-all. Deleting indexed records never deletes repository files or authoritative external logs.

Repositories now includes a mobile working-tree, commit-history, branch, stash, and remote synchronization surface. It parses porcelain v2 with NUL delimiters, preserves spaces and rename source paths, groups conflicts and file states, provides working/staged diffs, stages and unstages individual files or all changes, and only discards tracked working-tree changes after confirmation. Commit cards link indexed Devtool runs and artifacts. Phase 4 adds staged commits, validated branch creation and checkout, remote tracking checkout, fetch/prune/tags, fast-forward-only pull, non-force push with first-upstream setup, stash/pop including untracked files, and typed-confirmation tracked-only hard reset.

## TalkBack, touch targets, and large text

Interactive controls use at least 48 dp targets, bottom navigation uses at least
56 dp, visible field labels are associated with their inputs, and workflow,
history, progress, and overlay controls provide TalkBack descriptions. At large
system font sizes, action bars stack and progress pipelines use fewer segments
per row to avoid clipping. Hardware keyboard shortcuts and the TalkBack test
matrix are documented in `../../docs/UI_ACCESSIBILITY.md`.

## UI regression contract

Slice 4 adds deterministic terminal and SVG visual-state snapshots, explicit
concurrent-run and rollback-state tests, shared runner/status terminology checks,
and a 12,000-test catalog performance guard. Test catalog entries cache a
case-folded search key and scan each output field independently instead of
constructing a single oversized buffer. Completed rollbacks are announced as
**Changes rolled back**. See `../../docs/UI_REGRESSION.md`.


## 1.13.0 release qualification


Phase 6 adds adaptive navigation rails and split repository/log panes on wide screens,
large-text-safe action stacking, live queue timing and reordering, pinned-run retention,
configurable indexed-data cleanup, optional SQLite compaction, and reviewable credential
setup guidance. Remote checks remain passive, Git operations remain owner-aware, and
network commands remain non-interactive, bounded, fast-forward-only for pull, and
non-force for push.

Debug builds use the `.debug` application ID suffix and can coexist with a
release installation. Local JVM tests cover command generation/import, Termux
path normalization, and stable run identity. Connected instrumentation verifies
installation and `MainActivity` launch.

Release tasks require `DEVTOOL_RELEASE_STORE_FILE`,
`DEVTOOL_RELEASE_STORE_PASSWORD`, `DEVTOOL_RELEASE_KEY_ALIAS`, and
`DEVTOOL_RELEASE_KEY_PASSWORD`; no unsigned fallback is permitted. Run
`./gradlew lintRelease testReleaseUnitTest verifyReleaseApk` to assemble and
validate the release certificate with `apksigner`.

## Validation Workbench (1.15.1)

Run now includes a Validation subsection backed by Devtool's durable API-v1 sessions. It supports next, through-checkpoint, selected-with-dependencies, and all-remaining scopes; attempt history and evidence; safe pause/stop/retry/reset/finalize controls; and confirmed keep-changes or journaled rollback handling. Wide screens use a timeline/detail split pane.

Validation notifications reuse the normal custom layout with overall progress, previous/current/next phase context, state-specific controls, revision/attempt stale-action protection, and direct Workbench deep links. Rollback remains Workbench-only.
