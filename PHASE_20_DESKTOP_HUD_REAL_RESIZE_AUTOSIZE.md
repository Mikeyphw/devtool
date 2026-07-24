# Phase 20: Desktop HUD real resize and autosize

This phase fixes the remaining desktop HUD usability defects after the earlier UI/HUD polish stack.

## Fixes

- Replaces platform-dependent `startSystemResize()` resize-only hooks with explicit QML drag resizing for the right edge, bottom edge, and bottom-right grip.
- Adds a visible resize grip affordance.
- Disables auto-size while the user manually sizes the HUD.
- Adds double-click reset on the resize grip/edges to return to content-aware auto-size.
- Reworks auto-size to use the actual header and lower-content implicit heights instead of fixed lower-section heuristics.
- Adds vertical content height bindings and interactive scrollbars so capped HUD content remains reachable when content is taller than the screen-safe maximum.
- Preserves detailed HUD content; no lanes, parser diagnostics, metrics, status lines, or actions are removed.

## Validation

The focused contract test verifies manual resize hooks, content-driven auto-size hooks, scrollable capped content, and manual-size reset behavior.
