# EXEC-X03 — One-Graph Durable Validation Convergence

**Position:** EXEC-X03 — gate-window 1/2; EXEC scope 4/7; full-plan 19/63.

## Merge-window decision

The next-three window was `EXEC-X03 → EXEC-G2 → ENV-X04`. The items were not merged:

- `EXEC-X03` is an implementation boundary for one-graph durable validation convergence.
- `EXEC-G2` is the separate authoritative one-graph validation proof gate.
- `ENV-X04` is an ENV implementation boundary for later Gradle planning/adaptive history work.

## Delivered implementation

This overlay introduces the EXEC-X03 convergence layer without creating a second execution or validation truth store:

- `devtool.core.reuse_planner.ReusePlanner` is a pure side-effect-free planner over EXO-derived RER history indexes.
- Validation specs are bound to canonical node identities and RER fingerprints before durable plan pinning.
- Validation session state gains a canonical private reference projection under Devtool state, while the legacy repo-local phase/session files remain compatibility projections.
- The canonical private state records contract, checkpoint, control, verdict, EXO/RER/RUN references and compact per-check node metadata; it does not own process attempts, progress counters, event logs, resource scheduling, rollback, or execution history.
- `next`, `remaining/finish`, `through/until-checkpoint`, and `selected` semantic controls are represented by `ValidationControlIntent` and lowered to compatibility phase keys only at the boundary.
- Administrative `finalize` is treated as checkpoint satisfaction over required proof checks; it is not a fake executable validation phase.
- RUN-style private plan/report attachments are written alongside the canonical private state.
- Validation status exposes a bounded reference projection rather than copying EXO/RUN event truth into a validation-owned store.
- Artifact validation refs now count as validation checks for artifact lifecycle execution. Workflow-only refs no longer produce a schema-valid but execution-empty required validation.

## ENV-G2 export folded

ENV-G2 exported a defect where workflow-only `validation.refs` were schema-valid but were not counted by the artifact validation lifecycle. EXEC-X03 folds that directly by treating refs as an artifact validation surface alongside `validation.tests` and validation scripts.

## Ownership boundaries preserved

EXEC-X03 does **not** implement broad cross-run selective skipping, transaction/federation adoption, `--fresh`/rerun controls, or performance metric reporting. Those remain EXEC-X04/EXEC-G2/EXEC-G3 work. ENV remains owner for native Gradle task planning and process topology.

## Qualification

Direct bounded regression:

```text
170 passed, 0 failed, 0 skipped
```

Construction wrapper workflow in disposable desktop mirror:

```text
./devtoolw exec-x03
4 stages passed
170 tests passed
0 failed
0 skipped
0 warnings
0 errors
```

The downloadable overlay remains targeted at `native-termux`; the desktop mirror was used only for construction proof in this environment.

## Native apply log review and redo note

The first native Termux apply of `exec_x03_one_graph_validation_convergence_v1.zip` proved the targeted validation path did run as intended: artifact validation invoked `./devtoolw exec-x03`, which entered the Devtool wrapper/invoke/workflow path and completed four stages with 170 tests, 0 warnings, and 0 errors. The failure happened after validation, in the artifact finalize hook, because the hook ran an editable install and produced undeclared `devtool.egg-info` generated packaging metadata.

This redo removes the finalize hook entirely. EXEC-X03 does not require a post-apply editable-install refresh to establish its technical contract, and generated packaging metadata must not be declared as source payload. The artifact therefore keeps targeted validation enabled and relies on the already-proven wrapper workflow, while leaving the transaction worktree inside the declared write boundary.
