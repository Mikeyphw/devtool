# Devtool 0.2.27 changes

## Android progress reliability

- Fixes app-launched background runs remaining on “Waiting for devtool progress”.
- Gives the Termux launcher and devtool progress sink the same stable `DEVTOOL_RUN_ID`, so the initial card, notification, overlay, and final result update one run instead of creating two unrelated runs.
- Injects the app bridge token into the launched Termux environment, avoiding token-file lookup failures for app and Tasker launches.
- Direct Termux `android-bridge` runs now automatically start the native Android notification renderer instead of only writing bridge JSON and waiting for a separately started daemon.
- Preserves rich devtool completion details when Termux later returns its generic command exit bundle.

## Android frontend 1.3.0

- Reworks the companion into a four-page devtool frontend: Overview, Run, History, and Settings.
- Adds a visual command builder for `apply-overlay`, `apply-overlays`, `validate`, `build`, `test`, `restore`, `status`, `doctor`, `targets`, `history`, `commit`, and custom operations.
- Supports direct Termux execution or the `androidos` chroot runner.
- Exposes repository, target, artifact, commit, validation, copy, non-interactive, live-progress, and floating-HUD controls.
- Shows a live command preview before execution.
- Persists recent runs, progress, command, output, error text, timestamps, and rerun actions.
- Adds quick status and validation actions from the Overview page.
- Keeps the raw-command launcher as an advanced settings option.
- Retains Tasker plug-in support, Monet colors, GTK palette/icon, notifications, and floating overlay HUD.

## Validation

- Python regression suite expanded to 224 tests.
- Java source parser validation covers all Android Java files.
- Android XML resources and manifests are parsed during packaging checks.
