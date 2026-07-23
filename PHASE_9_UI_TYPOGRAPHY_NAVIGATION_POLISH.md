# Phase 9 — UI Typography and Navigation Polish

This slice applies the post-HUD Phase 0 roadmap polish without reducing HUD detail.

## Goals

- Standardize desktop typography tokens into integer, named roles.
- Reduce fixed desktop header chrome and move release identity out of the daily header.
- Make the selected desktop sidebar destination easier to see at a glance.
- Keep desktop and Android navigation copy workflow-oriented: Logs and Repos instead of implementation-heavy wording.
- Compact Android page headers on phones while keeping roomy headers on wide layouts.
- Humanize Android Overview copy so readiness reads as user-facing state rather than service inventory.

## Desktop changes

- `Theme.qml` now exposes integer typography tokens for page titles, section titles, card titles, body, labels, captions, and metric values.
- `Main.qml` uses a shorter header and removes the static version chip from the main header.
- Desktop diagnostics navigation is labeled `Logs` while retaining the existing diagnostics page implementation.
- `SidebarButton.qml` adds a selected accent rail and keeps vector icons at a stable icon size in compact mode.
- Metric labels in Overview are sentence case instead of all-caps slabs.
- Section headings use the label token with zero extra tracking.

## Android changes

- `UiTypography.PAGE_TITLE` moves from 28sp to 24sp with a slightly tighter line height.
- Phone page headers use lower top/bottom padding, one-line subtitles, and hide the `DEVTOOL` overline outside wide layouts.
- Overview readiness now says `Ready to run` when all core checks pass.
- The Logs quick tile is labeled `Logs`, not `Structured logs`.
- Selected navigation buttons use stronger selected contrast and a higher selected surface alpha.

## Validation

Focused source tests cover the typography tokens, compact headers, desktop selected rail, Logs navigation copy, Android humanized copy, and updated typography expectations.
