# SFDT-05 — Native GitHub-hosted provider admission and node-attempt evidence

This change **extends** the existing `actions_android_provider` and `actions_graph_export` native EXO/DAG machinery. It does not install a second task scheduler, change release/signing authority, or run Gradle on the developer's device.

## New source-owned Android host profile

Existing Android CI jobs continue to work unchanged. Jobs that explicitly use the **StudyFlow Ubuntu 24.04** profile opt into stricter checks in `targets.<name>.jobs.<job>.metadata`:

```toml
ci_toolchain = "android-gradle"
ci_android_host_profile = "studyflow-ubuntu24"
ci_android_java_version = "21"
ci_android_sdk_packages = ["platforms;android-36", "build-tools;36.0.0", "ndk;29.0.14206865"]
ci_android_ndk_version = "29.0.14206865"
# Existing exact Gradle-wrapper JAR SHA-256 is still mandatory.
ci_android_wrapper_jar_sha256 = "<sha256 from tracked gradle-wrapper.jar>"
```

The source-reviewed wrapper must use Gradle **9.7.1**. The source provider also validates debug-only tasks, module ownership, wrapper distribution SHA-256, original wrapper JAR bytes, and the no-signer/no-device contract. The generated workflow provisions pinned Java/Android/Gradle setup actions and **verifies directories for each declared Android SDK/NDK package before executing any node**. No local Android SDK is required for static planning. A real GitHub Ubuntu job must still qualify setup and execution.

## Native evidence and failure semantics

The GitHub native step has an explicit step ID. Its always-running observation step writes `attempt-evidence.json` using the runner's Python—not a Devtool virtual environment. It contains SHA-bound source/graph/run/attempt/step identity, a local status, a digest, and explicit `authenticated_remote=false`, `release_qualified=false` fields. A missing `node-evidence.json` on an otherwise successful native job **fails the job after writing the negative attempt receipt**. Missing or failing setup records `UNQUALIFIED` when the runner permits final steps. A cancelled runner cannot be guaranteed to run any post-step; GitHub's independent run conclusion remains authoritative. The original typed `node-evidence.json` remains the native EXO output on success, not a claim of external trust.

Overlapping byte ownership (`dist/a` and `dist/a/b`) across nodes in the same host cell is now refused, in addition to the existing exact-path collision guard. Unsigned, non-release CI remains separate from protected signing. All Gradle work is hosted-only.

## Acceptance distinction

Source-only tests establish static admission and receipt-generation behavior. **SFDT-05 full exit-gate acceptance still requires a real GitHub-hosted native EXO run**, its negative/failure cases, and immutable source/workflow/graph correlation. SFDT-06 will add shadow native StudyFlow jobs and compare full fast/deep coverage before any cutover. Do not retire the existing StudyFlow GitHub job bridge on this source-only result.
