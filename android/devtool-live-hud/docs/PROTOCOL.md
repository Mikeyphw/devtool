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
