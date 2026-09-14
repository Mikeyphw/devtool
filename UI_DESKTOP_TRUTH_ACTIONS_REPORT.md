# Overlay 13 of 15 — Shared UI/TUI and Desktop GTK truth, actions and IPC presentation

## Scope

This overlay closes the S13 and S14 remediation buckets: shared UI/TUI state semantics plus Desktop GTK truth, command, action, IPC, logging and preference hardening.

Assigned findings closed:

- S13-01 through S13-14
- S13-R2-01 through S13-R2-05
- S14-01 through S14-16
- S14-R2-01 through S14-R2-03

Total: 38 findings.

## Implementation summary

### Shared UI and TUI

- Parsed action booleans with strict string-aware coercion so `"false"` no longer enables retry/resume/cancel behavior.
- Kept cancel represented in semantic snapshots for compatibility, but disabled it when no authoritative `run_id` exists so TUI does not expose a guaranteed-failing cancel shortcut.
- Added artifact apply action surfacing when a runtime state has artifact evidence.
- Treated `rollback-failed`, warnings and interrupted states as terminal/actionable where appropriate.
- Fixed TUI live progress to use canonical `overall_percent`, `completed_nodes`, and `total_nodes` summary fields.
- Hid completed `passed`, `completed`, and `cached` steps when completed-step hiding is enabled.
- Preserved duplicate diagnostics from different phases by including phase identity in diagnostic de-duplication.
- Preserved duplicate progress ids by suffixing repeated ids while retaining `metrics.original_id`.
- Kept cyclic or parent-only progress nodes visible by attaching unsafe parent links to the operation root.
- Stopped cancelled child progress from folding parent target/workflow state to green `ok`.
- Made UI preference/env resolution respect explicit `env={}` instead of falling back to process environment.
- Made malformed/non-finite numeric UI values fail soft to safe presentation defaults instead of crashing snapshots.
- Added non-strict capability negotiation for runtime snapshots, so unknown requested capabilities are reported as missing instead of crashing rendering.
- Re-normalized pre-resolved presentation preferences crossing IPC/state boundaries.
- Made Tasker progress export ignore non-finite percent values.
- Validation control previews omit `--if-revision` for read-only control actions while retaining it for mutating actions.

### Desktop GTK and desktop runtime

- Added variant support to the desktop command model and run-workflow payload reconstruction.
- Kept partially typed quoted command snippets previewable instead of crashing `shlex` parsing.
- Preserved empty argv arguments in desktop process launch.
- Desktop run cancellation now reports `cancelled` rather than generic failure.
- Desktop output pump no longer remains stuck when descendants keep stdout/stderr file descriptors alive after the direct child exits.
- Output callback exceptions are contained and cannot kill the pump thread.
- Desktop IPC snapshot reading now handles fragmented JSON frames.
- Control Center prefers live IPC run state over stale local HUD cache and sorts runs by sequence/timestamp freshness.
- Desktop semantic UI now carries phase highlights into the rendered semantic model.
- Desktop run logs and GTK failure/event logs are created with `0600` permissions.
- Desktop diagnostic line parsing avoids false errors from success text and zero-error summaries.
- History limit `0` now means no history, not all history.
- Malformed/non-finite desktop numeric preferences and model options fall back to safe defaults.
- Artifact page input fields now feed a manual candidate into artifact rendering and can push selected artifact/repo values back to the Run page.

## Validation performed

- `tests/test_rem13_ui_desktop_truth_actions.py` — 19 passed.
- Shared UI/TUI/Desktop GTK compatibility sweep — 186 passed.
- Overlay 7–13 remediation regression sweep — 123 passed.

The generated overlay was also smoke-applied to an Overlay 12 state with Devtool using `--no-validate`.
