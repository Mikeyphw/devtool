# ACTIONS-07 — Cross-surface pipeline controls

The ACTIONS-06 journal is authoritative. ACTIONS-07 adds **typed command presets**
for `actions pipeline plan/run/status/resume` in desktop GTK and Android Live HUD,
plus a read-only normalized status projection in `devtool.actions_presentation`.
Interactive shell output renders a compact TUI-friendly graph; `--json` remains unchanged.

## Termux/TUI

`devtool -r "$HOME/Code/devtool" actions pipeline status --pipeline-id ID` prints
node-level status on a terminal. `--json` keeps the original machine contract.
`devtool.actions_presentation.project_pipeline(...)` is available for TUI/desktop/HUD
consumers and returns explicit waiting, blocked, uncertainty and nonqualification.
This does **not** install a background listener or replace native Textual screens.

## Desktop

The **Run** composer now offers `Pipeline · plan/dispatch/status/resume`.
Enter a repository-relative pipeline JSON for plan/dispatch or exact 32-hex pipeline
ID for status/resume. Review generates an argv array (not shell injection).
Remote mutations reset the approval checkbox when selected and require the user to
re-enable `--yes`. Desktop run history owns invocation.

## Android Live HUD

The **Run** operation dropdown exposes the same four modes with dedicated manifest
and pipeline ID fields. Existing Termux bridge executes the reviewed command, and
RunStore provides the ordinary progress/history transport. Explicit confirmation
is shown for dispatch/resume. No new embedded GitHub token handling is added.
The Android app itself must be built and installed to expose new options.

## Trust limitations

No UI assumes dispatch means success. No local/remote result is qualified for release
without the existing ACTIONS-05 artifact verification. A waiting result can be
resumed; failed/uncertain nodes require manual reconciliation, not blind retries.
ACTIONS-07 does not claim an end-to-end Android device build or GUI smoke test.
