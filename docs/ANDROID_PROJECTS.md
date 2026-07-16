# Android and Gradle project support

Devtool 0.15 uses the shared `build-apk` backend for Android and Gradle projects,
then layers structured test, progress, and lint reporting on top.

## Detection

A project is detected as Android when a Gradle root contains an Android
application/library plugin or `src/main/AndroidManifest.xml`. Plain Gradle/JVM
projects use the `gradle` runner. In a mixed repository, define and select a
named target:

```toml
[targets.mobile]
type = "android"
root = "apps/mobile"

[targets.mobile.android]
variant = "debug"
aapt2_provider = "auto"
instrumentation = "when-device-present" # never | when-device-present | required
install_command = "sudo pm install -r -t"

[targets.mobile.gradle]
max_workers = 4
structured_events = true

[targets.mobile.tasks]
restore = ["help"]
build = ["assembleDebug"]
test = ["testDebugUnitTest"]
lint = ["lintDebug"]
clean = ["clean"]

[targets.mobile.artifacts]
output_directory = "artifacts/android"
required = true
```

```bash
devtool -r "$HOME/Code/project" --target mobile inspect
devtool -r "$HOME/Code/project" --target mobile doctor
devtool -r "$HOME/Code/project" --target mobile restore
devtool -r "$HOME/Code/project" --target mobile build
devtool -r "$HOME/Code/project" --target mobile test
devtool -r "$HOME/Code/project" --target mobile lint
devtool -r "$HOME/Code/project" --target mobile clean
devtool -r "$HOME/Code/project" --target mobile validate
```

## Validation pipeline

Android validation runs:

```text
restore/configure
build/package
local JVM/unit tests
Android Lint
```

For a `debug` Android target, inferred defaults are:

```text
help
assembleDebug
testDebugUnitTest
lintDebug
```

The selected variant controls the inferred task names. Explicit `[tasks]`
entries always win.

Pure JVM Gradle projects default to `build` and `test`. There is no universal JVM
lint task, so `[tasks].lint` must name the project's real check, such as
`checkstyleMain`, `detekt`, or `ktlintCheck`.

## Shared `build-apk` backend

`build-apk` remains available as a compatibility command:

```bash
build-apk . --task assembleDebug --output-dir "$HOME/Downloads"
```

The implementation is `devtool.android_build`. Devtool stages a self-contained
copy under the devtool XDG data directory:

```text
${DEVTOOL_XDG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/devtool}/android/build-apk
```

The backend provides:

- Gradle wrapper, compatible PATH Gradle, or managed Gradle selection.
- JDK discovery and minimum-version inference from AGP and toolchain settings.
- Android SDK/platform/Build Tools preparation.
- NDK, CMake, and Ninja preparation for native projects.
- Native ARM AAPT2 staging.
- APK, AAB, AAR, JAR, WAR, ZIP, and TAR output discovery.
- Stable artifact naming and optional output copying.
- Gradle daemon/process cleanup in a `finally` path.

## Exact Gradle progress

Devtool stages a bundled init script under the same XDG data root:

```text
${DEVTOOL_XDG_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/devtool}/gradle/devtool-events.init.gradle
```

The script emits ordered `DEVTOOL_EVENT` JSON lines for:

```text
task graph ready
task started
task completed/skipped/up-to-date
test started
test completed
test suite completed
build completed
```

Terminal progress and Android HUD progress consume the same events. A local task
counter such as `8/31 tasks` is kept separate from overall validation progress.
Sequence numbers prevent older events from replacing newer state.

Set this only when a project cannot load init scripts:

```toml
[targets.mobile.gradle]
structured_events = false
```

## Authoritative test reports

After a Gradle test command finishes, devtool reads:

```text
**/build/test-results/**/TEST-*.xml
```

It records:

```text
discovered
selected
executed
passed
failed
skipped
duration
report files
failed test names and source locations
```

A failed test in JUnit XML makes the devtool phase fail even if the child process
unexpectedly exits zero.

## Android Lint diagnostics

Devtool reads Android Lint XML or SARIF from standard Gradle report locations:

```text
**/build/reports/lint-results*.xml
**/build/reports/lint-results*.sarif
**/build/reports/lint-results*.json
```

Findings are normalized to devtool diagnostics containing severity, rule ID,
path, line, and message. Duplicate XML/SARIF findings are collapsed.

Console diagnostics are also recognized for Kotlin, javac/GCC-style messages,
AAPT2, and manifest merger failures.

## Termux and chroot behavior

Native Termux is supported explicitly:

```bash
devtool --execution-environment native-termux -r "$PWD" validate
```

Auto mode remains conservative. When a usable `chrootctl` backend is present,
Android/Gradle toolchain phases can use `androidos`. A target can prefer Termux:

```toml
[targets.mobile.android]
prefer_native_termux = true
```

The staged builder and Gradle init script live under the shared home path so the
same path map can expose them inside the chroot while logs and Android
notifications remain managed by the Termux-side devtool process.

## Artifact verification

New build outputs are verified before validation succeeds. APK checks include
ZIP integrity, manifest presence, package/version/SDK/launcher metadata through
AAPT2 when available, signing through `apksigner`, ABI metadata, SHA-256, and
unsigned-release detection. AAB and AAR files are checked for required bundle
and library members. JVM JAR/ZIP outputs receive archive integrity checks.

## Instrumentation on the physical device

```bash
devtool test --instrumented
devtool test --instrumented --class com.example.EditorTest
devtool test --instrumented --method com.example.EditorTest.opensFile
```

The rooted-device path uses `sudo pm install -r -t` and `am instrument`. An
explicit device serial uses ADB. Configure the runner or target package when
more than one installed instrumentation runner is present.

## Current capabilities

```text
restore
build
test
selective_tests
lint
validate
clean
inspect
doctor
collect_artifacts
verify_artifacts
instrumentation
```

See [`TEST_SELECTION.md`](TEST_SELECTION.md) for profiles and framework-native
class, method, tag, path, failed-test, and changed-file selection.
