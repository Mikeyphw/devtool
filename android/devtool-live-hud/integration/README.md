# Devtool integration

The Android app is bundled under `android/devtool-live-hud`; the Python bridge is `devtool/android_live_hud.py`.

## Live progress

```text
--hud-overlay
--hud-no-overlay
```

`DEVTOOL_HUD_OVERLAY=1` also requests the floating HUD. An explicit `--hud-no-overlay` wins.

The desktop and Android paths publish a waiting/current-run state before launching or reusing the HUD. This prevents a newly started command from briefly displaying the preceding run.

## Nested overlay bundles

A wrapper archive whose non-metadata payload consists entirely of supported overlay archives is treated as an implicit overlay bundle. Its children are applied in deterministic filename order.

This means:

```text
bundle.zip
  01_first_overlay.zip
  02_second_overlay.zip
```

is expanded and applied sequentially. The inner ZIP files are not copied into the target repository. A normal overlay that contains both source files and a reference ZIP is still treated as a normal overlay.
