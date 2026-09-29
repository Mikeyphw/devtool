# ENV-X03 — Canonical Gradle planner, modern reuse layers and execution receipt

## Campaign position

- Gate window: **1/2** (`ENV-X03`, `ENV-G2`)
- ENV scope: **4/7**
- Full implementation plan: **17/63**
- Next overlay: **ENV-G2** (separate authoritative real-Gradle gate)
- Merge-window decision: keep `ENV-X03`, `ENV-G2`, and `EXEC-X03` separate. ENV-G2 is a gate; EXEC-X03 is a different execution/control boundary.

## Delivered boundary

ENV-X03 replaces runner-specific Gradle policy branches with a typed semantic planning boundary while leaving Gradle's task graph/build cache authoritative and leaving execution/reuse/history authority in EXEC/RER/RUN.

### Canonical semantic objects

`devtool/gradle_planning.py` introduces and/or makes authoritative:

- `GradleInvocationRequest`
- `GradleLauncherIdentity`
- `GradleExecutionContext`
- `GradleCapabilitySet`
- `GradleExecutionPolicy`
- `GradleHeapPolicy`
- `ConfigurationCacheQualification`
- `GradleArgumentClasses`
- `AggregateQualification`
- `GradleInvocationBarrier`
- `GradlePhysicalInvocation`
- `GradleInvocationPlan`
- `GradleExecutionReceipt`

### One planning path

JVM CLI actions, Android build/validation actions, Android instrumentation prebuilds (including the dedicated Android CLI), typed `job.gradle_tasks`, and declared Gradle/Android tests now lower through the same planner. Roadmap and federation execution consume those first-class workflow/CommandSpec surfaces rather than baking raw `./gradlew` commands into declarations.

Generic JVM restore no longer launches synthetic `gradle help`; an absent restore root is intentionally a no-op because dependency resolution belongs to the first real requested Gradle root.

### Argument and context truth

Managed switches are resolved once for Configuration Cache, build cache, daemon, parallelism, VFS and worker count. Raw equivalents must agree. Property-form equivalents such as `org.gradle.parallel` and `org.gradle.caching` converge into the same managed policy.

Raw arguments cannot override planner-owned project root, settings, Gradle user home, project cache, init-script/telemetry ownership, Java home or JVM heap. Raw property values are redacted in plan/receipt evidence while an opaque digest of the real in-memory argv keeps value changes identity-significant without leaking them.

TestSelection contributes a stable semantic identity to the Gradle request/plan so changing selected classes/tasks changes plan identity instead of reusing evidence from a different test scope.

### Aggregate safety

Legacy `qualified=true` aliases no longer authorize replacement. A replacement needs a qualification id over the exact original roots, replacement, context and build-logic digest. Changing build logic makes the qualification stale and causes the planner to retain the original required roots. The bounded build-logic scan records completeness; overflow fails closed for aggregate substitution and CC qualification instead of silently treating a truncated identity as authoritative.

### Resources and reuse layers

The policy separates default heap, phase minimum, explicit request, host-safe ceiling and effective heap. Lint/package floors can rise above the default until the independently calculated safe ceiling.

Balanced Termux/Android validation no longer means one worker + no daemon + no VFS + forced Gradle splitting. `balanced`, `constrained`, and `emergency` are explicit planner profiles. Android continues to own outer-process CPU/priority/memory-guard decisions without owning a second Gradle scheduler.

Configuration Cache is explicit `auto/on/off/required` truth. `summary` uses bounded console/report evidence. `standard` uses a public BuildEvents/BuildService task-completion adapter and is structurally CC-compatible; `full` keeps the bounded legacy detail adapter and is explicitly CC-incompatible. OCI embeds and byte-qualifies both adapters. ENV-G2 owns the real Gradle load + CC miss/store/hit proof.

### Semantic barriers

Physical process splitting is caused by semantic barriers rather than validation phase boundaries. X03 directly recognizes destructive `clean`, external/release effects, and device-bound roots and preserves caller-supplied typed barriers. The legacy `validation.split_gradle=true` compatibility switch is surfaced as an explicit-isolation barrier rather than treating phase names themselves as topology.

### Execution receipt / existing evidence stores

`run_specs` derives a `GradleExecutionReceipt` from the canonical plan plus observed Gradle progress. The receipt keeps requested roots, optimized roots and observed task outcomes distinct; records launcher/JDK/substrate and resource/reuse-layer state; and references artifacts, reports, diagnostics, EXO process identity, RER identity and RUN identity.

The receipt is projected into the existing `TaskFinished`/execution-store metadata. ENV does **not** create another scheduler, reuse planner or history store.

## Devtool dogfooding

The repository adds:

- wrapper command: `./devtoolw env-x03`
- workflow: `roadmap-env-x03`
- target-local `source-syntax` job
- first-class planner/caller/receipt/regression TestSpecs
- artifact bootstrap TestSpec: `roadmap-env-x03-artifact` → `./devtoolw env-x03`

Thus the implementation overlay itself runs targeted artifact validation through wrapper → invoke → workflow/EXO rather than launching one artifact-local pytest command. This targeted non-Gradle qualification is required on apply; ENV-G2 remains the separate authoritative real-Gradle gate.

## Qualification performed during construction

- bounded promise-audit loop: **3 passes; zero ENV-X03-owned gaps after pass 3**
- `./devtoolw env-x03`: **5 stages passed, 270 tests passed, 0 failed, 0 skipped, 0 Devtool warnings, 0 Devtool errors**
- Python compilation: passed
- `git diff --check`: passed
- packaging rebase: final artifact is based on the exact post-EXEC-G1 `.devtool.toml` and retains `execution_environment = "native-termux"`; the construction-only desktop mirror is not shipped

No real Gradle process is claimed here. That proof is intentionally owned by ENV-G2.

## Promise accounting

See `ENV_X03_PROMISE_LEDGER.json` and `ENV_X03_BOUNDED_PROMISE_AUDIT_20260929.md`. ENV-P073 through ENV-P088 are implemented at this boundary after the bounded audit/fix loop; real-process proof remains at ENV-G2 where identified. ENV-P089 through ENV-P092 remain explicitly later-owned by ENV-X04/G3 rather than being silently absorbed into X03.

## v5 native-Termux regression closure

The v4 native run proved the zero-warning hook lifecycle fix and exposed four remaining regression assumptions. v5 closes those without changing planner semantics: selected Gradle launcher version probing is single-pass, tests that mean balanced policy request it explicitly, automatic Android safe-policy tests follow the resolved evidence-driven worker/CPU values, and a separate explicit override regression preserves operator-control coverage. The targeted workflow now contains 270 tests.
