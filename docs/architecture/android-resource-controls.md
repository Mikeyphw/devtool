# Android / Termux Resource Controls

Devtool applies mobile-safe Android validation defaults on native Termux and chroot/PRoot execution unless explicitly bypassed.

## Memory controls

- `--memory-guard-mb 0` disables the free-system-memory watchdog.
- `--gradle-metaspace-mb MIB` sets Devtool's explicit Gradle `MaxMetaspaceSize`.
- `--gradle-metaspace-mb 0` disables Devtool's explicit metaspace cap.
- `--metaspace-guard-mb MIB` is an alias of `--gradle-metaspace-mb`.
- `--no-metaspace-guard` is the convenience form for `--gradle-metaspace-mb 0`.

Disabling Devtool's metaspace cap does not prevent Gradle, project/user `gradle.properties`, `GRADLE_OPTS`, or other JVM configuration from imposing a limit. The Android builder process summary reports whether Devtool supplied `org.gradle.jvmargs`.

## Profile bypasses

`--resource-profile standard` disables the Android-safe validation profile for the validation invocation.

The Termux-wide automatic resource defaults are separate. Bypass them with the global option before the command:

```sh
devtool --no-termux-resource-profile validate
```

Equivalent explicit form:

```sh
devtool --termux-resource-profile off validate
```

To bypass both automatic layers and both memory guards for one Android validation:

```sh
devtool --no-termux-resource-profile validate --resource-profile standard --memory-guard-mb 0 --no-metaspace-guard
```

This intentionally permits Gradle to choose its own concurrency/JVM policy unless other project/user settings override it.

## Diagnostics

A real `OutOfMemoryError: Metaspace` is classified as fatal `GRADLE_METASPACE_OOM` / `metaspace_oom`, ahead of generic Gradle task failures. Devtool reports Gradle's effective heap/metaspace values when Gradle prints them and states whether Devtool passed `org.gradle.jvmargs`. Low-storage and ADB-probe warnings are structured so HUD, JSON, summaries, and clipboard accounting share the same diagnostic source.
