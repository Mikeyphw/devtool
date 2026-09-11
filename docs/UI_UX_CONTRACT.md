# Devtool UI/UX contract

This contract captures the accepted end state of the UI/UX polish roadmap for the desktop Control Center, desktop HUD, Android app, Android floating HUD, and Android notification.

## Shared semantic UI contract

UI01/UI02 define a renderer-independent semantic layer under `devtool.ui_core`. The domain/runtime remains authoritative for facts; UI Core converts those facts into a versioned `UISnapshot`, shared action semantics, and shared navigation semantics; each frontend remains responsible for native widgets and responsive layout.

The shared snapshot covers workspace/selection, operation lifecycle, hierarchical progress, diagnostics, available semantic actions, and navigation. Action and navigation IDs are stable machine-facing identifiers; labels, short labels, notification labels, icon tokens, routes, and shortcuts are presentation metadata. Frontends must not duplicate business meaning simply because their layouts differ.

`devtool ui-contract --surface machine|desktop|tui|android|hud --json` is the supported inspection surface for the current shared action/navigation schema. Additive frontend-specific state may coexist with the semantic snapshot during migration, but it must not redefine action or navigation meaning.

## Shared principles

- Preserve operational detail where users already rely on it, especially in HUDs.
- Centralize semantic state, action meaning, and navigation identity while keeping renderer-native widgets and layout.
- Improve hierarchy before removing information.
- Keep the default path short while leaving power-user controls available.
- Prefer human workflow labels over implementation labels.
- Use semantic typography roles, sentence-case section labels, and uppercase only for compact state badges.
- Every interactive Android target must remain at least 48 dp where platform space permits.

## Desktop Control Center

The desktop shell is workflow-first:

- Primary navigation keeps Run, Live/Overview, Artifacts, Logs, History, Repositories, and Settings discoverable.
- The sidebar uses a visible selected marker, not color alone.
- The fixed daily header stays compact and does not reserve space for version/build trivia.
- Overview leads with the next action and latest run before snapshot metrics.
- Quick actions and metrics wrap responsively instead of crowding narrow windows.
- Run uses progressive disclosure: Basic first, Advanced controls collapsed, and dangerous bypass options separated.
- No-test operations use a shorter stepper that skips the Tests stage.

## Desktop HUD

The desktop HUD is detailed by design. It must not be reduced to a minimal widget.

Required behavior:

- Content-aware auto-size is enabled by default.
- Auto-size reacts to current run, workspace lanes, parser/detail expansion, and content height changes.
- Resizing clamps the HUD inside available screen bounds.
- Manual resize remains available, and the user can reset back to auto-size.
- The HUD keeps current run, workspace lanes, pipeline, parser diagnostics, runner/environment details, metrics, rollback/recovered states, copy/log/details actions, and multi-workspace visibility.
- Main status is presented with icon plus label so state is not color-only.

## Android app

The Android app is action-first and thumb-friendly:

- Main navigation uses Overview, Run, Logs, Repos, and Settings.
- Phone headers are compact; large overline branding is reserved for wider layouts.
- Overview leads with Next action, then Latest run, then Readiness and advanced setup.
- Run keeps Project, Operation, Target/artifact, Validate, Commit, and Run visible first.
- Chroot/workdir/environment args/selectors/instrumentation/output/HUD internals live under advanced sections.
- Danger-zone controls remain visually separated.
- Touch targets remain at least 48 dp for important actions.

## Android floating HUD

The Android floating HUD remains detailed, but it must behave like a proper overlay:

- Position is saved after drag end or cancel.
- Position is restored on service start.
- Restored and dragged positions are clamped to display bounds.
- Rotation and font-scale changes trigger reclamping.
- Current run is visually dominant; recent runs remain visible but quieter.
- Pipeline, status lead/detail, metrics, runner/environment, tests, warnings/errors, commit, and per-step information remain visible.

## Android notification

The notification is a compact mirror of live run state:

- Compact, expanded, and large-text layouts show status lead/detail below the progress rail.
- Progress rails are 7 dp tall.
- State appears as a dominant chip-style label and does not rely on color alone.
- The notification keeps exactly three actions for each state.
- Expanded and large-text action targets remain at least 48 dp tall.
- RemoteViews content descriptions cover state, progress, status lines, phase rows, workspace card, elapsed time, and actions.
- The JSON notification UI contract must stay in sync with the source and layouts.

## Regression gates

The finalization test suite must protect:

- HUD detail preservation.
- Desktop HUD content-aware auto-size and screen clamping.
- Android HUD saved position, drag bounds, rotation bounds, and detailed layout grouping.
- Notification status-line parity, 7 dp progress rails, dominant non-color state label, three-action model, and accessibility descriptions.
- Typography/navigation polish, including Logs copy and compact Android page title.
- Overview action-first layout and Run progressive disclosure.
