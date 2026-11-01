# R39-R3-R1 — Retire duplicate tracked-write proof generation

## What was retired

R38-R1 previously assembled the same `snapshot -> verify_node -> fail-closed
proof` path twice inside the native scheduler: the runner terminal-hook path
and a defensive fallback for runners returning results without hook metadata.
Both now call the same `workflow_write_audit.attest_node_transition` function.
This removes the redundant independently maintained proof constructor.

The authoritative terminal hook still rejects scope violations *before* the
runner emits terminal success. The fallback remains because runners/adapters
may return a result without having called the hook; it still fails closed and
records `workflow_node_state=failed` when evidence disagrees. The shared
attestation helper refuses unverifiable snapshots and does not advance its
cursor in that case. A synthetic fallback is not proof of terminal-event
atomicity for arbitrary external adapters.

## Retirement boundary

This is a **narrow deduplication**, not removal of the legacy `run_specs` engine.
R39-R2 proved only a bounded 5-case serial comparison. It did not qualify
parallel execution, historical result reuse, distributed/SSH/Android execution,
interrupted-host recovery, or device/release workflows. The native scheduler
still uses `run_specs` as a subprocess runner; removing it would be unsafe.

No default mode, scheduler capacity, source audit rules, scratch cache
routing, output metadata schema, rollback authority, or CI release gate changes.
These unqualified retirement lanes remain explicitly open for further R39
work and NEXT-11 independent acceptance.

## Acceptance

- Targeted unit/integration tests cover both hooked and fallback audit paths,
  allowed/outside-scope writes, refused snapshots, and legacy runner preservation.
- Native Devtool transactional application with source-preimage pin, automatic
  commit and rollback on failure.
- Hosted 24-shard full Python regression against the exact pushed commit.
- This is *source integration*, **not** real-device or release qualification.
