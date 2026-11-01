# R38-R1 — Serial tracked-source write audit (opt-in)

Status: **source implementation**, NOT filesystem sandbox qualification, hosted
acceptance or release. This slice extends the existing MP09/native workflow
scheduler and runner terminal-event path; it does not create a new scheduler.

## Operator switch

Set `DEVTOOL_R38_WRITE_AUDIT=enforce` for an invocation of an existing
repository-configured workflow. Default is `off`. Direct library callers may
pass `write_audit_mode="enforce"` to `run_workflow_specs`. Only `off` and
`enforce` are recognized; unknown values fail before execution. Dry-run does
not perform a write audit and must not be cited as proof.

Enforce mode requires a top-level clean **tracked** Git worktree with a
stable HEAD and refuses any request for parallel scheduling. It forces a
single live worker even for independent ready DAG nodes, disables historical
result reuse, records a pre/post snapshot for each actually executed command,
and checks changed Git-tracked paths against declared repository write scopes.
A `read_only` node must change no tracked paths. Changed bytes are compared
for already-dirty (previous-node) tracked files, not merely path presence.

A violation or unverifiable state converts the native command result to a
failure **before** the runner publishes its terminal TaskFinished/EXO event;
the existing DAG scheduler blocks dependent commands. Canonical terminal
truth therefore agrees with the result for native executed commands.
Proof lives in `OperationResult.metadata.workflow_r38_tracked_write_audit`:
`status`, stable `code`, `node`, `observed`, `outside_scope`, and `limits`.
Allowed changes do not establish that the process was fully isolated.

## Non-goals and explicit safety boundary

- This is *not* kernel namespace/mount isolation, a continuous watcher, or a
  security guarantee. A process may write and restore files between snapshots.
- Only Git-tracked paths and working-tree contents/mode changes are observed.
  Ignored, untracked, external, cache and run-temp files are **not** covered.
- Concurrent per-node attribution is unsupported: explicit parallel requests
  are refused in enforce mode. Other independent host processes may still
  mutate the worktree; an unverifiable result must not imply isolation.
- A clean tracked baseline is required; dirty tracked files fail admission.
  Maximum observed changed paths: 2048; maximum individual tracked file:
  32 MiB. Unsupported file types, Git failures, HEAD changes and unsafe
  path traversals fail closed.
- The audit is not authorization to delete, release, sign, or distribute
  artifacts. R38 scratch isolation, cache correctness, critical-path
  benchmarks, cross-process observation and real consumer qualification
  remain separate planned work.

## Evidence and acceptance

Targeted source tests include allowed/denied writes, read-only and unknown
scope contracts, per-part status, unchanged legacy execution, parallel
refusal, dirty worktree, invalid modes, Git HEAD drift, symlinks, oversized
files, actual native subprocess execution and scheduler regression tests.

The source-pinned overlay runs only these related tests and creates one Git
commit after success. The 24-shard full Python regression must be dispatched
against the *resulting* commit in GitHub Actions. This is independent of
MMRL/Android release qualification and is not a device gate.
