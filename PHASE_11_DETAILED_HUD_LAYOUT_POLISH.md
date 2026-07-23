# Phase 11: Detailed HUD Layout Polish

This phase applies the roadmap slice after the Overview/Run-flow polish pass. It keeps the HUDs detailed by default and improves scan order, persistence, and bounds behavior.

## Desktop HUD

- Keeps rich HUD content visible: current run, progress, shared pipeline chips, workspace lanes, lane metrics, parser diagnostics, runner and execution context, and status details.
- Adds a clear `Current run` label above the primary task title so the main status wins before supporting details.
- Adds icon + label status wording in the primary status line rather than relying on color alone.
- Adds a `Workspace lanes` section heading and visible lane count before the lane list.
- Makes inactive workspace lanes visually quieter while keeping them present and readable.
- Renames the parser block to `Diagnostics parser` so parser metrics are grouped instead of competing with the main status.

## Android floating HUD

- Persists the floating HUD `x/y` position in `overlay_hud` preferences.
- Restores the saved position on service start and clamps it to the current display.
- Clamps drag movement during move events, then saves position on drag end or cancel.
- Reclamps and recalculates HUD width after configuration changes such as rotation or font-scale changes.
- Keeps detailed run cards: title, status lead/detail, progress, runner/environment, pipeline steps, metrics, tests, diagnostics, and commit.
- Adds `Current run` / `Recent run`, `Pipeline`, and `Metrics` headings so detailed information has readable scan regions.
- Makes non-primary cards quieter without hiding them.

## Non-goals

- No reduction of HUD detail.
- No notification layout changes.
- No command-builder behavior changes.
- No repository or runner behavior changes.
