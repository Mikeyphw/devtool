# Devtool Desktop HUD and Control Center Phase 2 + 3 Report

## Scope

Desktop GTK4 surfaces only:

- GTK4 Control Center startup/polling pipeline
- GTK4 compact HUD layout
- Desktop installer/completion synchronization needed by regression gates

Android code is unchanged.

## Phase 2 implemented

- Control Center now presents a lightweight shell before repository, Git, history, artifact, or IPC data is loaded.
- Snapshot acquisition now runs on a daemon worker thread instead of the GTK main thread.
- The GTK main thread only applies completed payloads through `GLib.idle_add`.
- Refreshes are guarded by `in_flight`, `queued`, and monotonic `token` state.
- Rapid manual/timer refreshes coalesce instead of stacking overlapping work.
- Page rendering is lazy: only the selected page is rendered after each payload update.
- Inactive pages are marked dirty and refreshed when selected.
- The app keeps the last good payload and shows a visible stale-data banner/header state if a background refresh fails.
- The close request marks background refreshes obsolete so late worker completions cannot mutate a closing window.

## Phase 3 implemented

- HUD layout now has three safe regions: compact header, scrollable content body, sticky action footer.
- Long operation titles are single-line ellipsized and exposed through tooltips.
- Oversized status badge was replaced by a compact semantic status chip.
- Failure-first summary card is shown above telemetry.
- Progress row now separates the bar from exact task/progress text.
- Metric cards are compact FlowBox chips without hard card size requests.
- The old fixed-size phase card strip was replaced with a vertical stepper.
- Skipped/pending work is displayed as `Not run`, not as completed progress.
- Raw event log is collapsed by default in a `Gtk.Expander`.
- Periodic HUD refresh now uses the shared safe GTK wrapper instead of calling `read()` directly from the timer.
- Removed HUD height traps: no card `set_size_request(...)` and no `set_min_content_height(...)`.

## Regression compatibility included

- Synchronized `completions/_devtool` with `devtool.completion.ZSH_COMPLETION`.
- Kept installer clipboard wording used by older UX tests while still refusing the unsupported `--clipboard-monitor` unit from Phase 1.

## Validation

```text
PYTHONPATH=. pytest -q \
  tests/test_desktop_gtk_phase0_1_startup_logging.py \
  tests/test_desktop_gtk_phase1_2.py \
  tests/test_desktop_gtk_phase2_3.py \
  tests/test_desktop_gtk_phase3.py \
  tests/test_desktop_gtk_phase4.py \
  tests/test_desktop_gtk_phase5.py \
  tests/test_desktop_gtk_phase6.py \
  tests/test_ui_ux_slice2.py \
  tests/test_ui_ux_slice5.py \
  tests/test_desktop_hud_activation.py \
  tests/test_desktop_entry_identity.py \
  tests/test_completion_and_manpage.py::CompletionAndManpageTests::test_packaged_and_runtime_zsh_completions_are_identical \
  tests/test_installer_ux.py::test_installer_offers_platform_app_module_and_clipboard_workflows
```

Result:

```text
71 passed in 7.15s
```

Additional gates:

```text
PYTHONPATH=. python -m compileall -q devtool tests/test_desktop_gtk_phase2_3.py
bash -n desktop/gtk-control-center/package-deb.sh install.sh
```

Result: passed.

A full unfiltered `pytest -q` run was attempted. It exceeded the sandbox command timeout after progressing past 75% of the suite. Before the timeout, two baseline regression drifts were surfaced and fixed in this patch: completion synchronization and installer clipboard prompt wording.
