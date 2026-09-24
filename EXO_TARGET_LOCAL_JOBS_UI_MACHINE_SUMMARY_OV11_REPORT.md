# EXO TLJ-11 — Target-Local Jobs UI and Machine Summary

## Goal

Carry target-local job identity through shared UI labels and runner machine JSON
so TUI, dashboard, HUD, notification-copy, and external automation consumers can
show the same identity as plan, preflight, and run-summary exports.

## Delivered

- Added shared workflow job display rendering for action labels.
- Target-local job actions now render as `target-local job:<target>.<job>`.
- Repository job actions now render as `repository job:<job>` when workflow job
  metadata is present.
- Upgraded `build_runner_json()` to schema version 2.
- Added top-level runner JSON `workflow` payload for dashboard/HUD consumers.
- Added top-level runner JSON `execution_context` with target-local and
  repository job summaries.
- Added focused regression tests for action labels and runner JSON output.
- Updated `docs/EXO_TARGET_LOCAL_JOBS.md`.

## Validation replay

- `tests/test_exo_target_local_jobs_ui_machine_summary.py`
- `tests/test_exo_target_local_jobs_run_summary_export.py`
- `tests/test_exo_target_local_jobs_plan_export.py`
- `tests/test_exo_target_local_jobs_preflight_export.py`
- `tests/test_exo_target_local_jobs_inspection.py`
- `tests/test_exo_target_local_jobs_completion_doctor.py`
- `tests/test_summary.py`
- `tests/test_ui11_ui12_machine_completion_config.py`
- `python -m compileall devtool`

## Notes

This is a non-gate overlay. It does not alter execution ordering or target-local
job resolution. It only standardizes how already-resolved identity appears in
user-facing and machine-facing summaries.
