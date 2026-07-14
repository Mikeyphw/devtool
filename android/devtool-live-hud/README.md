# Devtool Android Frontend

A portrait-first GUI frontend and live HUD for devtool.

## Current model

- Concurrent runs keep independent notification IDs and floating-HUD cards.
- Two or more active runs receive a grouped summary notification.
- Cancelling or dismissing one run does not remove the others.
- Completed runs remain in History.
- Abandoned nonterminal history records expire after 12 hours.

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


## Staged Run and History workflow

Run is organized into Project, Operation, Tests, and Review stages with a fixed
bottom action bar. The visual builder includes all common lifecycle operations,
shows the exact command before launch, and confirms apply, restore, clean, and
commit actions.

The Tests stage can launch a `--list-tests` discovery run. Completed discovery
output is parsed into a searchable checklist while profiles, selectors, tags,
paths, classes, methods, and instrumentation remain available for manual use.

History supports text and status filtering, compact run cards, details, command
copy/rerun, per-run deletion, and guarded clear-all. Deleting history never
deletes repository files.

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


## 1.7.2 release qualification

Debug builds use the `.debug` application ID suffix and can coexist with a
release installation. Local JVM tests cover command generation/import, Termux
path normalization, and stable run identity. Connected instrumentation verifies
installation and `MainActivity` launch.

Release tasks require `DEVTOOL_RELEASE_STORE_FILE`,
`DEVTOOL_RELEASE_STORE_PASSWORD`, `DEVTOOL_RELEASE_KEY_ALIAS`, and
`DEVTOOL_RELEASE_KEY_PASSWORD`; no unsigned fallback is permitted. Run
`./gradlew lintRelease testReleaseUnitTest verifyReleaseApk` to assemble and
validate the release certificate with `apksigner`.
