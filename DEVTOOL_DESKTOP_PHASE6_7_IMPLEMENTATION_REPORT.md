# Devtool Desktop HUD and Control Center Phase 6 + 7 Report

## Scope

Desktop GTK4 HUD and GTK4 Control Center only. No Android files changed.

## Phase 6 implemented

- Added `devtool/desktop/run_process.py`.
- Control Center Run page now launches reviewed commands with direct `argv`, not `sh -lc`.
- Each desktop-launched run receives `DEVTOOL_DESKTOP_RUN_ID`.
- Launches write per-run logs under the Devtool desktop state directory.
- stdout and stderr are merged, redacted, and streamed into the Run page.
- Graceful cancel sends SIGTERM to the process group.
- Force cancel sends SIGKILL to the process group and falls back to process kill if process groups are unavailable.
- The Run page exposes live run identity, PID, log path, cancel, and force-cancel controls.

## Phase 7 implemented

- Added `devtool/desktop/preferences.py`.
- Control Center persists selected page and window size.
- HUD persists window size.
- Appearance can follow system or be forced through persisted preferences / `DEVTOOL_DESKTOP_APPEARANCE=light|dark`.
- Reduced motion is honored through preferences / `DEVTOOL_REDUCED_MOTION=1`.
- Control Center and HUD use Adwaita semantic colors for the desktop phase additions.
- Added keyboard shortcuts for refresh, close, page switching, and existing HUD actions.
- Added GTK accessibility labels/tooltips for key actions and live output areas.

## Validation

```text
PYTHONPATH=. pytest -q tests/test_desktop_gtk_phase6_7_run_theming.py
5 passed in 0.75s

PYTHONPATH=. pytest -q tests/test_desktop_gtk_phase4_5_truthful_hud_control_center.py tests/test_desktop_gtk_phase2_3.py tests/test_desktop_gtk_phase0_1_startup_logging.py tests/test_desktop_gtk_phase6_7_run_theming.py
22 passed in 1.10s

PYTHONPATH=. pytest -q tests/test_desktop_*.py tests/test_ui_ux_slice2.py
73 passed in 3.25s

python3 -m compileall -q devtool tests/test_desktop_gtk_phase6_7_run_theming.py
bash -n desktop/gtk-control-center/package-deb.sh install.sh
```

## Promise audit

- Direct argv launch: implemented through `DesktopRunLauncher.start(review.argv, ...)`.
- Live output: implemented by stdout/stderr pump thread and GTK idle append.
- Run identity: implemented by generated desktop run id and environment export.
- Cancel and force-cancel: implemented through process-group signals.
- Accessibility: implemented for core Control Center and HUD actions plus live output areas.
- Appearance/theme: implemented with Adwaita style-manager hooks and semantic CSS colors.
- Reduced motion: implemented for Control Center loading spinner and stored preference model.
- Persisted window/page state: implemented through desktop preferences.

## Artifact hygiene

- No `__pycache__`, `.pytest_cache`, generated build output, or reserved `.devtool-artifact` files are included.
