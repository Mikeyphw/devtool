# devtool 0.3.4

## HUD operation pipeline

- Replaced the desktop HUD's single progress bar and textual `Validate → Apply → Test → Commit` line with a structured sequence of per-operation progress segments.
- Apply workflows show Extract, Restore, Build, Test, Commit, plus external Validate and Rollback segments when those phases occur.
- Every segment tracks pending, running, complete, failed, or skipped status independently.
- Compact mode is centered on the pipeline and live Errors/Warnings/active-step metrics; buttons remain visible.
- Desktop warning/error totals now aggregate the full current run and update as diagnostic events arrive.
- Duplicate diagnostics and generic final failure events do not inflate live counts.

## Android HUD

- Added the same structured step payload to the authenticated Android bridge.
- The app active-run card and floating overlay now render separate operation segments.
- Android 16 progress notifications use dynamic segments instead of a hardcoded restore/build/test split.
- Android run history persists step state and live warning/error totals.
- Android frontend version is now 1.5.3 (version code 10).
