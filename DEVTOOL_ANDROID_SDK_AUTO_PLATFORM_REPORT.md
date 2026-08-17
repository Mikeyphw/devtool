# Devtool Android SDK Auto Platform Resolution Fix

## Problem

A BKX Android overlay declared `compileSdk = 36`, but Devtool generated an Android validation command with `--android-platform auto`.  Inside the chroot, the staged `build-apk` helper resolved `auto` to `platforms;android-37` and failed before Gradle compilation because that SDK package was unavailable.

The bug is that host Devtool treated `auto` as a value to pass through to the long-lived staged helper.  That lets stale or environment-dependent helper logic choose a platform different from the Gradle project declaration.

## Fix

`devtool.android_integration.android_builder_arguments_for_tasks()` now resolves `android.platform = "auto"` before invoking the staged builder:

1. Inspect the Android Gradle project from the host-side Devtool package.
2. Read the declared `compileSdk` from Kotlin/Groovy build files.
3. Resolve common `libs.versions.*` version-catalog references to integer SDK levels.
4. Pass a concrete `--android-platform <compileSdk>` to `build-apk`.
5. Preserve explicit platform overrides like `platform = "35"` or `platform = "none"`.
6. Fall back to `auto` only when no static compileSdk declaration can be found.

This makes project metadata authoritative and prevents a stale chroot-staged helper from guessing a newer SDK platform.

## Covered cases

- Direct Kotlin DSL: `compileSdk = 36`.
- Direct Groovy/Kotlin legacy syntax already covered by existing static inspection patterns.
- Version catalog reference: `compileSdk = libs.versions.android.compile.sdk.get().toInt()`.
- Version catalog simple value: `android-compile-sdk = "36"`.
- Version catalog rich value: `android-compile-sdk = { strictly = "36" }`.
- Explicit platform override remains authoritative.

## Validation

Focused tests:

```text
163 passed
```

Targeted BKX artifact check:

```text
--android-platform 36
```

for `bkx_android_aov01_aov02_foundation_termux_bridge_v4.zip` project contents.
