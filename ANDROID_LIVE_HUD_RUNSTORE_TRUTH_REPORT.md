# Overlay 12 of 15 — Android Live HUD bridge, run-store and root/Termux execution truth

## Scope

This overlay closes the remaining S12 Android Live HUD, run-store, root bridge and Termux execution-truth findings after the trust-boundary emergency seal.

Assigned findings closed: 23

- S12-01 through S12-14
- S12-R2-01, S12-R2-02, S12-R2-04 through S12-R2-10

## Implemented hardening

### Canonical run state

- Added bridge/run `generation` tracking to `HudUpdate`, `RunRecord`, `RunStore` and `BridgeUpdateGate`.
- `RunStore` now rejects stale bridge updates by generation, sequence and timestamp before touching persisted run state.
- Terminal run records cannot be resurrected by delayed non-terminal bridge events.
- Validation HUD updates now use the validation revision as both sequence and generation.

### Bridge sequencing

- `BridgeUpdateGate` persists per-run generation state and active-run generation state.
- Older generation broadcasts cannot replace newer active run state.
- Same-generation updates keep the existing sequence/timestamp ordering rules.

### Termux result identity

- `TermuxCommandRunner` now assigns a unique package-scoped PendingIntent action per execution id.
- Result PendingIntents carry an explicit generation matching the execution id.
- `TermuxResultService` rejects stale/mismatched Termux result callbacks before processing stdout, stderr, Tasker completion, validation results or HUD completion.

### Root bridge execution truth

- Replaced unbounded cached root executor with a bounded `ThreadPoolExecutor` and queue.
- Executor saturation is surfaced as a root-bridge error diagnostic instead of crashing UI command dispatch.
- Root command probing retries one time on transient `su`/pipe/busy failures.
- Existing bounded-output handling remains active for root bridge command and broker paths.

### Token lifecycle

- Bridge token generation is persisted and incremented on token regeneration.
- Token verification now records the verified generation as well as the verified hash.
- A stale verified hash from a previous token lifecycle cannot keep the bridge in the connected state after regeneration.

### Finite UI/log work

- Root log indexing now has an explicit per-file scan byte cap in addition to chunk count and source-file limits.
- HUD step parsing caps incoming semantic steps to a finite UI list.

## Validation

Executed locally with `PYTHONPATH=.`:

```text
4 passed   — tests/test_rem12_android_hud_runstore_truth.py
159 passed — S12 live HUD + AOPS/Termux/root/command-importer regression sweep
235 passed — S09-S12 runtime/HUD/validation/UI compatibility sweep
```

The overlay is intended as an intermediate remediation overlay and keeps validation optional in the artifact manifest. Apply with `--no-validate`; the final campaign seal owns full validation.
