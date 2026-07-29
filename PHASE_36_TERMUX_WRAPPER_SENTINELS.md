# Phase 36: Termux Wrapper Sentinels

Phase 36 extends the Android run-command trace across the shell boundary. The app now wraps every Termux RUN_COMMAND launch with an authenticated shell-side sentinel that reports durable checkpoints before the requested command starts and records the final wrapper exit code before Termux returns the pending result.

## Checkpoints

- `TERMUX_SHELL_STARTED`
- `TERMUX_WRAPPER_STARTED`
- `SHELL_RESOLVED`
- `TOKEN_MARKERS_OK`
- `TOKEN_MARKERS_MISSING`
- `WORKDIR_CHECKED`
- `WORKDIR_MISSING`
- `ENV_READY`
- `COMMAND_FOUND`
- `COMMAND_LOOKUP_SKIPPED`
- `COMMAND_NOT_FOUND`
- `EXEC_STARTING`
- `EXIT_CODE_RECORDED`
- `FIRST_OUTPUT` reserved for future bounded-output sentinels

## Behavior

The wrapper emits authenticated broadcasts to the existing `BridgeReceiver` using the same token and action namespace as normal HUD updates. Each broadcast carries `trace_checkpoint`, `trace_message`, `wrapper_stage`, `wrapper_command`, and a monotonically increasing synthetic timestamp so the bridge update gate accepts back-to-back sentinel events even when they happen inside the same second.

The requested command is executed inside a subshell, preserving stdout and stderr behavior while allowing the wrapper to capture the exit code and report `EXIT_CODE_RECORDED` before exiting with the original status.

## Scope

This phase is intentionally launch-focused. It does not add Repo Manager Doctor, path repair, root/chroot executor planning, or bridge roundtrip probes. Those remain later phases in the debugging-system roadmap.
