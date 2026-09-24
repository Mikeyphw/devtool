# EXO Target-Local Jobs Schema OV01

## Scope

Adds a first-class schema for target-local EXO jobs so repositories can declare auditable per-target workflows without inventing TOML that Devtool does not parse.

## Delivered

- Parses `[targets.<target>.jobs.<name>]`.
- Registers target-local jobs with internal execution keys `<target>.<job>`.
- Allows target workflows to reference local jobs by short `job:<name>` references.
- Keeps repository-wide `[jobs.<name>]` available to all targets.
- Gives target-local jobs precedence only within their owning target.
- Supports structured `command`, explicit `shell_command`, runner `action`, and `gradle_tasks` shorthand.
- Adds config-dict visibility for both global job keys and per-target local jobs.
- Ensures malformed target-local job schema fails during config load, before execution.
- Preserves existing repository-wide jobs/workflows and WRAP surfaces.

## Validation

Focused tests:

```text
python -m pytest tests/test_exo_target_local_jobs_schema.py -q
4 passed
```

Compatibility slice:

```text
python -m pytest \
  tests/test_exo_target_local_jobs_schema.py \
  tests/test_mp01_mp02_target_job_workflow_contracts.py \
  tests/test_mp03_mp04_workflow_graph_runtime.py \
  tests/test_mp05_mp06_cross_target_workspace_groups.py \
  -q
68 passed
```

Additional discovery/completion/config slice:

```text
python -m pytest \
  tests/test_completion_state.py \
  tests/test_rem03_cli_config_discovery.py \
  tests/test_ui11_ui12_machine_completion_config.py \
  tests/test_exo_target_local_jobs_schema.py \
  -q
40 passed
```

Static checks:

```text
python -m py_compile devtool/models.py devtool/workflow_config.py devtool/config.py devtool/workflow_runtime.py devtool/workflow_scheduler.py
python -m compileall -q devtool
```

## Notes

This overlay intentionally implements schema, validation, resolution, execution planning, and documentation. Later overlays can expand evidence capture, doctor UX, scheduler resource modeling, and DustUI migration onto this supported contract.
