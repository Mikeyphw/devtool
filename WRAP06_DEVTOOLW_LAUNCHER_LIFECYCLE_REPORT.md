# WRAP-06 — Repository `devtoolw` Launcher Lifecycle

## Scope

WRAP-06 adds repository-owned launcher lifecycle support for the wrapper stack:

- `devtool wrapper init`
- `devtool wrapper init --force`
- `devtool wrapper update`
- `devtool wrapper verify`
- `devtool wrapper status`
- `--json` machine output for launcher lifecycle commands
- generated POSIX `devtoolw`
- generated Windows `devtoolw.cmd`

The generated launchers do not execute repository commands themselves. They locate the repository root and delegate to:

```text
devtool --repo <repo-root> wrapper exec <args...>
```

All wrapper resolution, provenance, routing, SSH/chroot behavior, progress accounting, and EXO truth remain owned by the existing Devtool CLI path introduced in WRAP-01 through WRAP-05.

## Files

- `devtool/wrapper_launcher.py`
- `devtool/resources/wrapper/devtoolw`
- `devtool/resources/wrapper/devtoolw.cmd`
- `devtool/cli.py`
- `pyproject.toml`
- `tests/test_wrap06_wrapper_launchers.py`

## Lifecycle behavior

`init` creates both launchers and refuses to overwrite unmanaged or drifted files unless `--force` is supplied.

`update` refreshes generated launchers in place.

`verify` returns exit code `0` when launchers are installed/current and `1` when missing or drifted.

`status` reports the same status without failing.

Each generated launcher includes the managed marker:

```text
DEVTOOL_WRAPPER_LAUNCHER=1
```

## Safety boundaries

WRAP-06 intentionally does not add version pinning, self-download, network bootstrap, or any launcher-side execution engine. Launchers are thin, deterministic delegators only.

The artifact finalizer preserves repository cleanliness by snapshotting and restoring `devtool.egg-info` around `pip install -e "$HOME/Code/devtool"`.

## Validation

Focused suite:

```text
python -m pytest \
  tests/test_wrap01_wrapper_config_contract.py \
  tests/test_wrap02_generic_invoke_plan_parity.py \
  tests/test_wrap03_wrapper_resolution.py \
  tests/test_wrap04_execution_invocation_origin.py \
  tests/test_wrap05_wrapper_dispatch.py \
  tests/test_wrap06_wrapper_launchers.py -q
```

Result:

```text
50 passed
```

Compatibility slice:

```text
python -m pytest \
  tests/test_mp01_mp02_target_job_workflow_contracts.py \
  tests/test_mp03_mp04_workflow_graph_runtime.py \
  tests/test_mp05_mp06_cross_target_workspace_groups.py \
  tests/test_mp09_mp10_safe_scheduler_plan.py \
  tests/test_completion_state.py \
  tests/test_exo03_execution_store_reducer.py \
  tests/test_exo_p0_cli_run_finalization.py \
  tests/test_termux_native_android_providers.py \
  tests/test_python_chroot_env_consistency.py -q
```

Result:

```text
161 passed
```

Compile check:

```text
python -m py_compile devtool/cli.py devtool/wrapper_launcher.py
```

Result:

```text
PASS
```

## Roadmap note

WRAP-06 is not a validation gate in the wrapper roadmap. The artifact validation remains disabled; it should be applied with `--no-validate`.
