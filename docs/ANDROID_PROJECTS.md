# Android and Gradle project support

Devtool 0.15 uses the shared `build-apk` backend for Android and Gradle projects,
then layers structured test, progress, and lint reporting on top. AOPS02 adds the
canonical Android execution/device fabric described in [`ANDROID_OPERATIONS.md`](ANDROID_OPERATIONS.md).

## Detection

A project is detected as Android when a Gradle root contains an Android
application/library plugin or `src/main/AndroidManifest.xml`. Plain Gradle/JVM
projects use the `gradle` runner. In a mixed repository, define and select a
named target:

```toml
[targets.mobile]
runner = "android"
platform = "android"
root = "apps/mobile"

[targets.mobile.android]
variant = "debug"
aapt2_provider = "auto"
instrumentation = "when-device-present" # never | when-device-present | required
install_command = "sudo pm install -r -t"

[targets.mobile.android.adb]
provider = "auto" # auto | sdk | termux | termux-adb | path | none
device_policy = "exactly-one"

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
          -> lint -> instrumentation -> finalize
```

Each invocation may stop at a phase boundary and continue later. Overlay/archive integrity is checked during inspect/apply, not again during repository validation. State is stored
under `.devtool/state/android-validation/<session-id>/state.json`; the active
session marker is `.devtool/state/android-validation/active.json`. Gradle's
project cache, `GRADLE_USER_HOME`, dependency cache, and build outputs are kept.
Devtool never inserts `clean` between phases. Android validation also forces Gradle's
configuration cache off, independently of structured progress events. The managed Android
SDK may be provisioned or repaired immediately before a phase; treating that mutable SDK
directory as a configuration-cache fingerprint can cause repeated cache misses unrelated to
repository inputs. Gradle's dependency/build caches and Devtool's durable validation phase
cache remain available.

Common forms:

```bash
devtool -r "$HOME/Code/app" validate start --next
devtool -r "$HOME/Code/app" validate next
devtool -r "$HOME/Code/app" validate resume --through package
devtool -r "$HOME/Code/app" validate resume --complete
devtool -r "$HOME/Code/app" validate status
devtool -r "$HOME/Code/app" validate recover
devtool -r "$HOME/Code/app" validate abort
devtool -r "$HOME/Code/app" validate abort --keep-changes
```

The equivalent top-level commands are `start-validation`, `resume-validation`,
`validation-status`, `validation-finalize`, `validation-recover`,
`validation-reset`, `validation-accept-plan-change`, and `validation-abort`. A
failed phase blocks the session while preserving the
applied tree and rollback point. It is not repeated while its inputs are
unchanged; use `--retry-failed` after fixing the issue or when an explicit retry
is appropriate. `validate status` and `validate checkpoint` never reclaim or
rewrite a live attempt.


### Android validation control API

The Android app must consume the stable JSON control surface rather than parse
terminal prose. Session schema v4 exposes an API-v1 envelope and an append-only
JSONL event stream:

```bash
devtool -r "$REPO" validate status --events --json
devtool -r "$REPO" validate plan --json
devtool -r "$REPO" validate events --after-event "$CURSOR" --limit 100 --json

devtool -r "$REPO" validate run --scope next --if-revision "$REV" --json
devtool -r "$REPO" validate run --scope through --through package --if-revision "$REV" --json
devtool -r "$REPO" validate run --scope selected --phase compile --phase lint --if-revision "$REV" --json
devtool -r "$REPO" validate run --scope remaining --if-revision "$REV" --json

devtool -r "$REPO" validate pause --after-phase --if-revision "$REV" --json
devtool -r "$REPO" validate pause --cancel --if-revision "$REV" --json
devtool -r "$REPO" validate stop --if-revision "$REV" --json
devtool -r "$REPO" validate retry --if-revision "$REV" --json
devtool -r "$REPO" validate reset --from compile --if-revision "$REV" --json
devtool -r "$REPO" validate finalize --if-revision "$REV" --json
devtool -r "$REPO" validate abort --keep-changes --if-revision "$REV" --json
devtool -r "$REPO" validate abort --rollback --if-revision "$REV" --json
devtool -r "$REPO" validate recover --if-revision "$REV" --json
```

`status`, `plan`, and `events` never acquire the repository run lock or mutate
the session. `pause` and `stop` write an atomic out-of-band control request, so
they still work while the validation worker owns that lock. An accepted control
advances the session revision. A worker with an older in-memory copy merges the
durable revision before its next heartbeat or phase-result save, preventing a
revision rollback.

The app should send the revision it most recently rendered. Exit code `3` means
the action came from stale UI and the app should refresh. Exit `4` means a
failed phase blocks the session, `5` means recovery is required, and `6` means
the requested control is not available in the current state. A pending pause or
stop appears as `effective_state: pausing` or `stopping` without falsifying the
durable phase verdict.

Events include session creation, phase start/finish, pause/stop requests, blocked
and validated transitions, reset/plan acceptance, rollback transitions, and
completion. Poll with the returned `next_event_id`; cursor reads return events
after that ID in chronological order.

Failure policy for durable validation is explicit:

```bash
devtool validate continue --failure-action pause      # default, preserve changes and rollback
devtool validate continue --failure-action rollback   # restore immediately after a code/test failure
devtool validate continue --failure-action keep       # close transaction and retain unvalidated changes
```

`--dry-run` previews the selected phases without creating, advancing, or
finalizing a session. Running phases record PID/process-start ownership. Recovery
only reclaims owners that are provably dead, so Android status polling cannot
accidentally reset a live Gradle phase.

Phase selection uses a dependency graph. `--phase package` includes every
unsatisfied prerequisite. `--phase-only package` is accepted only when all of
its prerequisites have already passed. A removed required phase, reordered
graph, or changed phase identity pauses in `recovery-required`; accept a
reviewed change with `validate accept-plan-change --reason "..."` or restart the
session. `validation.minimum_phase` is enforced before finalization.

Input fingerprints are tracked by configuration, source, resources, and tests.
A phase that exits 0 after one of its relevant domains changed is marked stale
and must run again. `validate reset --from-phase compile` resets Compile and its
dependents while retaining attempt evidence; `validate reset --all` clears all
verdicts.

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
--max-infrastructure-retries 1
--max-resource-retries 1
--no-auto-retry
```

Runtime, battery, charging, and thermal guards are checked both between phases
and while a child process is active. A supervised stop is recorded as a
retryable timeout/resource/environment attempt, not a code failure. Corroborated
resource failures may retry with the `android-low-power` profile. Exit 137 is
not called an out-of-memory failure without supporting guard or output evidence.
`--stop-gradle-after-phase` reclaims
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
memory_guard_mb = 0
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

- Complete Gradle Wrapper preference, explicit Termux/system provider selection, compatible PATH Gradle, or managed Gradle fallback.
- JDK discovery with Termux/system provider identity and minimum-version inference from AGP and toolchain settings.
- Android SDK/platform/Build Tools preparation.
- NDK, CMake, and Ninja preparation for native projects.
- Native ARM AAPT2 staging.
- APK, AAB, AAR, JAR, WAR, ZIP, and TAR output discovery.
- Stable artifact naming and optional output copying.
- Gradle daemon/process cleanup in a `finally` path.

### Termux Java/Gradle provider policy

On native Termux, devtool recognizes JDKs under `$PREFIX/lib/jvm` and Gradle
resolved from `$PREFIX` as Termux providers. Provider selection is explicit and
capability-preserving:

```toml
[android.java]
provider = "auto" # auto | termux | system

[gradle]
provider = "auto" # auto | wrapper | termux | system | download
```

A complete project Wrapper (`gradlew`, `gradle-wrapper.properties`, and
`gradle-wrapper.jar`) wins in Gradle `auto` mode. If only part of the Wrapper is
present, devtool stops with an actionable diagnostic rather than substituting a
Termux/system Gradle that could violate the project's Gradle/AGP version
contract. An explicit `--gradle-command` or `--java-home` remains authoritative.
For a wrapperless non-Android Gradle project, the packaged Termux Gradle is
eligible in `auto` mode without pretending devtool's managed fallback version is
a project pin. For Android projects without a wrapper, auto-selection only uses
an unpinned Termux Gradle when its major generation matches the detected AGP
major (AGP 7+); otherwise devtool keeps the managed fallback path. Native
Termux runtime detection also records the Android API level (environment first,
then `getprop` fallback), and provider diagnostics distinguish missing tools from
detected executables whose version probe is unusable. Use `--gradle-provider` or
`--java-provider` when deliberately selecting a provider.

### Termux native/sysroot and Android build-tools policy

Termux `clang` depends on the NDK-derived `ndk-sysroot`, but devtool treats that
combination as a native compiler capability rather than a complete Android NDK.
Plain compiler/CMake work can use the native Termux provider; Android Gradle
projects using external native integration continue through the complete NDK
provider and existing NDK host-compatibility logic. Android Gradle Plugin uses
CMake or ndk-build for `externalNativeBuild`, so those projects are never silently
redirected to the Termux sysroot-only provider.

```toml
[android.native]
provider = "auto" # auto | termux-sysroot | ndk | system
cache = "auto" # auto | ccache | off

[android.build_tools]
provider = "auto" # auto | termux | sdk | commit451 | none
aapt2_provider = "auto" # auto | termux | sdk | revanced | none

[cmake]
provider = "auto" # auto | termux | system
native_provider = "auto" # auto | termux-sysroot | system
cache = "auto" # auto | ccache | off
```

On native Termux, `auto` discovers CMake, Ninja, ccache, AAPT/AAPT2, apksigner,
adb, and the native SDK helper names under `$PREFIX`. AAPT2 is selected only
after a compile/link probe against the actual selected Android platform. ARM64
SDK helper repair also executes a harmless probe for `aidl`, `zipalign`, and
`split-select`, because matching AArch64 ELF architecture alone cannot distinguish
Bionic from glibc compatibility. Native Termux therefore requires runnable
Termux-native helpers. Commit451 publishes linux-glibc-arm64 build tools, so that
provider is rejected under native Termux/Bionic and retained only for compatible
ARM64 Linux/chroot hosts. Commit451 release availability is resolved before asset
downloads, while the `35.0.0 -> 35.0.1` compatibility alias remains supported.
The explicit `sdk` provider refuses an SDK whose native tools cannot execute on
the host instead of quietly substituting another provider.

A `CMakeLists.txt` that merely exists somewhere in an Android repository is not
by itself treated as Android NDK integration. Devtool requires an explicit
`externalNativeBuild`/`ndkVersion`/ndk-build signal (or Android.mk/Application.mk)
before forcing the complete NDK provider, so host-side CMake helpers do not trigger
unnecessary NDK provisioning.

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
more than one installed instrumentation runner is present. Devtool writes a
structured instrumentation report for setup failures as well as runner failures,
and it uninstalls the test APK package in a `finally` cleanup path unless
`keep_installed` / `--keep-installed` is set.

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

### Custom AAPT2 warning suppression

When Devtool selects a custom AAPT2 executable, it automatically passes:

```text
-Pandroid.aapt2FromMavenOverride=<selected-aapt2>
-Pandroid.suppressUnsupportedOptionWarnings=android.aapt2FromMavenOverride
```

Existing comma-separated suppressions supplied through Gradle passthrough
arguments are merged. No suppression property is added when Android Gradle
Plugin uses its default AAPT2.


## PRoot-Distro Android command execution

When a Linux guest contains the Android toolchain you want to use, keep devtool on Termux and run only planned command phases inside that guest:

```bash
devtool --proot-distro ubuntu --proot-cwd /workspace/project validate
```

The guest does not need devtool. The repository is bound explicitly, host Termux Java/Gradle/CMake/NDK paths are not injected into the guest, and Android outputs written in the bound checkout remain immediately visible to local devtool artifact verification. If provider planning produced an absolute command under the Termux package prefix, the PRoot wrapper resolves that tool again by command name inside the guest instead of staging the Bionic host executable or launcher. Devtool-managed PRoot/backend environment variables are reserved and cannot be replaced by project or command environment. Do not use PRoot-Distro detached mode for normal validation because local devtool must retain progress, exit-code, and phase-order control.

Before an Android build, inspect why native Termux providers were selected or rejected without running an Android Gradle task:

```bash
devtool --host termux doctor --explain
devtool --host termux packages
devtool --host termux doctor --distro ubuntu --explain
```

The package view is advisory only. It never changes Termux packages. A PRoot guest probe discovers the guest's own build tools rather than treating the host's Bionic Java/Gradle/CMake/NDK commands as guest tools.
