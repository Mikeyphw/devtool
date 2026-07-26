# UI regression review

This pass reviewed the five failures that could plausibly indicate product regressions.

## Product changes

- Android Run now labels stage 3 as **Test selection**, making the catalog/search/selectors purpose explicit.
- Desktop Run now clearly states that execution opens in the preferred external terminal and that the Control Center continues tracking progress.

## Confirmed current behavior

- Desktop Overview is already action-first in rendered source order. The old test accidentally matched the `Latest run` helper string declared above the UI tree.
- Artifacts correctly derives narrow mode from `AppScrollView.availableWidth`, which reflects usable content width more accurately than the outer item width.
- Compact HUD intentionally uses explicit right-edge, bottom-edge, and corner drag resizing with double-click reset. This replaced platform-dependent `startSystemResize()` behavior in Phase 20.

## Test maintenance

The remaining stale tests now assert current scoped restore steps, dynamic workflow stages, dynamic Android step counts, current command-review wording, header terminology, canonical version consistency, and the UI/UX release gate.
