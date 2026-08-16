# Devtool Android Chroot Executor Fix Report

## Problem

A Termux-hosted Android chroot validation could look stuck in the non-verbose TUI while the verbose command showed a nested `chrootctl exec` shell that attempted to switch to the requested Linux user with `runuser` or `su`.

Two concrete defects were found:

1. The non-interactive `runuser`/`su` probes were unbounded. In Android chroot environments they can wait for PAM/login/TTY behavior even when `chrootctl start --login` works interactively.
2. The Android HUD bridge path in `run_auto_chroot()` referenced `config.mappings`, which is not a `RunnerConfig` field. The correct field is `config.path_map`.

## Fix

- Added bounded user-switch probes with `timeout -k 2s Ns` when `timeout` is available.
- Added a shell-level probe deadline fallback when `timeout` is unavailable, including TERM/KILL cleanup and exit `124`, so probes are still bounded on minimal chroot images.
- Added a non-login `su user -s shell -c ...` fallback before the existing login `su - user ...` fallback.
- Improved the user-switch failure message to say whether the probes failed or timed out and how long they waited.
- Added a non-verbose inactivity hint for long silent full-process chroot executions. The hint includes the process ID, log path, and `-v`/`DEVTOOL_TERMUX_CHROOT_VERBOSE=1` guidance.
- Added a generic runner stall diagnostic for host-UI `chrootctl exec` command specs, so Android/Gradle chroot phases also surface a no-output hint without waiting for verbose mode.
- Fixed Android HUD bridge identity resolution to use `config.path_map`.

## Operator controls

- `DEVTOOL_CHROOT_USER_SWITCH_TIMEOUT=8` controls the user-switch probe timeout. The value is clamped to 1..300 seconds.
- `DEVTOOL_TERMUX_CHROOT_STALL_HINT_SECONDS=30` controls the silent-run hint interval. Set it to `0` to disable hints.

## Validation

Focused validation passed:

```text
194 passed, 12 subtests passed
```

Covered suites:

- `tests/test_termux_chroot_runner.py`
- `tests/test_execution_environments.py`
- `tests/test_runners_termux.py`
- `tests/test_android_debug_phase36.py`
- `tests/test_android_debug_phase38.py`
- `tests/test_android_debug_phase39.py`
- `tests/test_hud.py`
- `tests/test_termux_runtime_proot.py`

## Follow-up audit

The first packaged overlay still had a promise gap: if the chroot image did not provide a `timeout` binary, the fallback branch ran the probe command directly and could hang. v2 closes that gap with a bounded shell fallback and a regression test.

The v2 audit also found that the first overlay only added the stall hint to the full-process auto-chroot path. Host-UI wrapped command phases use the generic runner, so v2 adds chroot-aware stall diagnostics there too.
