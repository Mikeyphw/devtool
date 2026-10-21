# RM-02 — Dependency-ready workflow scheduling

## Merge-window decision

RM-02 and RM-03 were reviewed together. They remain **separate overlays**:
RM-02 replaces the native workflow scheduling loop; RM-03 adds canonical
cross-system EXO/Actions identity, new remote-verified job types, and a lineage
query. Their boundary is deliberately the existing runner event API. Merging
would make source-bound rollback encompass substantially unrelated authority
and remote state.

## Behavior

When `[workspace.scheduler] parallel = true` is enabled, execution now keeps
one bounded pool alive for the complete native workflow. After **each observed** node
completion, its terminal status releases its dependents for consideration.
Before admitting more work, the scheduler harvests futures already terminal
in its worker pool, including completions omitted from a previous wait snapshot.
A candidate launches only after all dependencies have passed and when worker
slots, Gradle/.NET caps, exclusive resource classes, global reservations,
read/write scopes, and RM-01 typed outputs permit it.

Candidate ordering follows the graph's stable expansion order. Completed
results and raw output are returned in stable **graph order**, not thread
finish order. The planner still returns `waves` (now also `static_waves`) for a
conservative visualization. They are **not execution barriers**.

A completed `WorkflowExecutionPlan` exposes:

- `actual_start_order`: nodes submitted to the live worker pool;
- `actual_finish_order`: live terminalization order, including synthetic states;
- `node_outcomes`: authoritative runner-derived node states;
- `resource_wait_reasons`: recorded safety reasons that prevented admission;
- `as_dict()["runtime_scheduling"]`: one grouped machine projection.

Node result metadata includes stable admission ordinal, admission mode, elapsed
scheduler time, and terminal ordinal. The native runner still owns executable
start/finish and EXO event emission. No new remote dispatch or synthetic
success authority was introduced.

## Failure, cancel, and compat policy

- An unsuccessful dependency never releases its successors for actual execution.
- `continue_on_error=true` admits independent unaffected successors. Without
  it, fail-fast skips new independent work **once failure has been observed**
  while in-flight work is drained. Already-admitted tasks are not revoked.
  If B completes and releases D before unrelated A fails, D may legitimately
  run; wall-clock sleep durations alone cannot prove a fail-fast violation.
- Native runner synthetic skip/block/cancel results and events are preserved.
- External cancellation prevents additional admissions; it is cooperatively
  propagated to already running workers by existing runner controls.
- Terminal futures are harvested ahead of admission; an already-completed
  failure cannot be bypassed because an earlier wait returned another worker.
- Worker exceptions produce failed results, not a hung queue.
- Empty/one-node workflows, disabled parallelism, and dry-run retain the
  existing sequential runner path.
- RM-01 temp-output isolation, hash capture and cleanup retain their contract.
- Scope metadata remains an **admission promise**, not a filesystem sandbox;
  undeclared side effects are the caller's responsibility.

## Scope and next overlay

RM-03 will consume the stable node keys and existing EXO events, then add typed
remote-run correlation and one navigable lineage API. RM-02 does not claim to
implement RM-03, targeted retries (RM-04), universal provenance (RM-05), or
multi-job GitHub workflow export (RM-06).
