# WRAP-09 — Cross-Surface Convergence and Final Seal

## Scope

WRAP-09 closes the repository-wrapper roadmap by adding a read-only final seal surface that checks whether the wrapper contract, generated launchers, help/discovery output, completion state, doctor payload, documentation, and pure resolution agree with the same repository-local wrapper configuration.

## Delivered

- Added `devtool.wrapper_final_seal`.
- Added `devtool wrapper seal`.
- Added `devtool wrapper seal --json`.
- Added deterministic final-seal payloads with schema versioning.
- Added human-readable final-seal output.
- Added fail-closed checks for missing launchers and missing `docs/WRAPPER.md`.
- Added cross-surface checks for:
  - configured wrapper schema,
  - default command validity,
  - command descriptions,
  - launcher currentness,
  - wrapper docs presence,
  - discovery command parity,
  - completion command/alias parity,
  - overview and command help rendering,
  - help-mode resolution for every command,
  - execute-mode resolution for non-interactive commands,
  - doctor wrapper checks.
- Updated `docs/WRAPPER.md` with final-seal usage.

## Safety notes

The final seal is read-only. It does not execute wrapper commands, shell out through wrapper values, mutate launchers, or run repository jobs. Commands that require user-provided required options or positionals are explicitly recorded as skipped for execute-mode resolution while still requiring help-mode resolution.

## Validation

Exact-ZIP inventory replay:

```text
inventory replay PASS
```

Focused direct replay from the exact-ZIP replay tree:

```text
python -m pytest -q tests/test_wrap01_wrapper_config_contract.py tests/test_wrap02_generic_invoke_plan_parity.py tests/test_wrap03_wrapper_resolution.py tests/test_wrap04_execution_invocation_origin.py tests/test_wrap05_wrapper_dispatch.py tests/test_wrap06_wrapper_launchers.py tests/test_wrap07_binding_passthrough_safety.py tests/test_wrap08_help_completion_doctor.py tests/test_wrap09_wrapper_final_seal.py
64 passed
```

Compatibility direct replay from the exact-ZIP replay tree:

```text
python -m pytest -q tests/test_completion_state.py tests/test_ui11_ui12_machine_completion_config.py tests/test_mp01_mp02_target_job_workflow_contracts.py tests/test_mp03_mp04_workflow_graph_runtime.py tests/test_mp05_mp06_cross_target_workspace_groups.py tests/test_exo03_execution_store_reducer.py tests/test_exo_p0_cli_run_finalization.py tests/test_python_chroot_env_consistency.py tests/test_termux_native_android_providers.py
158 passed
```

Static checks:

```text
python -m py_compile devtool/cli.py devtool/wrapper_final_seal.py devtool/wrapper_help.py devtool/completion_state.py devtool/doctor.py
python -m compileall -q devtool
PASS
```

Finalize hook simulation:

```text
absent devtool.egg-info restore: PASS
existing devtool.egg-info restore: PASS
failure propagation and restore: PASS
```

The artifact validator routes through Devtool's own validation selector path using repeated `--task test` and repeated `--pytest-test` selectors. A local sandbox attempt to run that exact Devtool validation path reached Devtool's venv bootstrap and failed only because the sandbox has no DNS/network for dependency download; the selected test sets above were replayed directly from the exact-ZIP replay tree in the already-provisioned environment.

## Post-WRAP-09 re-seal

`devtool_wrapper_execution_evidence_reseal_02_v1` upgrades the final-seal payload to schema 2 and closes the execution-evidence gap found by the post-roadmap audit. The seal now probes the shared production wrapper re-entry bridge and persists its invocation origin through the real in-memory EXO event/store path without executing repository work. See `WRAPPER_EXECUTION_EVIDENCE_RESEAL_02_REPORT.md` for the remediation audit loop and validation evidence.
