# Devtool Desktop HUD / Control Center Phase 0 + Phase 1 Implementation Report

Base: `devtool-20260803-142916.tar.gz`
Scope: desktop GTK HUD and desktop GTK Control Center only. No Android files changed.

## Phase 0: reproduce and freeze startup failures

- Added `tests/test_desktop_gtk_phase0_1_startup_logging.py`.
- Added regression coverage for:
  - unexpected non-`RuntimeError` GTK launcher failures;
  - secret redaction in console/log output;
  - periodic GTK refresh failures continuing instead of killing the timer;
  - `control-center --page artifacts` being accepted;
  - new `--startup-test` option on direct Control Center and HUD launchers;
  - packaged launcher shebangs using `/usr/bin/python3`;
  - installer refusing the unsupported clipboard monitor service instead of writing a broken unit.

## Phase 1: crash handling, logging, dialogs, redaction

- Added `devtool/desktop/gtk_runtime.py` as the shared GTK runtime safety layer.
- Control Center and HUD now:
  - log GTK boundary failures to `$XDG_STATE_HOME/devtool/control-center.log` or `$XDG_STATE_HOME/devtool/hud.log`;
  - redact tokens, passwords, authorization headers, GitHub tokens, and API-key-like strings from logs and console messages;
  - catch ordinary `Exception` at activation boundaries instead of only `RuntimeError`;
  - present a startup error window with details, copy details, open log, and quit actions when activation fails;
  - wrap refresh callbacks so periodic failures keep the UI alive and keep the last visible state when possible.
- Control Center now presents the window before the first potentially blocking snapshot render, then schedules the first render through GTK idle handling.
- Direct CLI launchers log unexpected failures and return exit code `2` without leaking raw secrets.
- Debian package launchers now use `/usr/bin/python3`, avoiding `python3-gi` mismatches from pyenv/Conda/user Python.
- The installer no longer writes a systemd unit that calls the unsupported `--clipboard-monitor` option.

## Validation run

```text
PYTHONPATH=. pytest -q \
  tests/test_desktop_gtk_phase0_1_startup_logging.py \
  tests/test_desktop_gtk_phase1_2.py \
  tests/test_desktop_gtk_phase3.py \
  tests/test_desktop_gtk_phase4.py \
  tests/test_desktop_gtk_phase5.py \
  tests/test_desktop_gtk_phase6.py \
  tests/test_desktop_gtk_qt_parity.py \
  tests/test_desktop_entry_identity.py \
  tests/test_desktop_hud_activation.py
```

Result:

```text
52 passed in 2.34s
```

Additional validation:

```text
PYTHONPATH=. python -m compileall -q devtool tests/test_desktop_gtk_phase0_1_startup_logging.py
bash -n desktop/gtk-control-center/package-deb.sh install.sh
```

Both completed successfully.
