# Devtool EXO Execution Model — Final Convergence Seal

This document seals the 11-overlay EXO execution-model migration.

## Canonical truth

`ExecutionStore` is the semantic authority for a run. Typed events are reduced
before listeners observe them. Human labels are presentation only; identity is
`run_id`, `node_id`, `event_id`, and `process_id`.

The EXO-11 seal originally qualified canonical snapshot schema version 4. The current additive schema is version 7 (EXO promise remediation introduced v5, WRAP provenance v6, and FRX repository identity v7). Current consumers must use the emitted `schema_version` rather than pinning the historical EXO-11 value. It preserves:

- run lifecycle and graph-seal state;
- direct, recursive, and leaf operation accounting;
- measured progress separately from lifecycle resolution;
- node dependencies, progress, outcomes, process ownership, diagnostics,
  artifacts, evidence, and metrics;
- process transport provenance for SSH, PRoot, and chroot execution.

Terminal run status never manufactures success for planned/unobserved work.

## Persistence and recovery

`ExecutionProjectionSubscriber` writes:

- `execution.json` — current canonical snapshot;
- `execution-events.jsonl` — compact, redacted replay journal;
- `execution-checkpoint.json` — periodic/terminal checkpoint;
- history `<run_id>/execution.json` — terminal canonical snapshot.

Process-output journal records are bounded. Command/provenance secret values are
redacted before they enter canonical durable telemetry.

`recover_event_bus()` replays typed journals, retains unknown future event
types, restores the authoritative store, and advances the event sequence cursor
so live publication resumes without identity reuse.

## Surface convergence

Terminal/TUI, GTK/Desktop, Android Live HUD/RunStore, IPC, persistence, and
history consume the canonical execution snapshot. Legacy state remains a
compatibility/recovery input only where required.

A stale legacy `ok / 100%` record cannot override a canonical `failed / 43%`
run.

## Control identity

Operation controls carry stable `run_id` and, when a specific operation is the
target, `node_id`. Retry/cancel/resume workflows therefore do not depend on a
mutable display label.

## Compatibility

The migration remains additive for legacy event names and UI envelopes.
Unknown event types decode as `UnknownExecutionEvent` so newer producers do not
break older recovery readers.

## Release-seal rule

The EXO roadmap is complete only when validation gate 3 passes the explicit
EXO/core/runtime/surface compatibility selector and the before-commit self-host
proves persistence, replay recovery, cross-surface agreement, redaction, and
stable operation identity from the newly installed checkout.


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
