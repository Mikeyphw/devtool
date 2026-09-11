# Devtool UI/UX contract

This contract captures the accepted end state of the UI/UX polish roadmap for the desktop Control Center, desktop HUD, Android app, Android floating HUD, and Android notification.

## Shared semantic UI contract

UI01-UI06 define a renderer-independent semantic layer under `devtool.ui_core`. The domain/runtime remains authoritative for facts; UI Core converts those facts into a versioned `UISnapshot`, shared action semantics, and shared navigation semantics; each frontend remains responsible for native widgets and responsive layout.

The shared snapshot covers workspace/selection, operation lifecycle, hierarchical progress, diagnostics, available semantic actions, navigation, resolved presentation preferences, and semantic tokens. Action and navigation IDs are stable machine-facing identifiers; labels, short labels, notification labels, icon tokens, routes, and shortcuts are presentation metadata. Frontends must not duplicate business meaning simply because their layouts differ.

`devtool ui-contract --surface machine|desktop|tui|android|hud --json` is the supported inspection surface for profiles, preferences, capabilities, responsive intent, progress/diagnostic schemas, semantic tokens, icons, copy, actions, and navigation. `--width`/`--height` preview responsive intent and repeatable `--require-capability` shows explicit fallback negotiation. Additive frontend-specific state may coexist with the semantic snapshot during migration, but it must not redefine action or navigation meaning.

## Shared presentation policy

UI preferences resolve centrally from defaults -> profile -> surface defaults -> merged global/repository `[ui]` -> surface override -> environment -> per-run CLI override. Built-in profiles are `minimal`, `standard`, `developer`, and `debug`; custom inherited profiles are validated for cycles and invalid values.

Semantic status/severity/action tokens express meaning but do not prescribe literal colors. The icon registry provides Nerd Font, Unicode, ASCII, and native symbolic fallbacks. Common action, navigation, and status wording comes from the shared copy registry while stable machine IDs remain unchanged.

A renderer may adapt layout or choose a shorter registered copy variant, but it must not derive business meaning from a color, glyph, or display string.


## Capability, responsive, progress, and diagnostic policy

- Frontends declare capabilities; domain/runtime code must not grow `if gtk`, `if android`, or `if tui` branches to decide semantic behavior.
- Missing capabilities have named fallbacks (for example tree -> indented list, dialog -> inline confirmation, notification -> status event).
- Responsive policy communicates layout intent (`single-pane`, `two-pane`, `three-pane`) while native renderers own widgets and physical layout.
- Progress is normalized as Operation -> Workflow -> Target -> Phase/Step with a compatibility flat list retained for existing consumers.
- Diagnostics expose What happened, Why, Where, What you can do, and Technical details, plus stable semantic action IDs.
- Live Tasker/IPC/HUD state carries this enriched semantic snapshot across process boundaries; renderers do not re-parse logs to rediscover hierarchy or error meaning.

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

## UI07 confirmation and notification policy

Confirmation semantics are centralized in `ui_core.confirmations`; notification attention/delivery semantics are centralized in `ui_core.notifications`. Frontends must consume `requires_confirmation`/`confirmation_mode` from action state rather than creating their own destructive-action rules. Notifications use semantic attention levels (`passive`, `info`, `important`, `critical`, `success`) and carry an explicit `should_emit` and `bell` decision.

Repository/global configuration may define `[ui.confirmations]` and `[ui.notifications]`. `ui-contract --json` exposes both schemas and their resolved policy.

## UI08 terminal contract

TUI and terminal HUD consume the same `UISnapshot` as other frontends. Existing navigation shortcuts and legacy HUD state remain compatible, while live terminal rendering now derives status labels/icons, progress hierarchy, diagnostics, available actions, confirmation markers, and notification bells from UI Core.

## UI09/UI10 renderer contract

UI09/UI10 complete GTK/Desktop and Android Live HUD migration onto UI Core. Renderers consume semantic actions/navigation, presentation, capabilities/responsive intent, hierarchical progress, structured diagnostics and notification policy. Platform widgets/layout remain native, and legacy machine/bridge fields remain compatibility fallbacks. See `docs/architecture/ui-desktop-android-migration.md`.
