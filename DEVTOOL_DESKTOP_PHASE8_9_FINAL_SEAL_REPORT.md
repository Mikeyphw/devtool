# Devtool Desktop HUD and Control Center Phase 8 + 9 + Final Seal Report

## Scope

Desktop GTK4 HUD and GTK4 Control Center only. No Android source was changed.

This closes the remaining roadmap work after Phase 6 + 7:

- Phase 8: packaging, launchers, installer correctness.
- Phase 9: real GTK release qualification and mapped-window regression seal.
- Final seal: promise audit, artifact hygiene audit, and privacy/redaction audit.

## Phase 8 implemented

- Debian launchers continue to use `#!/usr/bin/python3` so PyGObject resolves to distribution packages.
- `package-deb.sh --verify` now extracts the generated package and imports PyGObject, GTK4, libadwaita, Rich, and Devtool with `/usr/bin/python3` plus the staged package path.
- Package verification checks stable absolute desktop-entry launchers:
  - `Exec=/usr/bin/devtool-control-center`
  - `TryExec=/usr/bin/devtool-control-center`
- Package verification still checks launchers, desktop metadata, metainfo, icons, checksum, version, GTK/libadwaita dependency set, and absence of Qt/QML runtime baggage.
- The installer still refuses the deprecated clipboard service path and never writes a systemd unit that invokes unsupported `--clipboard-monitor`.
- Desktop README now documents logs, startup diagnostics, shortcuts, package requirements, and the distinction between `--once` and real startup tests.

## Phase 9 implemented

- Added `devtool/desktop/gtk_smoke.py`.
- Added real startup-test commands for both desktop windows:
  - Control Center: `devtool control-center --backend gtk4 --startup-test`
  - HUD: `devtool-control-center-hud --startup-test`
- Startup probes run under the host display or `xvfb-run` when no display is present.
- Startup probes fail on:
  - missing display/Xvfb when required,
  - startup timeout,
  - non-zero exit,
  - GTK/Adwaita warnings,
  - critical messages,
  - height-overflow warnings such as the original `requested 816 px, 722 px available` failure.
- Release qualification no longer calls the JSON `--once` model snapshot a GTK smoke test.
- New release gates:
  - `desktop-gtk-imports`
  - `desktop-gtk-model-snapshot`
  - `desktop-gtk-control-center-startup`
  - `desktop-gtk-hud-startup`
  - `desktop-gtk-package`
- CI installs Xvfb and runs both mapped-window startup tests.
- `release/qualification-matrix.json` now describes the real GTK gates.

## Final promise audit 1: original defect coverage

| Original issue | Final state |
|---|---|
| HUD exceeded available window height | Covered by Phase 3 layout rebuild and Phase 9 warning matcher for impossible allocation warnings. |
| Giant red circular status | Replaced earlier by compact status chip. |
| Failure explanation buried | Failure-first card shipped earlier and remains covered by tests. |
| Conflicting diagnostic counts | Canonical diagnostic model shipped earlier and remains covered by tests. |
| Skipped stages looked complete | Honest stepper semantics shipped earlier and remain covered by tests. |
| Long overlay names enlarged window | Ellipsized title and tooltips shipped earlier. |
| Hardcoded dark-only experience | Phase 7 Adwaita preference support remains covered by tests. |
| Window presented after blocking work | Phase 2 immediate present and async refresh remain covered by tests. |
| Invisible startup exceptions | Phase 1 logging/dialog boundary remains covered by tests. |
| Wrong Python in packaged launcher | Phase 8 verifies `/usr/bin/python3` launch/import path. |
| JSON `--once` masqueraded as launch smoke | Phase 9 splits model snapshot from mapped-window startup tests. |
| Broken clipboard-monitor service | Phase 8 continues fail-closed installer behavior, no broken unit. |
| Misleading Control Center controls | Phase 5 implemented or removed them. |
| `sh -lc` execution | Phase 6 direct argv launcher remains covered by tests. |
| No integrated output/cancel | Phase 6 run view remains covered by tests. |
| No window/page persistence | Phase 7 preferences remain covered by tests. |
| Weak keyboard/a11y | Phase 7 shortcuts and labels remain covered by tests. |
| No real mapped-window gate | Phase 9 adds Xvfb startup probes. |

## Final promise audit 2: release-gate coverage

- Package verification now tests both artifact shape and staged runtime imports.
- Release qualification fails closed when `--require-desktop` or compatibility `--require-qt` is used and GTK imports/startup/package verification cannot pass.
- Optional desktop gates skip cleanly on non-desktop hosts when GTK dependencies are missing.
- CI invokes Xvfb startup tests directly, outside JSON snapshot mode.
- Documentation no longer presents `--once` as a real GTK launch test.

## Privacy and redaction audit

- Phase 1 startup/refresh logging redaction remains active for GTK failures.
- Phase 6 run logs use the shared redaction path before writing live output.
- Phase 8 package verification does not persist environment dumps or secrets.
- Phase 9 startup probes capture GTK stdout/stderr only and reuse existing redaction-aware application boundaries.
- No new clipboard export path was added.

## Artifact hygiene audit

Before packaging this phase, generated caches were removed:

- no `__pycache__`,
- no `.pytest_cache`,
- no `.pyc`,
- no generated build directories,
- no reserved `.devtool-artifact` inventory entries.

## Validation

```text
PYTHONPATH=. pytest -q tests/test_desktop_gtk_phase8_9_final.py tests/test_desktop_gtk_phase5.py tests/test_desktop_gtk_phase0_1_startup_logging.py tests/test_installer_ux.py
22 passed in 3.24s

python3 -m compileall -q devtool tests/test_desktop_gtk_phase8_9_final.py
bash -n desktop/gtk-control-center/package-deb.sh install.sh
PYTHONPATH=. pytest -q tests/test_desktop_*.py tests/test_ui_ux_slice2.py
79 passed in 3.52s

Patch apply-check against Phase 6+7 full source:
PYTHONPATH=. pytest -q tests/test_desktop_*.py tests/test_ui_ux_slice2.py
79 passed in 4.27s
```

`package-deb.sh --verify` was not executed in this sandbox because it now intentionally requires host GTK/libadwaita/PyGObject availability for `/usr/bin/python3`. The script-level syntax and source-contract tests passed; the real package verification should be run on the desktop release host with the documented Ubuntu/Debian dependencies installed.
