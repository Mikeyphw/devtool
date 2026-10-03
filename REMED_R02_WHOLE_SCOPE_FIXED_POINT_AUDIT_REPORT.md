# REMED-R02 whole-scope fixed-point audit hotfix

## Scope and position

- Scope audited: **REMED-R02 as a whole** after R02A v2 and R02B v2.
- Gate-window position: **R02 whole-scope audit between 4/8 and 5/8**.
- Full-plan context: R02A **73/93**, R02B **74/93**; next implementation overlay is **REMED-R03A, 75/93**.
- Current promise maxima retained for this remediation window: `CORE-P084`, `EXEC-P087`, `ENV-P131`, `ANDROID-P111`, `LIFE-P127`.

## Result

R02 is **not a final closed whole-scope seal**. It is a valid implementation window with explicit exports.

The source/runtime audit agrees with the corrected overlay reports:

- R02A v2 delivered scheduler/RUN substrate for normal `run_specs`/workflow/CLI ingress.
- R02B v2 delivered canonical test receipt projection lowering, failed-history selection, and Android count truth improvements.
- Several broader validation, test-system, Android taxonomy/evidence, universal rerun/fresh, provider/lifecycle, cleanup, and final evidence-resolution promises remain **partial**, **deferred**, or **exported**.

This hotfix prevents R02 from being misread as fully closed by adding a whole-scope disposition and correcting one contradictory R02B field.

## Corrected inconsistency

`REMED_R02B_PROMISE_DISPOSITION.json` previously listed `EXEC-A01` and `EXEC-A02` inside the folded hotfix's `fixed_findings`, while also keeping them in `partial_findings`. That was not truthful enough.

The corrected disposition says:

- H01 fixed the direct/library TestRunReceipt `event_run_id` binding gap and stale failed-history projection resurrection.
- Those fixes close the relevant `TEST-A09` / `TEST-A13` production gaps.
- `EXEC-A01` / `EXEC-A02` were touched by the same code paths but remain partial because validation phase/progress/resume/control compatibility state still exists and must be requalified by later fixed-point gates.

## Fully delivered by R02

- `EXEC-A04` — scheduler consults RER/RUN before spawn and honors no-spawn dispositions.
- `EXEC-A05` — reuse candidates are rebuilt from EXO/RUN history rather than projection caches.
- `EXEC-A07` — MATERIALIZE restores verified outputs without process spawn.
- `TEST-A02` — duplicate global test-run store is projection-only with canonical receipt pointer.
- `TEST-A09` — failed-test selection uses verified canonical receipts when available and clean canonical receipts suppress stale projections.
- `TEST-A13` — canonical TestRunReceipts bind repository, run identity, operations, testcase results, selection/environment refs, reports/logs/artifacts carried by operation metadata, and digest.
- `ANDROID-A05` — setup/install/runner readiness failures no longer fabricate failed testcase counts.
- `ANDROID-A06` — required zero-test behavior preserves executed/failed testcase counts at zero and fails through verdict/execution dimensions.

## Partial or exported by design after R02

- `EXEC-A01` / `EXEC-A02` — validation state is projection-marked and no longer stores replayable attempt argv, but validation resume/progress/control/session state remains compatibility state.
- `EXEC-A06` / `EXEC-A13` — normal workflow/direct execution adopted the scheduler boundary, but all domain-specific cache/reuse/resume paths and consumers still need later fixed-point audit.
- `EXEC-A08` — universal fresh/rerun/rerun-from controls remain deferred.
- `TEST-A01`, `TEST-A03`, `TEST-A05`, `TEST-A06`, `TEST-A07`, `TEST-A10`, `TEST-A14`, `TEST-A15` remain partial or exported for later test-system/gate work.
- `ANDROID-A03`, `ANDROID-A04`, `ANDROID-A07`, `ANDROID-A08`, `ANDROID-A10`, `ANDROID-A11` remain partial/exported beyond the count-truth fixes.
- Remote/provider parity, lifecycle/cleanup authority, and final gate evidence/hash/current-HEAD proof remain R03/R04/G3R responsibilities.

## Production paths checked

- `devtool.runners.run_specs` always enters `execution_scheduler_truth.decisions_for_specs` for normal command specs and cannot be disabled by mutable spec metadata.
- `execution_scheduler_truth.ReuseHistoryIndex.from_history_directory(...)` is the decision source; projection-only `reuse-index.json` cannot authorize reuse.
- `materialize_outputs(...)` verifies `file:<path>#sha256=<digest>` proof refs before writing outputs.
- `test_runstore.persist_test_run(...)` writes compatibility projections plus canonical RUN-V3 receipts.
- `test_selection.load_failed_test_ids(...)` uses the latest valid canonical receipt when present and treats a clean canonical receipt as authoritative.
- `validation_sessions.ValidationSession.as_dict`, validation plan/event writing, and phase result recording identify validation state as projection/reference-state and do not persist replayable attempt argv.
- `android.instrumentation` count classification paths preserve setup/zero-test failures as execution/verdict failures rather than fabricated testcase failures.

## Targeted validation

Native workflow declared by this hotfix:

```bash
./devtoolw remed-r02-whole-audit
```

The workflow runs:

- `tests/test_remed_r02_whole_scope_audit.py`
- retained R02A focused tests
- retained R02B focused tests

## Next

- Next overlay: **REMED-R03A — Provider & Lifecycle Authority, 75/93**.
- Remaining remediation implementation overlays before G3R: **4**.
- Next major phase after remediation overlays: **CORE-G3R at 79/93**.
