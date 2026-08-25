# Android pipeline architecture

Devtool now exposes Android behavior through the `devtool.android` package.

## Stable domain surfaces

- `devtool.android.build` — orchestration and artifact discovery
- `devtool.android.gradle` — Gradle selection and arguments
- `devtool.android.sdk` — SDK discovery/provisioning
- `devtool.android.ndk` — NDK host compatibility
- `devtool.android.cmake` — CMake selection
- `devtool.android.aapt2` — AAPT2/build-tools providers
- `devtool.android.tasks` — task classification/device-aware filtering
- `devtool.android.instrumentation` — instrumentation runner
- `devtool.android.diagnostics` — serializable project snapshots
- `devtool.android.providers.*` — Termux, Linux, and QEMU provider APIs

## Standalone builder compatibility

`devtool.android_build` intentionally remains the self-contained implementation
staged as `build-apk` into chroot-visible runtime storage. Moving that executable
behind package-relative imports would break direct execution in chroots. New
in-process consumers should import the domain modules above; the staged builder
remains a compatibility boundary until its runtime bundling contract is changed.

This split therefore removes direct monolith coupling from package consumers
without regressing the standalone/chroot execution contract.
