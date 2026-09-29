# EXEC-G2 — One-graph validation proof gate

## Position

- Gate-window position: 2 of 2
- EXEC scope position: 5 of 7
- Full implementation-plan position: 20 of 63
- Base state: post-ENV-G2 and post-EXEC-X03 (`e6f6c96` on the user device)
- Next overlay: EXEC-X04

## Merge-window decision

The next-three window was `EXEC-G2 -> EXEC-X04 -> ENV-X04`.

No merge was performed. EXEC-G2 is the separate authoritative one-graph validation gate; EXEC-X04 is an implementation/adoption boundary for operational reuse, transactions, federation, roadmap/gate execution, and native-runner adoption; ENV-X04 is a separate environment/native-runner planning boundary.

## Gate purpose

EXEC-X03 introduced the one-graph durable validation model. EXEC-G2 proves that model without adding a second validation database, validation-owned scheduler, or presentation-derived contract selection.

The gate proves:

- EXO remains the executable graph/status/attempt/progress/event authority.
- RER remains the reusable-result qualification/invalidation authority.
- Validation owns one generic resolved contract and one satisfaction evaluator.
- Validation private durable state is reference-only and secret-safe.
- Repo-local reports/session files are compatibility projections, not canonical truth.
- Generic skipped execution cannot satisfy a required check without applicability evidence.
- Empty-required and optional-only validation contracts resolve correctly.
- Output/presentation flags cannot change selected validation work.
- Semantic controls are revision-guarded and do not create conflicting lifecycle transitions.
- Administrative `finalize` is checkpoint satisfaction, not a fake executable process.
- Workflow-only artifact validation refs count as executed validation checks.
- EXO/RUN status projection is bounded and read-only.

## Implementation notes

This overlay is gate-shaped and intentionally small:

- Adds `roadmap-exec-g2` wrapper/workflow entries.
- Adds `roadmap-exec-g2-one-graph-proof`, `roadmap-exec-g2-x03-regression`, and `roadmap-exec-g2-artifact-bootstrap` TestSpecs.
- Adds `tests/test_exec_g2_validation_proof_gate.py`.
- Adds this report plus a promise ledger and exported obligations.

No broad EXEC-X04 behavior is implemented here. Cross-run operational adoption, federation/transaction adoption, same-run continuation, fresh/rerun controls, why-reuse/why-rerun UX data, and hashing-vs-work metrics remain EXEC-X04 / EXEC-G3 territory.

## Qualification

Direct bounded gate proof:

```text
8 passed
```

Retained X03 regression slice:

```text
48 passed
```

Construction wrapper workflow in a disposable desktop mirror:

```text
./devtoolw exec-g2

3 stages passed
56 tests passed
0 failed
0 skipped
0 warnings
0 errors
```

The downloadable artifact remains `native-termux`; the desktop mirror was only used to exercise wrapper/workflow mechanics in this environment.

## Delivered promises

Primary delivered promises:

- EXEC-P001 through EXEC-P005: one authority split; no second execution/history/validation store.
- EXEC-P037 through EXEC-P049: one validation request/contract/evaluator/outcome/verdict reference model.
- EXEC-P050 through EXEC-P058: plan pinning, controls, semantic checkpoints, and no synthetic finalize process.
- EXEC-P062 through EXEC-P066: RER-backed validation invalidation, private RUN-owned evidence/state, repo cleanliness, report projection, and shared classification.
- EXEC-P071 through EXEC-P075: mandatory validation evidence, delivery artifact contract convergence, LIFE transaction references, and no fake proof for `--no-validate`.

## Exports

- EXEC-X04: operational adoption across native runners, local workflows/gates/seals, transactions, federation, roadmap execution graphs, selected-test execution, fresh/rerun controls, continuation, source-proof retention, and semantic explain/diff data.
- ENV-X04: Gradle/native task plan and environment/native-runner detail remains ENV/runner-owned.
- ANDROID: device proof integration remains Android-owned.
- LIFE: rollback/recovery implementation remains LIFE-owned.
- PRES: human/TUI/Desktop rendering remains presentation-owned.
- EXEC-G3: final mixed executed/reused/materialized graph and late-failure selective-reuse proof.
