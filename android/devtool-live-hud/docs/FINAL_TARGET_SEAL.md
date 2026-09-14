# Android HUD final target seal

The Android HUD final target seal is the Overlay 15 target-scoped gate for `.devtool.toml` target `android_hud`. It is separate from the Python `devtool` target seal so final qualification can be applied and validated one target at a time.

Run:

```sh
devtool final-seal --target android_hud --strict
```

The target seal checks that the Android target is configured as `runner = "android"`, `platform = "android"`, and `root = "android/devtool-live-hud"`; that the Gradle project, manifest, release/test sources, root bridge integration, docs, and target report exist; and that the target validation policy declares the expected Android-safe split Gradle/resource limits.

The full release validation command for this target remains the normal Devtool apply/validation flow with `--target android_hud` and explicit Android Gradle tasks. Device tests stay conditional unless the user intentionally connects a device and enables instrumentation.
