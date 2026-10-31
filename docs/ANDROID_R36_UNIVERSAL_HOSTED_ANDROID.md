# ANDROID-R36 — Bounded universal hosted Android DAG admission (non-release)

Baseline: `21d58feb2fbec43be01ea41a827efab66f09ce04` / Git tree `18f863e2c4708acd511ec11f88cc20d0ec76dcd5`. One **Devtool-only** transactional overlay. Does not alter xbsctl, dustui, MMRL, production signing or device installation. No Gradle commands are run by the installer or any source-only tests on Termux.

## Implemented

- Extend the existing `AndroidGradleProvider` (not a new provider) to review tracked, executable **nested Gradle wrapper roots** via explicit `ci_android_project_dir` and exact target/job working-directory ownership. Symlinked roots, untracked Gradle wrapper/manifest/module files, unpinned wrapper JAR or Gradle distribution, path traversal and custom distribution origins remain refused.
- Admit explicit debug variants such as `officialDebug` with module-scoped `assembleOfficialDebug`, `bundleOfficialDebug`, `lintOfficialDebug`, `testOfficialDebugUnitTest`. Variant grammar is constrained to `debug` or a reviewed variant ending in `Debug`. Only Debug build-type outputs are accepted; release/signing/instrumentation tasks remain rejected.
- Admit one to three direct tasks within one native DAG job, with one module and **at most one APK/AAB build producer**. A build requires exactly one declared owned output; lint/unit-only jobs cannot claim unverified outputs. Additional `ci_android_expected_output` pins can enforce the exact output, and `ci_android_output_subdir` is a bounded explicit layout override for multi-dimension flavor layouts. None of these configure a Gradle build to write a particular filename: actual output must be proven by hosted execution.
- Recognize semantic `runner="android"` / `runner="gradle"` with `gradle_tasks=[...]` in `provider_plan`, using the **existing** native target-owned graph. Host admission recognizes these only when `ci_toolchain="android-gradle"` and bounded task metadata exist. The frozen review binds tasks, project directory, wrapper, toolchain and outputs.
- RM-06 `run-node` lowers reviewed semantic tasks on an actual GitHub Linux/X64 runner to a **direct wrapper CommandSpec**, not the Termux/local `build-apk` helper. The helper rechecks native job/tasks and the trusted source before generating argv; existing run-node verifies Git SHA, workflow graph hash, run/attempt and output scope before invoking it. Direct command jobs still use the existing native job resolver and now check cwd against the reviewed project root as well as argv.
- Preserve existing GitHub pinned Java/Android SDK/Gradle setup, DAG `needs`, source/run/attempt handoff, bounded staging, and R35 imported artifact lineage. No duplicate scheduler, new registry, new Github dispatcher or signer is introduced.

## Example — nested Android root + named debug flavor

```toml
[targets.mobile]
runner = "android"
root = "android"

[targets.mobile.jobs.debug]
runner = "android"
gradle_tasks = [":app:lintOfficialDebug", ":app:assembleOfficialDebug"]
outputs = ["android/app/build/outputs/apk/official/debug/app-official-debug.apk"]
metadata = { ci_toolchain = "android-gradle", ci_android_project_dir = "android", ci_android_debug_variant = "officialDebug", ci_android_java_version = "17", ci_android_sdk_packages = ["platforms;android-35", "build-tools;35.0.0", "platform-tools"], ci_android_wrapper_jar_sha256 = "<actual SHA-256 of the tracked android/gradle/wrapper/gradle-wrapper.jar>", ci_android_expected_output = "android/app/build/outputs/apk/official/debug/app-official-debug.apk" }

[targets.mobile.workflows]
ci = ["job:debug"]
```

The wrapper also requires tracked `android/gradlew`, `android/gradle/wrapper/gradle-wrapper.properties` containing an official Gradle distribution and `distributionSha256Sum`, `android/settings.gradle(.kts)`, and `android/app/build.gradle(.kts)`. The exact variant, AGP version, signed package and outputs must be verified against each real consumer before adoption. Repository-independent consumer reviews remain mandatory.

An equivalent reviewed direct `runner="command"` job uses `command=["./gradlew", "--no-daemon", ":app:lintOfficialDebug", ":app:assembleOfficialDebug"]` with the same target root and metadata. **Never run this command in Termux**; the generated workflow is the authorized Gradle execution surface. `provider_plan` and native DAG export only inspect and generate; they do not build locally.

## Test/qualification evidence and limits

- V0: source SHA/tree and clean worktree; transaction preimages; strict overlay manifest; no overwrite.
- V1: targeted `validation.tests` (using installed Devtool's existing artifact-test validation runner) with `uv run --with-requirements devtool-test-requirements.lock python -m pytest ...`. No new CLI behavior is required to install the overlay. R32/R34/R35 provider/registry and R03B destructive audit included.
- V2: negative tests for release, signing/device/root, injection, symlinked wrapper, wrong nested cwd, unsafe variant, more than one build producer, and mismatched owned outputs. Semantic runner admission and direct-hosted-only bridge have independent tests.
- V3: `devtool-android-r36-source-qualification.yml` source-only GitHub run and full 24-shard exact-commit regression after applying/pushing; R34 hosted native-DAG rerun for fallback compatibility.
- V4: **OPEN until independently demonstrated**: hosted execution of this R36 nested/flavored/semantic contract in real consumer repositories (xbsctl, dustui, MMRL), runtime Gradle/AGP compatibility, shared build outputs and package/signature verification. Source-only tests do not qualify these consumers.
- V5: separately governed release-candidate compilation, production signing, device installation and FC09 formal release remain **NOT QUALIFIED**. This overlay does **not** expand production signing or install authority.

The known limitation is that arbitrary opaque custom shell jobs may execute Gradle outside reviewed Devtool job boundaries. R36's generated Actions plan refuses such jobs; it does not claim to sandbox all operating-system subprocesses in every unrelated execution API. Strict hosted-only policies should keep unknown shell commands disabled, with broader runtime admission controls investigated in the R37/R38 policy phase.

## Follow-up for real consumers

Review each real consumer's tracked wrapper/config/AGP, run static `provider_plan`, inspect generated workflow in a clean review branch, dispatch only the GitHub Actions build on that consumer's own exact commit, independently inspect APK/AAB and signer, then import through R35 and preserve consumer-specific gates. Do not replace actual consumer workflows until source and hosted parity are documented. Do not infer qualification from this source-only overlay.
