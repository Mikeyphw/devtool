# EXO Target-Local Jobs OV03 — Runtime Provenance and Scheduler Identity

## Scope

Makes target-local job identity explicit across resolved workflow steps, workflow graph JSON, command-spec metadata, and safe-scheduler plan nodes.

OV01 made the schema executable. OV02 made discovery, preflight, and path accounting truthful. OV03 closes the provenance gap so downstream EXO surfaces no longer need to infer that `job:contracts` resolved to `dustui_android.contracts`.

## Delivered

- Extends `WorkflowStepRef` with resolution identity fields:
  - `resolution_scope`
  - `resolution_target`
  - `resolution_local_name`
  - `resolution_key`
- Populates job resolution fields during workflow parsing.
- Adds resolution fields to `workflow_step_as_dict` output.
- Adds a `resolution` object to job nodes in `ExecutionGraph.as_dict()`.
- Adds runtime command metadata:
  - `workflow_resolution_scope`
  - `workflow_resolution_target`
  - `workflow_resolution_local_name`
  - `workflow_resolution_key`
- Adds scheduler node identity fields:
  - `job_scope`
  - `job_target`
  - `job_local_name`
  - `job_key`
  - `target_local_job`
- Updates target-local jobs documentation with runtime provenance guidance.
- Adds focused tests for config graph, runtime metadata, and scheduler plan parity.

## Validation

Focused tests:

```text
python -m pytest tests/test_exo_target_local_jobs_runtime_provenance.py -q
2 passed
```

Target-local jobs + scheduler compatibility slice:

```text
python -m pytest \
  tests/test_exo_target_local_jobs_schema.py \
  tests/test_exo_target_local_jobs_preflight_discovery.py \
  tests/test_exo_target_local_jobs_runtime_provenance.py \
  tests/test_mp03_mp04_workflow_graph_runtime.py \
  tests/test_mp05_mp06_cross_target_workspace_groups.py \
  tests/test_mp09_mp10_safe_scheduler_plan.py \
  -q
58 passed
```

Discovery/completion compatibility slice:

```text
python -m pytest \
  tests/test_completion_state.py \
  tests/test_rem03_cli_config_discovery.py \
  tests/test_ui11_ui12_machine_completion_config.py \
  tests/test_exo_target_local_jobs_runtime_provenance.py \
  -q
38 passed
```

Static checks:

```text
python -m py_compile devtool/models.py devtool/workflow_config.py devtool/workflow_graph.py devtool/workflow_runtime.py devtool/workflow_scheduler.py
python -m compileall -q devtool
```

## Notes

This overlay is non-gate. It does not change execution order or scheduler safety decisions; it enriches metadata and machine output so future overlays can attach logs, artifacts, and EXO events to the correct target-local job identity.
