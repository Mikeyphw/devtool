# Devtool integration

This app is bundled inside the final devtool repository and the native bridge adapter is already integrated as `devtool/android_live_hud.py`.

The devtool CLI supports:

```text
--hud-overlay
--hud-no-overlay
```

The Android bridge remains notification-first. Overlay presentation additionally requires the app's display-over-other-apps permission and enabled overlay service.
