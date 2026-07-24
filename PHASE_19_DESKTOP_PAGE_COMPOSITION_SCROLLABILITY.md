# Phase 19: Desktop page composition and scrollability

This phase merges the desktop page scrollability fix with the visible composition issues found in the Control Center screenshots.

## Fixes

- Desktop scroll pages now bind their content height to the real page layout so vertical overflow becomes scrollable.
- The shared `AppScrollView` exposes an interactive vertical scrollbar.
- Overview pipeline steps wrap instead of clipping horizontally.
- The Logs page title now matches the navigation label and gets a success empty state.
- Run clipboard commands are review-first and warn when the command targets a different repository than the current form.
- Run project disclosure rows use clear chevron affordance and the first step now includes a context card.
- Artifacts and Repositories empty states are action-first instead of passive oversized panels.
- History rows are calmer and less alert-card-like.
- Settings no longer hardcodes Kitty copy when another terminal is preferred.
- The header emphasizes the selected repository instead of repeating the full application title.

## Runtime policy

No CLI validation semantics changed. This is a desktop Control Center layout and safety polish pass only.
