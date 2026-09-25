# GIT-FRX-03 — Isolated Worktree Transaction Engine

## Roadmap identity

- Roadmap ID: **GIT-FRX-03**
- Devtool target: `devtool`
- Native workflow: `roadmap-git-frx03`
- First-class declared test: `roadmap-git-frx03`
- Non-test operations: `transaction-worktree-fixture`, `self-refresh-root-probe`
- Apply policy for this milestone: **non-gate / `--no-validate` / no Devtool reinstall**

## Merge-window decision

The campaign merge window evaluated **GIT-FRX-03 + GIT-FRX-04 + GIT-FRX-05**. GIT-FRX-03 remains standalone because it changes the mutation/execution environment itself. GIT-FRX-04 changes commit/history policy and GIT-FRX-05 changes overlay base identity; merging those independent recovery surfaces would materially worsen fault isolation.

## Promise trace

| GIT-FRX-03 promise | Delivered contract |
|---|---|
| Prefer isolated worktrees | User-facing apply commands default to `--isolation worktree`. |
| Preserve direct escape hatch | `--isolation direct` keeps legacy in-place behavior. |
| Execute apply lifecycle in transaction tree | Extraction, delete/config registration, artifact preflight/hooks, validation config reload, and commit operate from the transaction execution root. |
| Keep primary checkout unchanged until success | Clean committed repositories create a detached linked worktree at captured start SHA; primary HEAD/index/worktree are verified unchanged before integration. |
| Integrate successful result exactly | Isolated commits advance the captured branch with compare-and-swap `update-ref`, materialize the exact result commit, and then synchronize any intentional uncommitted result. |
| Rollback without primary mutation before integration | Failure/cancellation before integration removes the linked worktree and restores the existing transaction journal without needing to undo speculative primary writes. |
| Durable transaction authority stays singular | The existing artifact transaction journal remains authoritative and owns the isolation section/execution root. No second transaction system was introduced. |
| Preserve compatibility for direct API callers | Low-level transaction API remains `direct` unless isolation is explicitly requested; CLI apply is the surface that prefers worktree isolation. |
| Truthful unsupported-source behavior | Non-Git, unborn, or dirty repositories fall back to direct mode with an explicit `fallback_reason`; Devtool does not pretend they are isolated. |
| Worktree lifecycle cleanup | Completion/rollback removes linked worktree checkout and prunes Git administrative state. |
| Durable validation across process death | Validation executes in the transaction worktree while validation control/report state is anchored to the primary repository, allowing later `validate continue` to recover the isolated execution root. |
| Self-refresh source is transaction tree | `DEVTOOL_TRANSACTION_REPO_ROOT` is exported and `python -m devtool.transaction_worktree self-refresh-root` resolves it before falling back to the normal Devtool checkout. This milestone does **not** reinstall Devtool; gate artifacts can consume the contract later. |
| Native roadmap evidence | `roadmap-git-frx03` declares the real linked-worktree fixture, self-refresh probe, predecessor GIT-FRX-02 test, GIT-FRX-03 test, and EXO baseline. |

## Architecture

The primary repository owns the durable transaction record, private recovery refs from GIT-FRX-02, reservations, and recovery identity. For a clean committed Git source, transaction creation then adds a detached linked worktree under the transaction directory at the captured start SHA.

The runner receives two roots:

- `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT`: unchanged user checkout and durable transaction authority.
- `DEVTOOL_TRANSACTION_REPO_ROOT`: isolated execution tree used by artifact lifecycle work and later validation/commit.

When the transaction succeeds, integration first re-verifies the primary HEAD, index identity, and user worktree identity. Devtool-owned *untracked* validation state/report files are excluded from that drift identity, while tracked files under the same paths remain authoritative and still block integration if modified.

If the isolated worktree contains a new commit, integration advances only the captured branch ref and only if it still points to the captured start commit. It then materializes that exact result in the primary checkout. Any remaining uncommitted transaction-tree changes are synchronized afterward so no-commit and after-commit-hook semantics remain truthful.

## Deferred validation convergence

GIT-FRX-03 splits validation execution from validation durability intentionally:

1. apply occurs in the linked transaction worktree;
2. validation session/control/report state is persisted under the primary repository's `.devtool` state/report roots;
3. a later `devtool validate continue` identifies the pending transaction from the primary checkout;
4. Devtool reloads configuration from the transaction execution root;
5. validation and embedded artifact validator execute there;
6. commit occurs in that worktree when requested;
7. successful finalization integrates the exact result into primary and removes the linked worktree.

This was found by the audit loop; without this split a deferred validation session could become undiscoverable after the apply process ended.

## Native execution contract

`roadmap-git-frx03` resolves to six nodes:

1. `job:source-syntax`
2. `job:transaction-worktree-fixture`
3. `job:self-refresh-root-probe`
4. `test:roadmap-git-frx02`
5. `test:roadmap-git-frx03`
6. `test:roadmap-workflow-exo-baseline`

The worktree fixture creates and removes a real linked Git worktree. It deliberately declares no known output set, so scheduler planning treats it conservatively rather than falsely claiming read-only behavior. The self-refresh probe is read-only.

On the audit host, native workflow preflight/invoke stops at the expected configured `native-termux` versus host `desktop-linux` execution-environment boundary. The contract was not weakened to make the analysis host pass.

## Audit loop

Final bounded regression evidence:

- transaction / rollback / finalize compatibility: **82 passed**;
- artifact / Git-state / federation scheduler / roadmap / GIT-FRX-03: **142 passed**;
- resumable validation / validation reports / process-resume behavior: **86 passed**;
- completion + release-integrity: **25 passed + 32 subtests**;
- selected CLI apply/rollback/history/commit regressions: **7 passed**;
- first-class `roadmap-git-frx03` test: **15 passed**, JUnit failures/errors = 0.

The audit loop additionally exercised:

- isolated no-commit integration;
- isolated exact-commit integration;
- rollback before integration;
- unrelated primary user drift protection;
- tracked `.devtool` operational-path drift protection;
- dirty/unborn fallback truth;
- direct compatibility;
- real worktree fixture cleanup;
- deferred validation pause/resume across separate CLI invocations;
- durable validation report survival after worktree cleanup;
- zsh completion exposure for `--isolation worktree|direct`.

## Gate policy

This milestone is **not** a validation gate. The overlay contains no finalize hook and performs no `pip install -e ~/Code/devtool`. It only establishes the transaction-root self-refresh contract that a later gate can consume.
