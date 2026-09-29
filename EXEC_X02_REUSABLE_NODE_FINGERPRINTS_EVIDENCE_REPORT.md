# EXEC-X02 — Reusable-node fingerprints, evidence qualification and runner contribution contract

## Campaign position

- Gate-window: **2 of 3** (`EXEC-X01`, `EXEC-X02`, `EXEC-G1`)
- EXEC scope: **2 of 7**
- Full plan: **15 of 63**
- Base: post-EXEC-X01 (`2a72e43` on the user repository; reconstructed replay bytes are equivalent)
- Next: **EXEC-G1 — reuse correctness milestone**
- Next major scope after this gate window: **ENV-X03**

## Merge-window decision

`EXEC-X02`, `EXEC-G1`, and `ENV-X03` remain separate. X02 implements fingerprint/evidence qualification; G1 independently proves reuse correctness; ENV-X03 returns to environment ownership.

## Delivered architecture

EXEC-X02 adds a side-effect-light RER qualification layer under `devtool.core.reuse` while keeping EXO/RUN history authoritative. No second cache database or executable graph was added.

Delivered contracts:

- `InputManifest` with explicit → runner-inferred → conservative repository-content fallback precedence.
- Content identity based on bytes/Merkle identity; conservative Git fast-path uses HEAD + actual binary diff + untracked content, never timestamps as semantic proof.
- `DependencyFingerprint` with terminal upstream result-identity propagation from canonical EXO history.
- `ExecutionEnvironmentFingerprint` and `PortabilityClass` with relevant semantic domains and destination/provider/device/host policy axes.
- Secret-dependent work is non-reusable unless a safe secret identity is supplied; raw secret values are never persisted by RER.
- `OutputManifest` covering required outputs, evidence hashes, and digest-addressed materialization provenance.
- Output/evidence verifier; missing or mutated required proof prevents REUSE.
- `RunnerReuseContribution` generic contribution contract that consumes runner/ENV metadata rather than re-implementing Python/Go/Android resolution.
- Explainable `FingerprintDiff` and `ReuseQualification` with source run/node and proof references.
- `ReuseHistoryIndex` rebuilt from terminal EXO snapshots; lookups do not mutate terminal history.
- ReusableNodeRecord schema v2: immutable fingerprint/output/result proof attached to canonical EXO terminal node history.
- Ordered multi-spec command fingerprints and output manifests aggregate at the canonical workflow node.
- `REUSE`, `MATERIALIZE`, `INVALIDATED`, and `RUN` qualification is implemented, but **authoritative scheduler skipping remains disabled until EXEC-G1**.

## Audit loop findings folded before closure

1. **Unverified materialization references** — arbitrary strings could initially request MATERIALIZE. Fixed: only digest-addressed provenance (or locally verified file+SHA provenance) qualifies.
2. **Dependency fingerprint API without runtime propagation** — fixed at EXO terminal record creation using upstream result identities.
3. **Workflow node identity overwrite** — command-level identity could overwrite X01 canonical workflow-node identity. Fixed: all lowered specs receive the workflow node's `NodeDefinitionIdentity`; command variation stays inside per-spec fingerprints.
4. **Multi-spec output truncation** — only the final spec's outputs were retained. Fixed: all spec output/evidence manifests aggregate at node terminal history.
5. **Missing declared output overclaim** — successful process status could produce `reusable=true` despite a missing declared output. Fixed: output completeness is part of terminal qualification.
6. **Corrupt raw history bypass** — qualification now independently validates schema, fingerprint structure, provenance, result identity, and output-proof structure before considering reuse.

No additional EXEC-X02-owned gap remained after the final bounded audit pass.

## Promise accounting

Delivered/advanced in this overlay: `EXEC-P012`, `EXEC-P014..P024`, `EXEC-P026`, `EXEC-P027`, `EXEC-P032`, `EXEC-P034`, `EXEC-P035`, and the generic runner-contribution portion of `EXEC-P069`. `EXEC-P013` remains preserved and is strengthened by fail-safe qualification parsing. `EXEC-P023` vocabulary was introduced in X01 and becomes operationally meaningful here.

Not falsely claimed here:

- visible scheduler-level REUSED lifecycle (`EXEC-P025`) — gated by EXEC-G1 and later planner adoption;
- automatic orchestration reuse / direct-run policy (`EXEC-P028`) — later planner/adoption work;
- interruption recovery and cross-run selective execution (`EXEC-P029..P031`) — EXEC-X04/LIFE integration;
- retention/pinning (`EXEC-P033`) — LIFE/RUN integration;
- full validation RER consumption (`EXEC-P062`) — EXEC-X03;
- federation/remote reuse adoption (`EXEC-P076..P077`) — EXEC-X04 + DIST;
- rendering/explain UI surfaces (`EXEC-P078..P080`) — PRES/EXEC later adoption.

## Devtool-native qualification

Reusable first-class surfaces were added:

- test: `roadmap-exec-x02`
- workflow: `roadmap-exec-x02` (`source-syntax → roadmap-exec-x02`)

Final source qualification:

- `./devtoolw test -- roadmap-exec-x02` (construction-host target temporarily mapped to desktop Linux only): **101 passed, 0 failed, 0 skipped, 0 Devtool warnings/errors**.
- native Devtool workflow invoke: **2 stages passed, 101 tests passed, 0 failed/skipped, 0 warnings/errors**.

Artifact validation is declared through structured `validation.tests`; there is no validation shell script.

## Clean-base artifact replay

The built v1 ZIP was applied with baseline post-EXEC-X01 Devtool against a clean reconstructed base. Result: **8 lifecycle stages passed; 101 passed, 0 failed, 0 skipped; 0 Devtool warnings/errors; finalize, single commit, Git index reconcile, apply verification, and artifact cleanup all succeeded**. The replay commit is construction-only and is not the user's canonical commit.

## Exported existing obligations

- LIFE: pre-existing hook file-handle `ResourceWarning`.
- TEST/PRES: multi-suite artifact result aggregation/projection bug previously observed.
- TEST/RMP: stale roadmap identity allowlists previously confirmed on untouched baseline.

These were not pulled into EXEC-X02.
