# Devtool Android Frontend

A portrait-first GUI frontend and live HUD for devtool.

## Current model

- Exactly one active run is displayed at a time.
- New app-launched runs are rejected while the current run is active.
- Direct Termux runs replace the single live app state rather than creating concurrent cards.
- Completed runs remain in History.
- Abandoned nonterminal history records expire from active-run blocking after 12 hours.

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
