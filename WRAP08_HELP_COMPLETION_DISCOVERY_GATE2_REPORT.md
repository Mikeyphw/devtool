# WRAP-08 — Help, Completion, Discovery, Doctor, and Docs

## Scope

WRAP-08 is the second wrapper roadmap gate. It makes the repository wrapper feature discoverable and supportable before the final cross-surface convergence overlay.

Implemented surfaces:

- `devtool wrapper help [command]`
- `devtool wrapper help --json`
- `devtool wrapper discover`
- `devtool wrapper discover --json`
- polished `devtool wrapper exec --help`
- polished `devtool wrapper exec <command> --help`
- polished `devtool wrapper exec --version`
- completion-state kinds:
  - `wrapper-commands`
  - `wrapper-aliases`
  - `wrapper-options`
- doctor payload and checks for wrapper launchers and command documentation
- wrapper user documentation at `docs/WRAPPER.md`

## Design notes

- Help/discovery is read-only and does not execute wrapper commands.
- `wrapper exec` still resolves locally and re-enters the canonical Devtool CLI only for executable wrapper commands.
- `--help` and `--version` requests terminate before canonical execution.
- Discovery payload includes launcher lifecycle status so repositories can expose both command contract and launcher drift state from one machine-readable command.
- Completion-state exposes wrapper command names, aliases, and options without making shells parse `.devtool.toml` directly.
- Doctor reports wrapper launchers and missing command descriptions as diagnostics instead of hidden failures.

## User-facing examples

```bash
devtool wrapper discover
devtool wrapper discover --json
devtool wrapper help
devtool wrapper help build
devtool wrapper exec --help
devtool wrapper exec build --help
devtool completion-state --kind wrapper-commands
devtool completion-state --kind wrapper-options --format json
devtool doctor --json
```

## Gate validation policy

This gate should be validated through Devtool's own validation selection path instead of hand-running pytest in the artifact validator. The artifact validator therefore uses repeated explicit `--pytest-test` selectors with `--task test`.

## Local audit results

Executed in the reconstructed post-WRAP-07 tree:

```text
WRAP-01 → WRAP-08 focused suite: 60 passed
Compatibility replay suite: 158 passed
py_compile selected files: PASS
compileall devtool: PASS
```

Focused suite:

```text
tests/test_wrap01_wrapper_config_contract.py
tests/test_wrap02_generic_invoke_plan_parity.py
tests/test_wrap03_wrapper_resolution.py
tests/test_wrap04_execution_invocation_origin.py
tests/test_wrap05_wrapper_dispatch.py
tests/test_wrap06_wrapper_launchers.py
tests/test_wrap07_binding_passthrough_safety.py
tests/test_wrap08_help_completion_doctor.py
```

Compatibility suite:

```text
tests/test_completion_state.py
tests/test_ui11_ui12_machine_completion_config.py
tests/test_mp01_mp02_target_job_workflow_contracts.py
tests/test_mp03_mp04_workflow_graph_runtime.py
tests/test_mp05_mp06_cross_target_workspace_groups.py
tests/test_exo03_execution_store_reducer.py
tests/test_exo_p0_cli_run_finalization.py
tests/test_python_chroot_env_consistency.py
tests/test_termux_native_android_providers.py
```

## Artifact validator

The packaged gate validator uses:

```text
python -m devtool --repo "$repo" --target devtool validate --task test --pytest-test <test-file> ... --pytest-arg=-q
```

This keeps the gate wired through Devtool's validation selector plumbing while preserving explicit, repeatable test selection.

## Status

PASS — ready for WRAP-08 gate application.

## Final exact-ZIP replay

After packaging, the exact ZIP bytes were unpacked and replayed onto a fresh post-WRAP-07 base.

```text
source inventory replay: PASS (8 entries)
WRAP-01 → WRAP-08 exact-ZIP replay: 60 passed
compatibility exact-ZIP replay: 158 passed
py_compile selected files: PASS
compileall devtool: PASS
finalize hook write-boundary simulation: PASS
```
