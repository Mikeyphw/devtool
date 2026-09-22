# Devtool EXO Execution Model — Final Convergence Seal

This document seals the 11-overlay EXO execution-model migration.

## Canonical truth

`ExecutionStore` is the semantic authority for a run. Typed events are reduced
before listeners observe them. Human labels are presentation only; identity is
`run_id`, `node_id`, `event_id`, and `process_id`.

The canonical snapshot schema is version 4. It preserves:

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
