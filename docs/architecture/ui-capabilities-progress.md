# UI05/UI06 capability, responsive, progress, and diagnostics architecture

UI05 and UI06 extend the shared UI Core without creating a universal widget toolkit.

## Capability contract

`devtool.ui_core.capabilities` defines one versioned capability matrix for machine, desktop, TUI, HUD, and Android surfaces. Consumers can call `negotiate_capabilities(surface, requested)` to receive supported/missing capabilities and named fallbacks.

## Responsive contract

`devtool.ui_core.responsive` resolves surface + viewport + UI preferences into semantic layout intent. TUI uses single/two/three-pane intent at narrow/medium/wide widths, desktop uses stacked/two/three-pane intent, Android uses phone/two-pane/tablet intent, and HUD remains intentionally compact. Exact native widget composition stays with each frontend.

## Progress contract

The compatibility `progress` list is retained. The new `progress_tree` normalizes Operation -> Workflow -> Target -> Phase/Step and preserves explicit dependency parent links. `progress_summary` exposes total/completed/running/pending/failed/blocked/skipped/cancelled counts plus overall percent and active IDs.

## Diagnostics contract

The compatibility diagnostics list is enriched with stable IDs, normalized location, cause, related workflow node, next semantic actions, technical details, and evidence. `diagnostic_presentations` maps those facts into renderer-independent What happened / Why / Where / What you can do / Technical details sections.

## Inspection

```bash
devtool ui-contract --surface tui --width 55 --require-capability notifications --json
devtool ui-contract --surface desktop --width 1500 --json
devtool ui-contract --surface android --width 420 --json
```
