# ENV-G2 — Gradle policy / evidence milestone gate

Date: 2026-09-29
Campaign position: ENV gate-window 2/2; ENV scope 5/7; full implementation plan 18/63.
Audited implementation owner: ENV-X03, live commit `5b1175b`.
Next overlay after a successful gate: EXEC-X03 (19/63). There are 0 overlays remaining before the next major scope. Two ENV overlays remain later in dependency order (`ENV-X04`, `ENV-G3`). Next major scope: EXEC.

## Gate purpose

ENV-G2 is the separate authoritative real-process gate for the ENV-X03 Gradle planner / evidence milestone. It does not merge with ENV-X03 and does not pull forward ENV-X04 destination/adaptive/advanced-feature work. The gate validates the canonical Gradle planner through first-class Devtool workflow identities and then executes a dependency-free real Gradle fixture twice to prove Configuration Cache and receipt truth.

## Merge-window decision

The next-three window is `ENV-G2 -> EXEC-X03 -> EXEC-X04`. No merge is allowed: ENV-G2 is a gate, while the following overlays belong to EXEC architecture and scheduling/validation ownership.

## Promise reconciliation and folded fix

The gate audited ENV-P073..ENV-P088 and the GRADLE-G1/G2 milestone criteria against the live ENV-X03 implementation. One owned defect was found and folded into the gate candidate:

- **Semantic barrier reordering.** ENV-X03 split clean/effect/device barriers, but collected all non-barrier roots globally and inserted them before barrier invocations. `clean, assemble, check` could therefore become `assemble+check` then `clean`. The planner now coalesces only contiguous barrier-free regions and flushes each region before the next typed barrier, preserving semantic request order while retaining the minimum physical process count.

A deterministic planner regression and a real-process barrier proof both cover the repair. No second ENV-X03-owned gap was found in the bounded gate reconciliation.

## Authoritative validation architecture

There is no ad-hoc `validate.sh`. A construction lifecycle replay found that the post-EXEC-G1 `validation.refs = ["workflow:..."]` schema is accepted but workflow-only refs still resolve to zero **counted artifact validation checks** in the current apply accounting path. Required validation correctly fails closed instead of committing. That accounting defect is exported to EXEC-X03 rather than widened into ENV.

ENV-G2 therefore uses the already-proven first-class bootstrap path:

```text
validation.tests -> roadmap-env-g2-artifact
                       |
                       `-- ./devtoolw env-g2
                              |
                              `-- invoke roadmap-env-g2
                                   +-- target-local source-syntax
                                   +-- roadmap-env-g2-contracts
                                   +-- roadmap-env-g2-callers
                                   `-- roadmap-env-g2-real-gradle
```

This still dogfoods wrapper -> invoke -> workflow -> EXO/RUN execution; pytest remains only the leaf runner for bounded contract assertions.

The contract and caller nodes are intentionally bounded. The real-process node runs `python -m devtool.env_g2_gradle_gate`, requires `gradle` and `java`, and refuses `DEVTOOL_ENV_G2_GRADLE` unless the construction-only `DEVTOOL_ENV_G2_ALLOW_TEST_GRADLE=1` bit is also explicitly present. The distributed artifact does not set either override.

## Real Gradle proof performed by the gate

The authoritative process stage creates a temporary dependency-free Gradle fixture and a private Gradle user home, then enters Gradle through `JvmGradleRunner` and the canonical ENV-X03 planner. It requires:

1. three barrier-free requested roots to coalesce into one physical process;
2. a first real Gradle run that records Configuration Cache `miss` or `stored`;
3. a second unchanged run that records Configuration Cache `reused`;
4. stable execution-context and Configuration-Cache qualification identity across the pair;
5. requested/effective truth for CC, build cache, daemon, VFS, parallelism, two workers, heap/resource policy and standard telemetry;
6. launcher and JDK identities plus a real RUN reference in `GradleExecutionReceipt`;
7. requested roots to remain distinct from observed dependency-task outcomes;
8. a 51-task graph to emit bounded structured telemetry (event count bounded by observed tasks, each line <=4096 bytes, no legacy artifact-output scan);
9. `clean, gateAlpha, gateBeta` to produce exactly two real processes in semantic order: `clean`, then a coalesced `gateAlpha+gateBeta`; and
10. all three barrier-proof roots to be observed as successful real Gradle outcomes.

The gate separately proves unqualified aggregate aliases fail closed, build-logic overflow fails closed, required CC rejects incompatible telemetry/filesystems, heap clamp evidence is truthful, adaptive-vs-explicit resource policy is intentional, and Android/JVM/workflow/TestSpec/artifact/instrumentation callers converge on the canonical planner.

## Construction qualification vs authoritative evidence

This build environment has Java but no installed real Gradle suitable for authoritative qualification. Construction therefore used an explicitly allowed fake `gradle` only to prove workflow wiring, event/receipt parsing, miss->hit state handling, and barrier-process accounting. That check completed 4 stages / 16 tests / 0 warnings/errors. It is **not** recorded as real-process gate evidence.

The artifact apply is authoritative because the construction override is forbidden by default and the `real-gradle` TestSpec requires an actual `gradle` executable. A commit can be produced only after that native gate passes.

The merged ENV-G2 authority specifically closes the native-Termux balanced-mode milestone here. The same real fixture remains portable for later chroot/desktop/CI replay, and ENV-G3 retains final cross-substrate environment/toolchain sealing; this gate does not fabricate evidence for substrates not executed by the live apply.

## Gate verdict

Candidate is sealable only when the complete `roadmap-env-g2` artifact workflow passes with zero failed/skipped required tests and zero warnings/errors. A successful artifact commit therefore means the real Gradle miss/store->reused transition, bounded telemetry, canonical caller reconciliation, truthful resource/reuse policy, semantic barrier physical-process proof and receipt invariants all passed on the live gate host.
