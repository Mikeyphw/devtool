# Phase 13: UI contract finalization

Phase 13 is the closing documentation and validation slice for the Devtool UI/UX roadmap. It intentionally makes no runtime UI behavior changes. Its job is to lock in the accepted behavior from the five applied UI overlays so future cleanup does not quietly unwind them.

## Covered stack

- Phase 8: Android notification status-line parity and desktop HUD content-aware auto-size.
- Phase 9: typography and navigation polish.
- Phase 10: Overview action-first layout and Run progressive disclosure.
- Phase 11: detailed HUD layout polish and Android floating HUD position persistence.
- Phase 12: notification progress/state/accessibility polish.

## Final contract

- Desktop HUD remains detailed and content-aware. It auto-sizes to rich content, clamps to screen bounds, preserves manual resizing, and keeps workspace lanes, parser diagnostics, runner/environment details, phase progress, and metrics visible.
- Android floating HUD remains detailed, remembers its position, clamps drag/restored positions to the current display, and reclamps after configuration changes.
- Android notifications show status lead/detail below the progress rail on compact, expanded, and large-text layouts. Progress rails are prominent, state is not color-only, and the approved three-action model remains intact.
- Desktop and Android navigation use the human-facing Logs label while retaining diagnostics implementation names internally where necessary.
- Overview pages prioritize next action and latest run before secondary readiness/metrics sections.
- Run pages expose a short default path first and keep advanced/danger controls available through progressive disclosure.
- Typography uses semantic roles and sentence-case labels; uppercase remains reserved for compact state badges.

## Validation

`tests/test_ui_contract_finalization.py` checks the final contract across source, layouts, JSON contracts, release gates, and documentation. The embedded overlay validator runs source-audit plus the focused finalization test so this docs/contract slice is not rolled back by unrelated baseline build issues.
