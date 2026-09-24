# EXO TLJ-07 — Target-Local Jobs Inspection and Filtering

## Scope

This overlay closes the inspection gap left after completion and doctor support:
`devtool jobs` now reports target-local jobs in human output and accepts a
`--target` filter for target-local job inspection.

## Delivered

- `devtool jobs` includes repository-wide and target-local jobs in text output.
- `devtool jobs --target <target>` filters target-local jobs to one target while
  keeping repository-wide reusable jobs visible.
- `devtool jobs --json` is upgraded to schema version 4.
- JSON output includes `selected_target`, `summary`, `repository_jobs`,
  `target_jobs`, and filtered completion lists.
- Unknown `--target` values fail early with a clear error.
- Regression tests cover human output, JSON filtering, parser support, and the
  existing completion/doctor schema update.

## Validation replay

- TLJ focused inspection/completion suite: passed.
- TLJ schema/preflight/runtime/affinity/workflow/completion/inspection suite: passed.
- MP01-MP10 workflow/scheduler/discovery compatibility slice: passed.
- py_compile: passed.
