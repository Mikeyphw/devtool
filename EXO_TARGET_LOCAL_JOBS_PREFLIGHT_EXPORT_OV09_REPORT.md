# EXO TLJ-09 — Target-Local Jobs Preflight Export

## Scope

This overlay extends the target-local jobs contract from planning into preflight/toolchain analysis. It keeps the `preflight` surface aligned with `plan`, `jobs`, `doctor`, completion, and workflow provenance by exposing the exact target-local job identity involved in a selected workflow.

## Delivered

- Upgraded `ToolchainReport.as_dict()` to schema version 2.
- Added preflight `summary` fields:
  - `target_local_job_count`
  - `repository_job_count`
  - `targets_with_target_local_jobs`
- Added `target_local_jobs[]` and `repository_jobs[]` machine-readable sections.
- Added explicit `job:<target>.<job>` readiness checks for target-local jobs.
- Added human preflight rows for target-local and repository jobs.
- Documented TLJ-09 usage in `docs/EXO_TARGET_LOCAL_JOBS.md`.
- Added focused regression tests for JSON and human output.

## Validation replay

Run in the packaged replay tree:

```bash
python -m pytest tests/test_exo_target_local_jobs_*.py -q
python -m py_compile devtool/toolchain_requirements.py devtool/cli.py tests/test_exo_target_local_jobs_preflight_export.py
python -m compileall -q devtool tests/test_exo_target_local_jobs_preflight_export.py
```

Observed:

```text
27 passed
py_compile: PASS
compileall: PASS
```

## Artifact hygiene

- Non-gate overlay: validation is disabled in the artifact manifest.
- Finalize hook uses the current object-shaped `finalize.script` schema.
- Finalize hook refreshes the editable Devtool install before commit and restores `devtool.egg-info` metadata.
