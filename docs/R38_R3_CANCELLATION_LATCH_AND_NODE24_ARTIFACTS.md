# R38-R3 — Cancellation admission latch + Node.js 24 artifact Actions

## Scope

This implementation closes a narrow native DAG scheduling race where a stop signal
may arrive between the scheduler's outer cancellation poll and a worker's process
spawn.  It does not introduce a second scheduler or claim OS-level termination,
reboot recovery, or orphan-process detection.  Earlier R38-R1 tracked-write audit
and R38-R2 scratch/cache routing remain independent and opt-in.

## Runtime behavior

* One thread-safe, per-run cancellation latch preserves the first observed stop.
  The sink's token may subsequently reset; this is not authority to restart work.
* Every ready-queue admission rechecks the latch, and the node observer's
  `started` callback is followed by another check before worker submission.
* Every worker rechecks on entry, before snapshots or `run_specs`, and forwards
  cancellation to the existing native synthetic-result and terminal-event path.
* The worker HUD proxy shares the latch with the scheduler and cannot clear it.
  Running child-process cancellation remains under the existing runner watcher.
* A progress sink that raises during polling retains the previous fallback
  semantics; this does not claim a successful stop for a failed external signal.

## CI Node.js 20 warning

The full 24-shard workflow uses `actions/upload-artifact@v7` and
`actions/download-artifact@v8`. Both Actions declare `runs.using: node24`.
The previous v5 majors still triggered a Node.js 20 deprecation warning even
on a Node 24 runner. The checkout/setup-python v6 majors remain unchanged.
Other workflows are out of scope; JavaScript library `punycode` or `Buffer()`
warnings may still appear independently and are not runtime-version warnings.

## Verification and authority

Focused cancellation tests cover a stop inside the started observer before
spawn, a transiently cleared stop token, and unchanged non-cancel execution.
R38-R2 tests assert the new workflow Action majors to avoid future stale
contracts. Full repository pytest is hosted only after push. Source integration
is not real-host process-tree acceptance, release signing, or a device gate.

## Residual R38 work

R38-R3 follow-up: independent tests for descendant-process survival, host
interruption, stale recovery evidence, and durable restart semantics. R38-R4:
repeatable DAG/cache performance benchmarks. R39: architecture consolidation.
