# Phase 12: Notification Accessibility Polish

This slice finishes the Android notification readability pass after the HUD parity and detailed HUD overlays.

## Changes

- Increased compact, expanded, and large-text notification progress rails to 7dp.
- Promoted the state label into a dominant chip-style non-color status marker.
- Added RemoteViews content descriptions for state, progress, status lead/detail, phase rows, workspace card, elapsed metric, and actions.
- Kept status lead/detail below the progress rail on all notification surfaces.
- Preserved the approved three-action model and 48dp notification action targets.
- Updated `notification-ui-contract.json` with visual-prominence and accessibility requirements.
- Added focused tests for progress prominence, state-label hierarchy, touch targets, and accessibility bindings.

## Non-goals

- No HUD detail reduction.
- No notification action expansion beyond three actions.
- No command-builder or validation behavior changes.
