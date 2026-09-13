# Overlay 4 of 15 — Execution environments, cancellation and scheduler correctness

Campaign: `final_remediation_20260913`  
Overlay artifact: `devtool_rem04_execution_scheduler_correctness_v1.tar.gz`

## Findings closed

This overlay closes the complete S03 + S04 bucket: 31 audit findings.

### S03 — execution environments and cancellation

- `S03-01` — TUI/stop callback cancellation now forces process return code 130 even if a child traps SIGINT and exits 0.
- `S03-02` — SSH command-mode wrapped phases install a local INT/TERM trap that kills the remote tmux session before returning 130.
- `S03-03` — Termux/chroot PTY mode now forwards local TTY stdin to the chroot child PTY instead of being output-only.
- `S03-04` — Termux/chroot Ctrl+C now returns cancellation status 130 regardless of trapped child status.
- `S03-05` — execution preflight resolves relative expected paths from `repo_root`, not from the process current directory.
- `S03-06` — chroot home auto-discovery rejects ambiguous multi-user checkout matches instead of picking the first candidate.
- `S03-07` — SSH staging/preflight subprocess calls now have bounded transport timeouts.
- `S03-08` — chroot environment translation treats `CLASSPATH` as a path-list variable.
- `S03-09` — SSH boolean precedence is now file < env < CLI, so env booleans can override file booleans as documented.
- `S03-10` — chroot launcher probes and home discovery pass the supplied env to launcher construction and subprocess execution.
- `S03-R2-01` — Termux chroot host-copy flag injection treats `--group` as a value flag.
- `S03-R2-02` — chroot path translation replaces mapped host paths only on path boundaries.
- `S03-R2-03` — explicit empty env mappings can clear inherited execution env instead of being ignored.
- `S03-R2-04` — chroot probe helpers no longer mutate global `os.environ` for launcher overrides.
- `S03-R2-05` — malformed execution/workflow metadata is coerced safely instead of crashing `run_specs`.
- `S03-R2-06` — SSH default session naming skips option values such as `--group android` before choosing a command label.

### S04 — scheduler, workflow and reservations

- `S04-01` — `read_only = "false"` no longer becomes truthy scheduler state.
- `S04-02` — cross-process reservations now reserve known read scopes as well as write scopes to prevent write/read collisions.
- `S04-03` — dotted target/workflow/step components are escaped in workflow node keys to prevent silent key collisions.
- `S04-04` — parallel node progress proxies no longer clear the shared cancellation token from worker-local cleanup.
- `S04-05` — `read_only=true` no longer defeats fail-closed external read/write scope rules.
- `S04-06` — affected target selection parses `git diff --name-status -z -M`, so renames include both source and destination paths.
- `S04-07` — affected pattern normalization no longer strips the leading dot from `.github/**`.
- `S04-08` — malformed stale reservation PIDs are handled safely.
- `S04-09` — workflow plan identity now prefers the workspace action when available instead of blindly using the first flattened child node.
- `S04-R2-01` — programmatic `parallel_requested="false"` no longer enables parallel scheduling.
- `S04-R2-02` — typoed resource class overrides are ignored so Gradle/.NET caps still apply.
- `S04-R2-03` — unkeyed specs disable workflow parallel planning instead of being dropped.
- `S04-R2-04` — exceptions inside parallel workers become failed node results with `workflow_worker_exception` metadata.
- `S04-R2-05` — duplicate normalized job names and workflow action names fail during config load.
- `S04-R2-06` — terminal node state honors `OperationResult.skipped` even when metadata lacks an explicit skipped state.

## Files changed

- `devtool/affected.py`
- `devtool/config.py`
- `devtool/execution.py`
- `devtool/execution_environments.py`
- `devtool/reservations.py`
- `devtool/runners.py`
- `devtool/ssh_tmux_runner.py`
- `devtool/termux_chroot_runner.py`
- `devtool/workflow_config.py`
- `devtool/workflow_graph.py`
- `devtool/workflow_runtime.py`
- `devtool/workflow_scheduler.py`
- `tests/test_mp09_mp10_safe_scheduler_plan.py`
- `tests/test_rem04_execution_scheduler.py`

## Validation performed

Focused regression gate:

```text
225 passed
```

Command:

```bash
python -m pytest \
  tests/test_rem04_execution_scheduler.py \
  tests/test_execution_environments.py \
  tests/test_mp01_mp02_target_job_workflow_contracts.py \
  tests/test_mp03_mp04_workflow_graph_runtime.py \
  tests/test_mp07_mp08_affected_toolchain.py \
  tests/test_mp09_mp10_safe_scheduler_plan.py \
  tests/test_ov03_config_execution_contracts.py \
  tests/test_ov06_ssh_contracts.py \
  tests/test_ssh_tmux_runner.py \
  tests/test_termux_chroot_runner.py \
  tests/test_runners_termux.py -q
```

Full repository pytest was also started and progressed beyond 75% without a new assertion failure before the sandbox execution limit stopped the run. That is intentionally not recorded as the final release seal; Overlay 15 owns the full repository qualification.
