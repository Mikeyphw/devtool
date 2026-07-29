# Phase 43: Debug Workbench UI

Phase 43 adds a central Android Debug Workbench cockpit on top of the diagnostics landed in phases 34-42.

## Scope

- Adds `DebugWorkbenchActivity` as an internal diagnostics screen.
- Adds `DebugWorkbenchHealth` to compose existing read-only health signals.
- Surfaces current health, run command trace, bridge roundtrip probe, repo doctor/path/queue diagnostics, validation health, recent debug events and support-bundle export.
- Adds Settings and Overview entry points.
- Adds support bundle `debug_workbench` summary.

## Safety

The workbench does not run Git, validation or repository mutation actions while rendering. It can launch only the already-safe bridge roundtrip probe. Repository actions remain behind their existing detail-screen confirmations.

## Validation

Source-contract validation passed for Phase 43 plus the prior Android debug/repository/validation contracts.
