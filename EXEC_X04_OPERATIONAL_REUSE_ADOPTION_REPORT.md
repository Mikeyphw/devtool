# EXEC-X04 — Operational reuse adoption

## Position

- Gate-window: 1/2 before EXEC-G3 final seal
- EXEC scope: 6/7
- Full plan: 21/63
- Previous commit expected in live repository: EXEC-G2 `2b19877`

## Scope

EXEC-X04 adopts the generic EXEC/RER/EXO validation and reuse contracts across the operational surfaces that consume them:

- local workflows, gates and seals;
- native runner frontends;
- selected-test execution;
- Android/device proof integration references;
- transaction/apply validation and `--no-validate` policy;
- federation resume shared predicates and result identities;
- remote/provider portability boundaries;
- roadmap/gate execution graphs;
- explicit `fresh`, `rerun` and `rerun_from` controls;
- source-proof retention hooks;
- singleflight candidate qualification;
- rebuildable reuse indexes;
- why-reuse / why-rerun explanations and validation diff projections;
- node-scoped performance metrics for hashing versus avoided work.

It does not take over owning programs:

- Gradle/native task planning remains ENV/runner-owned;
- rollback/recovery remains LIFE-owned;
- delegated transport/event delivery remains DIST-owned;
- pytest lane organization remains TEST-owned;
- roadmap promise policy remains RMP-owned;
- human/TUI/Desktop/Android rendering remains PRES-owned.

## Implementation

Added `devtool.exec_operational_adoption`, a side-effect-light projection layer that consumes canonical `CommandSpec` fingerprints and `ReusePlanner` results. It provides:

- `OperationalContext` for domain/operation/provider/target/selection/device/remote/fresh/rerun identity;
- `build_operational_adoption_plan()` for a generic `RUN`, `REUSE`, `MATERIALIZE`, or invalidation decision list;
- validation-proof eligibility that prevents `--no-validate` from fabricating proof;
- source-proof retention refs for reused/materialized records;
- singleflight qualification tokens keyed by canonical identity;
- rebuildable indexes from history projections;
- why-reuse / why-rerun and diff projections for PRES consumers.

The new layer keeps execution history, reuse qualification, and validation truth in the existing authorities: EXO, RER and validation contracts. It stores no process attempts, no stdout/stderr, no progress counters, no transport state, and no presentation state.

## Targeted validation

The overlay adds `./devtoolw exec-x04`, which runs:

1. source syntax;
2. EXEC-X04 operational-adoption contracts;
3. retained EXEC-G2 / EXEC-X03 regression slice.

This is an implementation overlay, not the final EXEC seal. EXEC-G3 remains responsible for the mixed executed/reused/materialized final acceptance scenario.

## Delivered promises

The overlay delivers the operational adoption portion of EXEC-P028 through EXEC-P036, EXEC-P060 through EXEC-P081, and the EXEC-X04 portions of EXEC-P076 through EXEC-P081.

## Exported obligations

- `PRES-X04`: render why-reuse, why-rerun and validation diff projections.
- `DIST-X04`: transport remote/federated event delivery from EXEC projection records.
- `LIFE-X04`: rollback/recovery implementation and transaction-state equivalence.
- `TEST-X04`: selected-test lane organization and testcase outcome semantics.
- `ENV-X04`: native toolchain / runner contribution semantics.
- `ANDROID-X04`: device freshness and instrumentation proof domain ownership.
- `RMP-X04/G3`: roadmap promise policy and final grand seal sequencing.
- `EXEC-G3`: final mixed graph acceptance with late-failure selective reuse.
