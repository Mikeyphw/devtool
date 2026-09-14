# Overlay 9 of 15 — Core runtime, events, IPC, plugins and hooks

## Scope

This overlay closes the remaining S09 audit bucket after the earlier Overlay 1 IPC ownership emergency fixes. It hardens the runtime event bus, JSON event/listener output, local IPC state ownership and monotonicity, embedded hook execution, hook result validation, plugin discovery/doctor isolation, and core operation result truth.

## Findings closed

- S09-01 — Event bus listener failures are contained so one bad listener cannot break command execution.
- S09-02 — Event payloads/logging are recursively JSON-safe, including `Path`, bytes, sets and nested metadata.
- S09-04 — IPC terminal states cannot resurrect to running/in-progress states.
- S09-05 — Equal sequence/timestamp candidates are not considered newer and cannot stale-overwrite current state.
- S09-06 — IPC timestamps are compared chronologically with timezone normalization, not lexicographically.
- S09-07 — `IpcPublisher.connect()` registers an initial state containing the authoritative run ID and monotonic sequence.
- S09-08 — Hook timeouts terminate the full process group so descendants cannot keep mutating after timeout.
- S09-09 — Hook write-boundary snapshots detect ignored files as well as ordinary Git status changes.
- S09-10 — Structured hook result parsing rejects malformed fields, including boolean `schema_version`.
- S09-11 — Malformed plugin descriptors are contained as discovery errors instead of crashing discovery.
- S09-12 — External plugins cannot silently override built-in plugins.
- S09-13 — Plugin doctor exceptions are returned as failed required checks instead of escaping.
- S09-R2-02 — IPC register/publish now rejects disagreement between message run ID and embedded state run ID.
- S09-R2-04 — `broker_is_running()` now requires a real Devtool `pong` with expected protocol/schema metadata.
- S09-R2-05 — IPC publisher payload serialization is JSON-safe for non-JSON state values.
- S09-R2-06 — `CommandResult.from_operation()` treats fatal issues as failures even when return code is zero.
- S09-R2-07 — Hook JSON-v1 payload context is JSON-safe before serialization.
- S09-R2-08 — Hook Python runner booleans parse string `"false"` as false.
- S09-R2-09 — Hook write-boundary snapshots detect empty directory creation.

## Implementation notes

- `devtool.core.events` now isolates listener exceptions and sanitizes event dictionaries before logging/JSON collection.
- `devtool.ipc` now serializes safely, assigns publisher-owned run IDs/sequences, enforces run-ID consistency, rejects terminal resurrection and equal-identity overwrites, parses timestamps as datetimes, and verifies actual broker ping/pong semantics.
- `devtool.hooks.engine` now starts hook subprocesses in a new process session and kills the process group on timeout. Python hook runner options use strict boolean parsing.
- `devtool.hooks.isolation` now keeps a repository state snapshot even in Git repositories, allowing ignored files and empty directories to participate in write-boundary checks.
- `devtool.hooks.result` rejects malformed structured hook outputs and sanitizes evidence/annotations.
- `devtool.plugins.registry` validates external descriptors, refuses built-in overrides, rejects duplicate plugin names, and contains doctor exceptions.
- `devtool.core.results` promotes fatal issues into result errors and non-zero failure truth.

## Validation run in this environment

```text
17 passed  — tests/test_rem09_core_runtime_ipc_hooks.py
168 passed — IPC, hooks, artifacts, plugin, diagnostics/session regression sweep
140 passed — overlays 1–9 remediation regression sweep
```

A wider `test_arch_final_seal.py` golden contract check was not counted in this overlay gate because it has an existing golden-file mismatch unrelated to the S09 runtime changes and belongs to the final seal/golden-refresh track.
