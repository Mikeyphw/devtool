# EXO TLJ-12 — Target-Local Jobs Final Seal

## Purpose

Seal the target-local jobs roadmap with an adoption contract and regression
coverage that proves target-local job identity is stable across public and
machine-readable surfaces.

## Scope

- Documentation adoption checklist for future repo migrations.
- DustUI Go target-local jobs example.
- Final-seal regression tests covering:
  - config loading and job metadata
  - completion-state target-local job refs
  - doctor payload
  - jobs inspection and filtering
  - plan JSON export
  - preflight JSON export
  - runner/workflow summary export

## Acceptance

- Target-local jobs retain the executable key `<target>.<job>`.
- Public JSON surfaces expose structured identity fields so consumers do not
  need to parse raw keys.
- Repository-wide jobs remain reusable across target filters and workflow
  boundaries.
- Cross-target orchestration continues to use `target:<target>#<workflow>`.

## Validation

Focused final-seal validation:

```bash
python -m pytest       tests/test_exo_target_local_jobs_final_seal.py       tests/test_exo_target_local_jobs_plan_export.py       tests/test_exo_target_local_jobs_preflight_export.py       tests/test_exo_target_local_jobs_run_summary_export.py       tests/test_exo_target_local_jobs_ui_machine_summary.py -q
```
