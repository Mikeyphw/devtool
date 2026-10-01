# EXEC-G3 final execution / reuse / validation seal

## Position

- Roadmap: **EXEC-G3**
- Full plan: **69/77**
- EXEC gate window: **2/2**
- Next-three decision: **no merge** (`EXEC-G3 → PRES-G3 → RMP-X01`).

## Why this gate was reopened

A real finalization incident reported `cannot finalize execution run with nonterminal node(s): xdm_modern.validate.$runner, xgo_final_wave12.validate.wave12_final_gate`. The audit showed this was not merely a finalizer presentation problem: worker exceptions and early/fail-fast exits could leave already-published workflow descendants nonterminal in canonical EXO/RUN truth even though local scheduler status had moved on.

## Runtime correction owned by EXEC-G3

1. Workflow worker `BaseException` paths now emit a canonical failed `NodeFinished` event before returning the synthetic scheduler result.
2. Final lifecycle closure reconciles dangling node bookkeeping only after proving there are no live processes.
3. Failed/blocked/cancelled outcomes terminalize unresolved descendants without overwriting an already-observed primary failure.
4. A requested success with unresolved required work **fails closed** (`unobserved_required_work`) and returns nonzero; it never fabricates successful execution.
5. Historical failed runs containing dangling descendants can be re-finalized idempotently.
6. The CLI uses the authoritative final snapshot to correct a nominal zero exit when reconciliation proves the run failed.

## Final EXEC acceptance

The gate derives evidence from retained EXEC authorities and fresh-process proofs:

- explicit retained coverage for `EXEC-P001..EXEC-P082` without inventing meanings for historical range-only P083..P087 references;
- mixed execution decision proof: one executed node, one reused node, one materialized node;
- late semantic input mutation invalidates only the affected reusable frontier;
- source proof remains pinned for reused/materialized decisions;
- same-run interrupted validation remains recoverable/retryable under retained validation-session authority;
- exact XDM/XGO dangling-node names are exercised and the primary exit-7 failure remains authoritative;
- active processes remain a hard finalization barrier.

## Construction fixed point

Stage-shaped commands from the authoritative `roadmap-exec-g3` workflow pass locally:

- Gate/incident contracts: **6 passed**.
- Retained EXEC identity/reuse/evidence foundation: **58 passed**.
- Retained one-graph validation convergence: **13 passed**.
- Operational reuse + Gradle ingress: **29 passed**.
- EXO scheduler/finalization lifecycle: **26 passed**.
- Same-run interruption/resume validation-session slice: **7 passed** (38 unrelated cases deselected).
- Canonical generated surfaces + release integrity: **27 passed**.
- `python -m devtool.exec_g3_gate`: **PASS**.

Native Termux execution of `./devtoolw exec-g3` remains authoritative for applying the seal.

## Native revision history

- **v1** failed and rolled back cleanly on native Termux: run `060a039b99fc401a97171607bcaa6111`, duration 212.85s, zero warnings and one validation error. The retained EXO-P0 nested CLI probe inherited/saw the diagnostic-only `host-android` substrate during artifact validation and the current execution-environment parser correctly rejected it.
- **v2** failed and rolled back cleanly on native Termux: run `c4ad9ed376324718a55e8ad6112de6bf`, duration 208.28s, zero warnings and one validation error. v2 correctly removed inherited routing variables but then explicitly forced `native-termux`; artifact validation had intentionally sanitized the Termux markers used by canonical host detection, so the nested process observed `host-android` and native-Termux preflight correctly rejected the mismatch (`termux: host-android`).
- **v3** keeps all EXEC runtime corrections unchanged and fixes only the nested qualification boundary. Generic EXO-P0 probes clear inherited routing overrides and use the public `--execution-environment host` selector. That selector resolves to whatever substrate the nested process actually observes, so sanitized Android validation can remain `host-android` without pretending it is a configurable execution backend or weakening canonical environment parsing.
- A construction regression explicitly monkeypatches Android host detection, removes Termux markers, and proves the nested three-node workflow still executes and seals successfully under the `host` selector. The complete construction fixed point is **166 pytest tests passed** plus the fresh-process EXEC-G3 final proof.

## Next

On successful qualification, EXEC finalization is closed and the next overlay is **PRES-G3 70/77**, the final cross-surface presentation seal.


## Native closure

EXEC-G3 v3 qualified/applied on native Termux in run `b9bff06189b04df6b6f7ac4336feb94b`: **341 passed, 0 failed, 896 skipped (1237 total)**, zero diagnostics, commit `87baef9`. EXEC finalization is closed at **69/77** and the active item advances to **PRES-G3 70/77**.
