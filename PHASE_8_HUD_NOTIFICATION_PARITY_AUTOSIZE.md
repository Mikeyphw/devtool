# Phase 8: HUD notification parity and desktop HUD auto-size

This overlay fixes two blocking UX defects without reducing HUD detail.

## Android notification status-line parity

The Android notification now exposes the same status lead/detail semantics that
terminal output and the desktop HUD expose below their progress bars. Compact,
expanded, and large-text RemoteViews all include `notification_status_lead` and
`notification_status_detail` immediately after the progress bar.

## Desktop HUD content-aware resizing

The desktop HUD now computes desired width and height from the visible detailed
content, debounces content-size changes, grows when lanes/parser details expand,
shrinks when they collapse, and clamps itself inside the current screen bounds.
Manual resizing is preserved; double-clicking a resize edge/grip returns the HUD
to auto-size mode.

## Release metadata fix

The current project version is `1.1.0`, so the first canonical changelog release
heading is now `1.1.0` instead of `1.1.0-rc.6`. This prevents the release/source
audit from treating the changelog as stale against the project version.

## Validation

Focused validation run:

```bash
python3 -m pytest tests/test_android_notification_mock.py \
  tests/test_desktop_hud_autosize.py \
  tests/test_qt_control_center.py -q
python3 -m devtool.python_support source-audit .
```

Result: 33 passed for the focused UI/HUD regression slice and 0 source-audit
errors.

Full-suite note: the uploaded base archive has unrelated baseline failures
outside this overlay slice, so this overlay validates against the affected
UI/HUD notification slice instead of claiming a full-suite pass.
