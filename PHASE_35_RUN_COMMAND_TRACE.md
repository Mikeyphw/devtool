# Phase 35: Android Run Command Trace

Phase 35 adds an ordered run-launch trace on top of the Phase 34 debug recorder.
The goal is to distinguish UI request, command planning, Termux intent delivery,
RunStore creation, shell-start acknowledgement, first authenticated bridge
progress, watchdog waiting, result receipt, and final completion/failure.

## Scope

- Adds `RunCommandTrace` with stable ordered checkpoints.
- Records `UI_RUN_REQUESTED` from `MainActivity.runRaw`.
- Records Termux-side launch envelope checkpoints from `TermuxCommandRunner`.
- Adds explicit `trace_checkpoint` and `trace_message` extras to the existing
  Termux shell-start acknowledgement broadcast.
- Teaches `BridgeReceiver` to persist bridge trace checkpoints before normal HUD
  update handling.
- Teaches `RunStore` to append trace checkpoints to the existing execution log
  and persist them as `run_trace` debug events.
- Records first authenticated progress and active-running transitions.
- Records Termux result receipt and final completion/failure trace points.
- Adds root bridge run-path trace points for parity with the normal run button.

## Non-goals

- This is not the Phase 36 wrapper-sentinel work. Phase 35 uses the existing
  shell-start acknowledgement and app-side events. Phase 36 can replace the
  shell prelude with a fuller wrapper that proves `COMMAND_FOUND` and
  `EXEC_STARTING`.
- This does not add the Repo Manager Doctor. That starts in Phase 38.
