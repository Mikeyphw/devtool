# UI07/UI08 confirmation, notification, and terminal architecture

UI07 centralizes the policy that decides **when Devtool asks for confirmation** and **when a state change deserves user attention**. Renderers no longer invent destructive-action prompts or notification severity independently.

## Confirmations

`devtool.ui_core.confirmations` exposes a versioned policy with `never`, `smart`, and `always` modes. The built-in policy is intentionally conservative:

- cancellation: `smart`
- rollback/delete/snapshot overwrite: `always`
- retry/rerun: `never`
- artifact apply/manifest and dirty-tree continuation: `smart`

Configure it with `[ui.confirmations]`. Legacy `confirm_destructive` and `confirm_cancel` remain supported as compatibility shims. `UIActionState` carries `confirmation_mode`, `requires_confirmation`, and `confirmation_reason`, so TUI, GTK, Android and machine clients can present the same decision natively.

## Notifications

`devtool.ui_core.notifications` maps runtime events to semantic attention levels: `passive`, `info`, `important`, `critical`, and `success`. `[ui.notifications]` controls started/phase/success/warning/failure/requires-action delivery plus terminal bell policy. Runtime snapshots carry the resolved event, level, semantic token, emit decision, bell decision, and available action IDs.

Tasker/IPC state keeps all legacy notification fields and adds `attention`, `should_notify`, `bell`, `notification_token`, and `notification_actions`.

## Terminal migration

UI08 keeps Devtool's non-curses, scrollback-friendly terminal design. TUI navigation remains prompt driven, but live operation cards, hierarchical progress, structured diagnostics, status/icons/copy, and available actions now come from `UISnapshot`.

The terminal HUD uses the same snapshot and presentation preferences. Critical notifications may ring once when policy permits; completed-step visibility, icon mode, density, diagnostics, and max-step limits come from UI03/UI04 preferences.

TUI action keys are surface adapters over central action IDs. Cancellation uses the shared IPC helper, destructive actions consult central confirmation policy, and clipboard/log/workflow fallbacks stay terminal-native.
