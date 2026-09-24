# EXO Roadmap Promise Audit — Post-EXO-11

Audit input: final repository snapshot after EXO-11.

## Result

The original 11-overlay acceptance selector passed (480 tests + 7 subtests), but
source-level promise auditing found three gaps not covered by that selector.

### Gap 1 — Android graph persistence was incomplete

`RunRecord` had `executionJson`, schema, sequence, and event ID fields, but
`DevtoolDatabase` did not store them. A run reloaded from SQLite therefore lost
the typed execution graph and fell back to flattened `steps_json`.

Remediation:
- SQLite schema v11 persists all canonical execution graph fields.
- v10 → v11 migration adds the columns without deleting history.
- history rendering reconstructs from persisted `executionJson` when available.

### Gap 2 — Android graph truth was flattened

The Android typed graph omitted `depends_on` and `blocked_by`, converted
`blocked` to `failed`, and the Main/Overlay progress bars forced failed/skipped
steps to 100%.

Remediation:
- dependencies, blockers, parent, kind, and detail survive the typed model and
  `steps_json` compatibility projection;
- `blocked` remains a distinct terminal lifecycle state;
- live and persisted graph UI displays relationship context;
- partial failed/blocked/skipped progress remains the measured value instead of
  being synthesized as 100%.

### Gap 3 — Evidence references were not bounded

Process output was bounded, but process/diagnostic/node evidence-reference
collections could grow without limit.

Remediation:
- max 64 evidence references per canonical owner;
- max 2048 characters per evidence reference;
- max 4096 characters per log reference;
- durable journal evidence/log fields obey the same bounds;
- oldest causal and newest diagnostic evidence are retained when the cap is
  exceeded.


### Gap 4 — synthetic/inferred transition provenance was not first-class

The scheduler already knew why workflow nodes were synthetically skipped,
blocked, or cancelled, but the canonical node snapshot exposed only the final
status. Consumers therefore had to inspect compatibility metadata to tell an
observed runner transition from a scheduler-inferred transition.

Remediation:
- `NodeFinished` carries `transition_origin` and `transition_reason`;
- canonical node snapshots expose those fields directly;
- dependency `NodeBlocked` transitions are marked as inferred;
- scheduler-created workflow terminal events use
  `transition_origin=scheduler_inferred` and preserve the reason;
- Android typed graph/history carries and presents the same provenance;
- the canonical execution snapshot advances from schema v4 to schema v5.

## Promises confirmed delivered

The audit also reconfirmed:
- canonical run/node/event/process identity;
- typed event protocol + forward-compatible unknown events;
- serialized EventBus reduction/listener ordering;
- authoritative ExecutionStore/reducer and graph sealing;
- direct/recursive/leaf accounting and measured-vs-resolution semantics;
- Gradle/npm/.NET/generic runner child adapters;
- Gradle cached/up-to-date/no-source outcome preservation;
- process/diagnostic/artifact producer ownership and diagnostic dedupe IDs;
- redacted durable command/provenance telemetry;
- SSH/PRoot/chroot provenance;
- journal replay/recovery, checkpoints, and sequence continuation;
- Tasker/IPC/history canonical projections;
- terminal/TUI and Desktop/GTK canonical truth migration;
- Android typed execution transport;
- stable `run_id`/`node_id` control payloads;
- cross-surface stale-legacy-state rejection.

## Current compatibility note

This report records the post-EXO-11 remediation point where transition provenance advanced the canonical snapshot to schema v5. Later additive campaigns advanced the same snapshot to v6 (WRAP invocation provenance) and v7 (FRX repository identity), while retaining the EXO semantics audited here. The current schema version is centralized as `EXECUTION_SNAPSHOT_SCHEMA_VERSION`; EXO tests assert the transition-provenance promise is available in v5 or later instead of pinning the historical value.


## Current lifecycle convergence addendum

A later audit found that embedded artifact lifecycle scripts (preflight, validation,
finalize/before-commit/after-commit) still used Tasker/Rich phase callbacks as the
primary live-progress path while their subprocesses bypassed the authoritative EXO
node/process graph. That was not full EXO convergence.

The current re-seal routes every embedded hook through `HookExecutionAdapter`:
- the lifecycle phase is a canonical `artifact-hook` EXO node;
- the actual spawned script runner is a canonical EXO process;
- failures carry exit code, failure kind, diagnostics, and durable log evidence;
- retries receive distinct hook-attempt node identities;
- `ExecutionProjectionSubscriber` receives these events, so terminal/desktop/Android/IPC
  surfaces can render the same authoritative execution truth;
- Tasker/Rich phase callbacks remain compatibility projections, not lifecycle authority.

This addendum is enforced by `tests/test_exo_lifecycle_hook_convergence.py` and the
current re-seal contract.
