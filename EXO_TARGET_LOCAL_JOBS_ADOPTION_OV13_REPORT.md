# EXO TLJ-13 — Target-Local Jobs Adoption Report

## Purpose

Add a read-only adoption report after the TLJ final seal so follow-up repo
migrations can inspect target-local job wiring before generating project
overlays.

## Scope

- New `devtool target-local-jobs` / `devtool tlj` command.
- JSON and human adoption surfaces.
- Per-target filtering with `--target`.
- Workflow command examples for `plan`, `preflight`, and `invoke`.
- Zsh completion entries for the new command and target filter.
- Documentation updates for post-seal adoption reporting.

## Non-goals

- Does not mutate `.devtool.toml`.
- Does not alter target-local job runtime semantics.
- Does not weaken TLJ-12 final-seal assertions.

## Validation

```bash
python -m pytest tests/test_exo_target_local_jobs_adoption.py -q
python -m pytest tests/test_exo_target_local_jobs_final_seal.py tests/test_exo_target_local_jobs_plan_export.py tests/test_exo_target_local_jobs_preflight_export.py tests/test_exo_target_local_jobs_run_summary_export.py -q
python -m py_compile devtool/target_local_adoption.py devtool/cli.py devtool/completion.py tests/test_exo_target_local_jobs_adoption.py
```
