# ANDROID-X01 — Canonical Android Environment and Release Truth

## Position

- Full implementation plan: **37/70**
- Android environment/device window: **1/3** before ANDROID-G1
- Previous authoritative gate: **GRADLE-TAKEOVER-G1 36/70**, closed on device with 148 passed, 1 skipped, 0 diagnostics.
- Next overlay: **ANDROID-X02 38/70**.
- Next gate: **ANDROID-G1 39/70**.

## Merge-window decision

The next-three window is ANDROID-X01 (37/70), ANDROID-X02 (38/70), ANDROID-G1 (39/70). They remain separate: X01 owns canonical environment/release/variant truth; X02 owns exact artifact/device qualification and AndroidExecutionSnapshot convergence; G1 is the authoritative environment/device milestone gate.

## Delivered boundary

ANDROID-X01 establishes a canonical, inspectable Android environment authority with explicit EnvironmentView propagation, package-aware SDK/Build Tools qualification, first-class AAPT2/AIDL/zipalign/split-select resolution, exact NDK/CMake qualification, semantic component identities separated from path provenance, independent release/instrumentation variant lanes, fail-closed variant/task collision checks, unconditional release artifact materialization and SHA-256 verification, private RUN-backed release evidence, collision rejection, and prerequisite reduction that prevents device-side effects after required readiness failure.

Build-only release environment resolution does not probe ADB. Release signing uses a narrow signing-tool context rather than the legacy all-capabilities Android execution context. Gradle context/plan identity is linked from canonical Gradle evidence; X01 does not create another Gradle planner.

## Validation

Implementation-owned Devtool workflow: `./devtoolw android-x01`.

- 3 stages passed, 0 failed
- 51 tests passed, 0 failed, 0 skipped
- 0 warnings, 0 errors
- construction log: `20260930-040444-476083.log`

Retained Android capability/toolchain regression boundary:

- 76 passed, 0 failed

Additional repository checks: `python -m compileall -q ...` and `git diff --check` pass. Broad Gradle/device/remote qualification is intentionally deferred to ANDROID-G1.

## Exported obligations

ANDROID-X02 must consume this authority rather than fork it. It owns AndroidExecutionSnapshot, exact ArtifactPair lineage from Gradle/RUN truth, DeviceLease/action-aware transport, destination-local/remote qualification, componentized RER convergence, and the staged runtime mechanical seal. ANDROID-G1 owns the broad Android/Gradle/device proof matrix. See `ANDROID_X01_EXPORTED_OBLIGATIONS_TO_ANDROID_X02_G1.json`.
