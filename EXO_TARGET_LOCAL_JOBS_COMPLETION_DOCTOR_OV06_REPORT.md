# EXO TLJ-06: Completion and Doctor Surfaces

## Intent

Make target-local jobs visible to command-line completion, editor tooling, JSON discovery, and doctor diagnostics without weakening the ownership boundary introduced in TLJ-05.

## Delivered

- `completion-state` schema v2 with target-local job categories:
  - `target-jobs`
  - `target-job-keys`
  - `target-job-refs`
- Zsh completion advertises the new completion-state categories.
- `devtool jobs --json` schema v3 includes a `completion` object with stable `target_job_keys` and `target_job_refs`.
- `devtool doctor` text and JSON now report target-local jobs and workflows.
- Documentation explains the completion categories and preserves the rule that cross-target composition uses `target:<target>#<workflow>`.
- CLI help golden coverage is refreshed so the post-WRAP `wrapper` and `invoke` commands remain aligned with the retained final-seal test.

## Validation

Exact-ZIP replay covered TLJ focused tests, completion/doctor compatibility, discovery, workflow, scheduler, architecture/OCI final-seal golden checks, and py_compile checks.

## v2 packaging correction

- Repackaged the same TLJ-06 code payload with the current schema-v2 finalize manifest shape.
- Changed `finalize.script` from the legacy string form to the required object form with path, runner, protocol, sha256, and timeout fields.

