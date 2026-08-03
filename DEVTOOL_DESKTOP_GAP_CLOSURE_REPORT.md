# Devtool Desktop HUD and Control Center Gap Closure

This patch closes the delivery-audit gaps found after the Phase 1-10 pass.

## Closed gaps

- Desktop-launched processes keep stdout and stderr distinct. The run handle now stores `stdout_lines`, `stderr_lines`, a combined channel-prefixed log, current phase hints, elapsed time, and parsed diagnostics.
- The Control Center Run page blocks duplicate launch clicks while a desktop-launched run is still active and re-enables Launch after exit.
- Failed desktop-launched runs are published into the HUD state directory so the HUD failure summary and diagnostics copy path show the failure instead of hiding it in a log-only cul-de-sac.
- Preferences now persist maximized state, HUD raw-details expansion, high-contrast preference, and per-page filters for history, artifacts, diagnostics, and targets.
- The HUD restores its raw-details expansion state and saves it on change.
- Control Center/HUD roots gain an explicit high-contrast CSS hook when requested.
- GTK release gates now include constrained 1024x722 startup probes for both Control Center and HUD, using the short work area that reproduced the original Adwaita height warning.
- Added an opt-in GTK thread guard for release/startup probes via `DEVTOOL_GTK_THREAD_GUARD=1`; guarded GTK mutations from worker threads are logged and raised.
- CI and release qualification matrix include the constrained-layout gates.

## Validation performed

```text
PYTHONPATH=. pytest -q tests/test_desktop_gap_closure.py tests/test_desktop_*.py tests/test_ui_ux_slice2.py
84 passed in 4.04s

python3 -m compileall -q devtool tests
bash -n desktop/gtk-control-center/package-deb.sh install.sh
```

A full unfiltered `pytest -q` was also attempted. It reached 74% with no visible failure before the sandbox timeout, matching the prior long-suite timeout behavior.
