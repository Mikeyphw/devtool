# Devtool Live HUD protocols

## Token-protected progress bridge

Component:

```text
io.github.mikeceead.devtoolhud/.BridgeReceiver
```

Actions:

```text
io.github.mikeceead.devtoolhud.UPDATE
io.github.mikeceead.devtoolhud.COMPLETE
io.github.mikeceead.devtoolhud.FAIL
io.github.mikeceead.devtoolhud.CANCEL
```

Required extras:

- `token` (`String`)
- `run_id` (`String`)

Progress extras:

- `status`: `running`, `waiting`, `ok`, `failed`, `interrupted`, `cancelled`, `skipped`
- `phase`, `detail`, `repo`, `target`, `commit`, `summary`
- `progress`, `current`, `total`, `passed`, `failed`, `skipped`, `warnings`, `errors`
- `started_at_ms`, `duration_ms`
- `indeterminate`
- `overlay`

Notifications are always posted. `overlay=true` asks for overlay presentation but does not grant permission or bypass Android foreground-service rules.

## Tasker/Locale plug-in

Edit action:

```text
com.twofortyfouram.locale.intent.action.EDIT_SETTING
```

Fire action:

```text
com.twofortyfouram.locale.intent.action.FIRE_SETTING
```

Configuration bundle:

```text
com.twofortyfouram.locale.intent.extra.BUNDLE
```

Configuration keys:

- `command`
- `workdir`
- `background`
- `overlay`

The configuration activity accepts only Tasker’s known package identities and stores a private per-install authorization token in the action bundle. The fire receiver verifies that token before it may delegate the app’s Termux permission. This keeps the necessarily exported Locale receiver from becoming a generic command-execution endpoint.

For an ordered broadcast, the receiver returns `RESULT_CODE_PENDING`, Termux runs asynchronously, and `TermuxResultService` sends the Tasker completion intent with the final result code and variable bundle. Immediate validation or launch failures are returned directly through the ordered broadcast.

## Termux RUN_COMMAND

Service:

```text
com.termux/com.termux.app.RunCommandService
```

Permission:

```text
com.termux.permission.RUN_COMMAND
```

The app supplies command path, arguments, workdir, background mode and a unique one-shot mutable pending intent. Returned Termux bundle keys consumed by the app are:

```text
result
stdout
stderr
exitCode
err
errmsg
```

## Validation control bridge

The Validation Workbench invokes Devtool's API-v1 JSON commands through the same execution owner that created the session. Root-owned sessions use the root bridge; Termux-owned sessions use `RUN_COMMAND` utility execution.

Every mutation carries:

- `session_id`
- `if_revision`
- active `attempt_id` when the notification action targets a running attempt
- repository identity cached by the app

The app persists full status snapshots in `validation_sessions`, `validation_phases`, and `validation_attempts`. Mutation acknowledgements may be sparse; the app retains the previous complete phase snapshot and immediately requests authoritative status.

Supported Workbench controls include scoped run, pause after phase, cancel pending pause, stop now, resume, retry, safe-profile retry, phase/reset-from/reset-all, finalize, recover, keep changes, and explicit rollback.

### Phase 31.1 identity, ordering, and evidence rules

Read-only status and plan calls may repair metadata for a migrated cache entry. Every mutation must match the session's structured executor identity and repository identity. Chroot sessions retain the chroot name, Linux user, working directory, and root-bridge transport; they are never silently rerouted through native Termux.

The SQLite store rejects lower revisions and also rejects older authoritative timestamps at the same revision. Phase and attempt rows are upserted by stable keys; completed attempt evidence is never deleted merely because a sparse response omitted it. Status polling includes durable events, so numeric phase progress and rollback journal progress can update without advancing the control revision.

The Workbench evidence activity renders cached commands, log paths, diagnostics, classifier evidence, supervisor events, input snapshots, output tails, and attempt history. It never reads arbitrary root/chroot files directly; copy actions preserve the authoritative path for use in the owning environment.
