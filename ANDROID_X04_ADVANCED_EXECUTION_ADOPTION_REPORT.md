# ANDROID-X04 — Advanced Android execution, UX and dogfooding adoption

**Campaign position:** full-plan overlay **51 of 70**.  
**Base:** qualified DIST-X04 (`5e2d89d`) with TEST-X03 runtime/shard authority and sealed ANDROID-G2 instrumentation/device authority.  
**State:** `PREPARED_LOCAL_QUALIFIED_PENDING_NATIVE_TERMUX`.  
**Next:** LIFE-X04 — 52/70. Android's final seal remains the separate ANDROID-G3 gate.

## Next-three merge decision

The inspected window is **ANDROID-X04 51/70 → LIFE-X04 52/70 → PRES-X08 53/70**. They remain separate. ANDROID-X04 owns Android-specific adoption of canonical test/device/instrumentation contracts; LIFE-X04 owns artifact/recovery/signing/cleanup convergence; PRES-X08 owns presentation adoption. No gate or neighboring ownership boundary is merged here.

## Delivered Android execution adoption

ANDROID-X04 adds an adoption/planning layer over existing authorities; it does **not** create another runner, retry engine, device fabric, run store, Gradle planner or privilege broker.

- Typed `AndroidJUnitShard` emits AndroidJUnitRunner's standard `numShards` / `shardIndex` capability instead of smuggling sharding through arbitrary runner arguments.
- `AndroidTestOrchestratorMode` is fail-closed: Orchestrator mode requires an explicit qualified capability receipt and target instrumentation identity.
- `ExoRetryPolicy` represents failed-only retry semantics while retaining **EXO** as the execution/retry owner. Android X04 never performs a private retry loop.
- `QuarantinePolicy` is explicit and evidence-bearing; a quarantined failed testcase remains failed in canonical truth.
- `DeviceCapacity` and `DeviceMatrixChild` bind matrix children to X02 `DeviceLease` identities. Per-device concurrency is expressed as deterministic resource slots and scheduled through TEST-X03's resource-safe scheduler.
- Emulator snapshot reuse is typed and is rejected for physical devices.
- `AndroidExecutionProfile` gives AndroidJUnitRunner and UI Automator/cross-app execution one evidence foundation rather than a parallel UI-test runner.
- `AdvancedInstrumentationPlan` creates one parent semantic plan with DeviceLease-bound child nodes, deterministic waves, profile identity, retry/quarantine policy and source migration diagnostics.
- `matrix_evidence()` fails closed when a child receipt is missing or unexpected; it never invents testcase results.

## UX and migration adoption

The canonical CLI now exposes the roadmap UX without weakening existing commands:

- `devtool android env explain`
- `devtool android env diff`
- `devtool android qualification show`
- action-aware `devtool android doctor --for <action>` while preserving legacy bare `android doctor` behavior
- `devtool android instrumentation --plan --shards ... --retry-failed-attempts ... --quarantine ...`
- qualified Orchestrator and UI-Automator profile switches on the non-mutating plan surface

`android instrumentation --plan` is intentionally non-mutating and reports device binding as `pending-DeviceLease`; live execution remains owned by the existing X03 instrumentation + X02/HOST DeviceLease + EXO boundaries. Advanced options on the direct executor fail closed rather than silently falling back to raw runner arguments.

Legacy flat instrumentation keys are reported as migration diagnostics toward `android.instrumentation.*`; configuration is not silently rewritten. The historical `tests/test_android_connected_task_skip.py.bak-20260821-162146` backup fixture is retired from the repository rather than remaining executable-looking test debris.

## Authority preservation

ANDROID-X04 composes:

- **ANDROID-X03:** `InstrumentationIntent`, base resolved instrumentation semantics and receipt truth;
- **ANDROID-X02 / HOST:** `DeviceLease` and device freshness;
- **TEST-X03:** deterministic shard/resource scheduling and performance-evidence boundary;
- **EXO:** attempts/retries/cancellation;
- **RUN V3:** durable evidence/attachments;
- **PRES (later):** presentation rendering of the canonical projection.

The X04 machine projection names those authorities explicitly so later TUI/Desktop work consumes canonical IDs instead of reconstructing Android state.

## Targeted validation

Construction-host validation uses the Devtool-owned X04 workflow with only the execution-environment requirement temporarily adapted for the construction host; packaged production configuration is restored to `native-termux`.

The targeted boundary includes:

1. source syntax;
2. X04 advanced execution/UX contracts;
3. retained X01/X02/X03/G1/G2 Android environment/device/instrumentation authority;
4. retained CLI/project-runner instrumentation parity;
5. TEST-X03 shard/runtime adoption and TEST-G2 selection regression.

Native-Termux artifact qualification is authoritative and remains required on apply.

## ANDROID-G3 obligations retained

ANDROID-X04 does not claim the final Android seal. ANDROID-G3 must still prove from fresh processes and real available destinations/devices:

- qualified Orchestrator execution;
- multi-device DeviceLease child execution;
- real AndroidJUnitRunner class/method/failure/skip/cleanup/cancellation fixtures;
- UI Automator/cross-app execution;
- rooted/unrooted and USB/TCP/loopback combinations where available;
- native Termux/chroot/SSH/OCI environment parity;
- release/validation/direct instrumentation convergence;
- final source-promise disposition for AND-P001..P084 and INST-P001..P084.
