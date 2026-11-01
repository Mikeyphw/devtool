# R39-R1 — Shared execution-policy authority (non-release)

## Scope and motivation

The existing native workflow scheduler is the authoritative owner for DAG admission,
node execution, cancellation, terminal events, resource reservations, and the
run-temporary-file ownership lifecycle. R38-R1 tracked-write auditing and R38-R2
scratch/cache routing were implemented as independent opt-in branches within this
scheduler; the branches each parse their own mode and independently mark commands
non-reusable. In the prior implementation, a bad scratch override on a later
command could cause a refusal **after** earlier CommandSpec metadata had already
been changed by the audit preflight.

R39-R1 consolidates **only** this overlapping preflight policy in
`devtool/workflow_execution_policy.py`. It is a pure, side-effect-free authority
that returns an immutable execution policy. `workflow_scheduler.py` still owns
Git source snapshotting, worker-limit enforcement, subprocess execution, R38
terminal audit hooks, descriptor-owned scratch cleanup and cancellation semantics.

## Compatibility / intentionally frozen behavior

- Default `off` modes do not change environments, metadata, or execution paths.
- `DEVTOOL_R38_WRITE_AUDIT=enforce` still requires a native, serial workflow;
  invalid modes and missing plans retain their original fail-closed reasons.
- `DEVTOOL_R38_SCRATCH_ISOLATION=enforce` still requires native nodes and
  refuses reserved environment variables (case-insensitive) on **any** command.
- Both enforced modes still disable historical result reuse; scratch isolation
  retains precedence for `reuse_disabled_reason`, as before consolidation.
- Dry-run validates mode syntax but does not enforce safety modes, allocate
  scratch roots, or change command metadata.
- All mode/environment preflights and the clean-tracked-base audit complete
  before caller `CommandSpec.metadata` is modified. A refusal is atomic with
  respect to those metadata fields. This strengthens the former failure path.
- Recovery, event lineage, output-scope leases, ownership records, existing
  engine selection, and CLI syntax remain unchanged.

## Test strategy

Focused R39 tests exercise the mode matrix, source selection, reserved variable
collisions in later commands, fallback environment, historical error precedence,
metadata unchanged after rejection, default behavior, and a real subprocess with
both audit and scratch routing. Regressions include R38-R1/R2/R3/R4 plus the
existing MP09/REM04/R30/EXO event and cancellation suites. Local synthetic
reconstruction is **not** equivalent to exact production-HEAD CI.

## Explicit non-goals

This is **R39-R1 source consolidation**, not wholesale legacy/native retirement.
Legacy adapters must not be removed before measured parity and a reversible
migration. The policy does not establish host sandboxing, physical isolation,
real build-cache benefit, external worker qualification, signer/device evidence,
release authorization, or formal NEXT-11 closure.

Post-push evidence: mandatory full 24-shard GitHub Python regression against
exact new SHA. Independent consumer/device and release gates remain separate.
