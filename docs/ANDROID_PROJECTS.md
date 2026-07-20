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
parallel = true
build_cache = true
daemon_idle_timeout_ms = 900000
# stop_after_run = true # opt-in for low-memory devices only

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

Normal Android validation still uses the fastest safe coalesced Gradle plan. On
memory-constrained Android and Termux hosts, persistent sequential validation
splits that plan into durable checkpoints:

```text
preflight -> dependencies -> compile -> resources -> unit-test -> package
          -> artifact-check -> lint -> instrumentation -> finalize
```

Each invocation may stop at a phase boundary and continue later. State is stored
under `.devtool/state/android-validation/<session-id>/state.json`; the active
session marker is `.devtool/state/android-validation/active.json`. Gradle's
project cache, `GRADLE_USER_HOME`, dependency cache, and build outputs are kept.
Devtool never inserts `clean` between phases.

Common forms:

```bash
devtool -r "$HOME/Code/app" validate start --next
devtool -r "$HOME/Code/app" validate next
devtool -r "$HOME/Code/app" validate resume --through package
devtool -r "$HOME/Code/app" validate resume --complete
devtool -r "$HOME/Code/app" validate status
devtool -r "$HOME/Code/app" validate abort
```

The equivalent top-level commands are `start-validation`, `resume-validation`,
`validation-status`, `validation-finalize`, and `validation-abort`. A failed
phase is cached and is not repeated while its inputs are unchanged. Use
`--retry-failed` to retry it explicitly.

Useful boundaries and guards:

```bash
--phase compile
--from-phase resources --through package
--max-phases 2
--max-runtime 15m
--pause-after lint
--stop-below-battery 30
--require-charging
--stop-above-temperature 44
--require-complete
```

Runtime budgets are checked only between phases. Devtool lets the active Gradle
phase finish, checkpoints it, then pauses. `--stop-gradle-after-phase` reclaims
daemon memory after every invocation; `--keep-gradle-daemon` keeps the warm JVM.
The target may set `validation.stop_daemon_after_phase = true` as its default.

Changed-file planning keeps the smallest conservative pipeline:

- shared Gradle, wrapper, version-catalog, buildSrc, or build-logic changes run all phases;
- source changes start at compile;
- manifest, resource, and asset changes start at resource processing;
- test-only changes start at unit tests;
- documentation-only changes skip Gradle and run finalization only.

Explicit phase tasks may be configured under `[validation.phases]`, for example:

```toml
[validation.phases]
preflight = ["help"]
dependencies = ["dependencies"]
compile = [":app:compileDebugSources"]
resources = [":app:processDebugResources"]
unit-test = [":app:testDebugUnitTest"]
package = [":app:assembleDebug"]
lint = [":app:lintDebug"]
```

Per-phase resource profiles may override the Android-safe defaults:

```toml
[validation.phase_profiles.compile]
max_workers = 1
cpu_limit = 2
gradle_heap_mb = 1536
memory_guard_mb = 640
low_priority = true
```

The HUD, Android notification, terminal summary, and machine JSON expose the
overall phase index independently from Gradle task progress, such as `phase 3/9`
and `28/117 Gradle tasks`. Pure JVM Gradle projects use the same session engine,
but omit Android-only resource and instrumentation phases where unavailable.

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
