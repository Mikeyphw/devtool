# SSH-02 Execution, History & Recovery Convergence Report

## Scope

SSH-02 is the SSH roadmap milestone gate that moves remote execution truth out of
live tmux discovery and into durable Devtool run/output/return state. It builds on
SSH-01 transport/environment/worker preparation and preserves the existing EXO,
RunStore, federation, frozen-source, and GIT-FRX lineage contracts rather than
introducing a parallel SSH database.

## Delivered contracts

### Canonical persisted SSH run identity

Remote phase metadata now persists the SSH execution context needed to reconnect
history to the rest of Devtool:

- canonical Devtool run ID and phase node ID;
- SSH profile/destination, workspace ID/path, source sync ID and lease;
- repository root/fingerprint and selected target;
- FRX-Y07 typed contract type/name/ref;
- GIT-FRX-09 parent/child run/node/repository/target/workflow/contract lineage;
- frozen-source lock digest;
- tmux session/window/pane as execution-host coordinates, not history authority;
- stdout/stderr durable paths and output schema;
- confirmation mode (`automatic` for global `-y/--yes`, otherwise interactive).

### Persistent split stdout/stderr

Each remote phase writes independent durable `stdout.log` and `stderr.log` files
inside the remote run directory while still feeding the established workspace
log. FIFO/tee orchestration preserves stream separation and the actual command
exit code. Output remains queryable after command completion, local disconnect,
or tmux death.

Output state is projected as one of:

- `complete`
- `partial`
- `still-writing`
- `unavailable`
- `interrupted`

### Historical sessions and runs

`devtool ssh sessions` and `devtool ssh runs` now project persisted remote run
records instead of treating live tmux inventory as history. The projections
include phase/output state, workspace/source identity, typed contract, lineage,
return state and recovery guidance. When local RunStore evidence exists for the
same repository/run ID, grouped run history includes its test summary, artifacts,
build artifacts, logs, operations, and comparison data.

Legacy SSHW-05 persisted records remain readable.

### `devtool ssh output`

A new historical output surface supports selection by run/session/latest and can
read either/both streams. `--follow` tails persisted stream files rather than
attaching to raw tmux. Follow mode maintains independent byte offsets and retries
transport loss without advancing those offsets, preventing duplicate output on
reconnect within the follow invocation. JSON follow captures output into the
structured response rather than mixing live stream text into JSON stdout.

### Managed `devtool ssh exec`

A new arbitrary-command surface reuses the canonical SSH substrate instead of
bypassing it:

- existing profile and worker probing;
- managed/existing/ephemeral workspace behavior;
- SSH-01 environment policy and multiplex transport;
- tmux remote phase execution;
- EXO run/node/process events;
- durable stdout/stderr and historical lookup;
- return bundle handling and recovery;
- lease cleanup and resilience operations;
- repository/target/frozen-source/federation provenance.

Normal argv mode preserves argument boundaries. Raw shell text requires explicit
`--shell`. Foreground, detached, and follow flows are represented without
publishing false terminal events for a still-running detached command. Global
`-y/--yes` is preserved as explicit confirmation provenance.

### Recovery convergence

Resume/cancel/recover/retrieve state now reports recovery intent and whether a
remote command actually needs to be rerun. Completed phases are reconciled rather
than re-executed. Recovery distinguishes remote execution failure from a
recoverable return-transfer failure.

### Per-artifact return lifecycle

Return evidence now projects artifact lifecycle state across:

- expected
- produced
- staged
- transferred
- verified
- restored locally
- failed
- recoverable

Concrete restored files include size and SHA-256 evidence. Unresolved expected
selectors remain represented when a return transfer is partial/recoverable.

## Native Devtool gate

SSH-02 declares a target-local eight-stage `roadmap-ssh02` DAG. The gate includes
an explicit transaction-root-aware editable install before SSH-02 validation:

```text
python -m pip install --no-build-isolation -e "$DEVTOOL_TRANSACTION_REPO_ROOT"
```

The final gate run used `/mnt/data/devtool_ssh02_work` as the transaction root and
completed these stages successfully. Artifact replay additionally verified the editable-install lifecycle: the declared gate job installs the temporary transaction candidate before tests, while the before-commit artifact hook re-points the editable install at the stable primary checkout so transaction cleanup cannot break the next Devtool command:

1. source syntax compile
2. SSH-02 editable candidate install
3. SSH-02 execution/history/recovery fixture
4. GIT-FRX-09 contract tests — 9 passed
5. SSH-01 contract tests — 23 passed
6. SSH-02 contract tests — 19 passed
7. complete SSH regression family — 173 passed
8. workflow/EXO baseline — 18 passed

The corrected post-Termux gate completed 8/8 stages with 242/242 tests, including 19/19 SSH-02 contracts and 173/173 SSH regressions.

Gate diagnostics: no test failures were observed. The final gate log is written
to `.devtool/reports/roadmap/roadmap-ssh02-gate-final.log` in the validation
workspace.

## Audit-loop findings fixed before seal

The implementation was not sealed on its first green subset. The audit loop found
and corrected:

1. a shell-fragment newline construction regression that broke fake-tmux
   execution and could hide the real remote exit status; the complete SSH family
   subsequently passed 169/169 and, after final coverage additions, 173/173;
2. missing local return/RunStore projection from grouped SSH history;
3. JSON follow mode potentially mixing live output with its JSON payload;
4. explicit coverage for reconnect offsets/no duplication, persisted history
   after tmux death, argv/shell boundaries, detached completion semantics,
   per-artifact return evidence, and non-rerunning recovery.
5. package replay showed that leaving the editable install pointed at the temporary transaction worktree could break the next `devtool` invocation after cleanup; the declared gate still installs the candidate for validation, but the before-commit artifact hook now re-points the editable install at `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT`.

## Qualification boundary

This milestone validates the Devtool implementation and simulated/local SSH
contracts. It does not claim that every SSH-02 scenario was exercised against a
specific live Termux or Linux worker during this build session. Live worker
qualification remains an operational/environmental check and SSH-03 final
qualification will harden the remaining lifecycle/seal surface.

## Result

SSH-02 is implementation-complete for its roadmap scope and passes its declared
milestone gate. The next SSH roadmap item is SSH-03 Lifecycle Hardening & Final
Qualification.


## Termux validation hotfix

A real Termux milestone-gate run failed `test_commands_mode_drops_host_runtime_and_cache_paths` because SSH-02 persisted the local repository root into remote phase metadata, reintroducing `/data/data/com.termux/...` into the generated remote shell. The final implementation persists the remote repository root instead and retains repository identity through the repository fingerprint. The original Termux leakage regression plus a new SSH-02-specific assertion cover this boundary. The corrected milestone gate passes 8/8 stages and 242/242 tests.
