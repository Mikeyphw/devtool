# EXEC-X01 — Canonical execution intent + validation request/contract foundation

## Position

- Gate-window: **EXEC overlay 1 of 3** (`EXEC-X01`, `EXEC-X02`, `EXEC-G1`).
- EXEC major-scope position: **1 of 7**.
- Full implementation plan: **14 of 63**.
- Merge-window decision: **do not merge** with `EXEC-X02` or `EXEC-G1`.
- Next overlay: **EXEC-X02** (15/63).
- Remaining before leaving this EXEC gate window: **2** (`EXEC-X02`, `EXEC-G1`).
- Next roadmap scope after `EXEC-G1`: **ENV**, returning at `ENV-X03` (17/63).

## Why this overlay is separate

`EXEC-X01` establishes semantic identity and validation-contract authority but deliberately leaves reusable-result qualification and actual skipping disabled. `EXEC-X02` owns fingerprints, evidence/output qualification, environment/dependency contribution and materialization proof. `EXEC-G1` is the separate correctness gate. Combining those layers would make identity/schema failures indistinguishable from qualification/fingerprint failures.

## Delivered semantic foundation

`devtool/core/execution_semantics.py` introduces side-effect-free canonical types without creating a second scheduler, run store, validation store or reuse database:

- `ExecutionIntent`
- `ExecutionRoot` / `ExecutionRootKind`
- `ToolInvocation`
- `NodeDefinitionIdentity`
- `ReuseDisposition`
- `ReusableNodeRecord`
- `ValidationControlIntent` / `ValidationControlAction`
- `ValidationRunRequest`
- `ValidationCheck`
- `ApplicabilityPolicy`
- `CheckpointPolicy`
- `ResolvedValidationContract`
- `ValidationOutcome` / `ValidationOutcomeState`
- `ValidationVerdict` / `ValidationVerdictStatus`

The vocabulary includes distinct `REUSE`, `MATERIALIZE`, `RUN`, `INVALIDATED` and `BLOCKED` dispositions, but X01 remains **shadow-only**: every reuse prediction is fail-safe `RUN`, and no existing work is skipped because of the new metadata.

## EXO / reusable-record convergence

`ExecutionStore` now attaches a stable `NodeDefinitionIdentity`, canonical `ExecutionIntent`, and shadow reuse prediction when a workflow graph is planned. On terminal completion it attaches an immutable `ReusableNodeRecord` shell to the canonical EXO node history. No parallel reuse cache/history database is introduced.

Unknown, legacy-incomplete or corrupt reusable-record metadata normalizes to `RUN`, `reusable=false`, and `qualification_complete=false`.

The same workflow node lowered through `invoke` and the future canonical `run` entrypoint produces the same semantic `ExecutionIntent`; per-run IDs are excluded from `NodeDefinitionIdentity`.

## Validation correctness migration

Validation sessions advance to schema v7 and persist the typed semantic projection:

- one `ValidationRunRequest`;
- one `ResolvedValidationContract`;
- one `ValidationVerdict`.

The durable validation report exposes those three semantic objects under `semantics`, making the new authority observable rather than private implementation state.

Correctness fixes folded into X01:

1. **Presentation/output invariance** — JSON/text, verbosity and copy/presentation flags do not change the semantic validation request.
2. **Text/JSON control parity** — invalid control scopes are rejected by the same typed validation-control boundary instead of divergent argparse behavior.
3. **`validate --task` migration boundary** — task names become compatibility `ExecutionRoot(NATIVE_TASK)` values and no longer leak into semantic `TestSelection`.
4. **Typed check identity** — new canonical checks require `validation_check_id`; command-name inference is migration-only and records provenance.
5. **Required vs optional vs applicability** — a generic execution `skipped` cannot satisfy a required check; explicit not-applicable evidence can.
6. **Empty / optional-only contracts** — the required set is vacuously satisfied, while selected optional checks still must finish before session completion.
7. **Single satisfaction authority** — schema-v7 session completion consumes `ValidationVerdict`; legacy phase-state evaluation remains only a migration fallback for old durable sessions.
8. **Synthetic finalize semantics** — administrative finalize phases are excluded from the canonical `ResolvedValidationContract` and do not become fake validation proof.
9. **Revision guard preservation** — `ValidationControlIntent` carries the existing expected-revision guard without creating another lifecycle controller.
10. **Android compatibility** — Android Gradle finalize is explicitly marked administrative/non-required so it cannot become a synthetic proof obligation.

## Promise accounting

### Delivered by EXEC-X01

- `EXEC-P006` — canonical `ExecutionIntent` distinguishes action/job/test/validation/delivery context.
- `EXEC-P007` — `ExecutionRoot` is distinct from `TestSelection`.
- `EXEC-P008` — `ValidationCheck` is distinct from executable root identity.
- `EXEC-P009` — `ToolInvocation` separates managed, raw passthrough and protected identity argv.
- `EXEC-P010` — stable cross-run `NodeDefinitionIdentity` is distinct from per-run node ID.
- `EXEC-P011` — reusable-record shell is attached to canonical EXO terminal history.
- `EXEC-P013` — unknown/legacy/corrupt reusable metadata fails safe to `RUN`.
- `EXEC-P023` — canonical reuse disposition vocabulary exists.
- `EXEC-P037` — one typed `ValidationRunRequest` is resolved before presentation handling.
- `EXEC-P038` — presentation/output flags are excluded from semantic validation identity.
- `EXEC-P039` — one `ResolvedValidationContract` and one satisfaction evaluator are canonical for new sessions.
- `EXEC-P040` — new validation checks require stable typed identity.
- `EXEC-P041` — command-name inference is migration-only and provenance-bearing.
- `EXEC-P042` — required/optional/applicability semantics are separate from execution lifecycle status.
- `EXEC-P043` — generic skipped execution cannot satisfy a required check.
- `EXEC-P044` — empty required set / optional-only contract semantics are correct.
- `EXEC-P045` — `ValidationOutcome` is distinct from EXO execution status.
- `EXEC-P046` — `ValidationVerdict` is distinct from execution/transaction state.

### Preserved/bridged but not claimed complete here

- `EXEC-P001`, `P003`, `P004`, `P005` — existing EXO/Validation/RUN authority boundaries are preserved; X01 introduces no competing store.
- `EXEC-P050`, `P052` — existing plan pinning and revision-guard behavior remain intact while typed request/control identity is added.
- `EXEC-P058` — synthetic finalize is removed from the **canonical contract model**; broader legacy scheduler cleanup remains for later convergence work.
- `EXEC-P078` — validation report now exposes the semantic request/contract/verdict foundation; complete cross-surface PRES adoption is later-owned.
- `EXEC-P082` — this implementation uses bounded non-Gradle validation; no Gradle qualification is pulled into the implementation overlay.

### Deliberately deferred to EXEC-X02 / later EXEC gates

`EXEC-P012`, `P014..P022`, `P024..P036`, `P047..P049`, `P051`, `P053..P057`, `P059..P077`, `P079..P087` are not falsely closed here. X01 supplies identity/schema foundations where applicable, but qualification, fingerprints, materialization, one-graph convergence, explain/diff, transaction/federation adoption and final mixed executed/reused proof remain with their canonical later owners.

## Audit-loop findings

The X01 implementation audit caught and fixed these gaps before packaging:

- optional-only sessions initially completed before selected optional checks ran;
- legacy command-name inference provenance was overwritten during annotation;
- an untyped new check could silently derive identity from its command name;
- schema-v7 session completion initially duplicated required-check satisfaction instead of consuming the canonical verdict;
- text vs JSON validation-control normalization diverged;
- `validate --task` leaked into `TestSelection` rather than remaining a compatibility execution-root request;
- old/corrupt reusable metadata needed an explicit fail-safe normalization path;
- canonical semantics existed internally but were not initially exposed by durable validation reports.

No remaining EXEC-X01-owned gap was found after the final bounded audit.

## Exported baseline / cross-program obligations

1. **TEST/RMP roadmap inventory adoption:** `tests/test_devtool_roadmap_native_execution_contract.py` contains stale hard-coded `EXPECTED_TESTS` / `EXPECTED_WORKFLOWS` inventories that predate newer TEST/ENV roadmap identities. The failure reproduces on untouched post-ENV-G1 source. Owner: TEST stable inventory/adoption + RMP roadmap definition/adoption; do not patch inside EXEC-X01.
2. **LIFE hook resource leak:** the final first-class replay still emits the existing `ResourceWarning` for an unclosed file at `devtool/hooks/engine.py:523`. It is already LIFE-owned and is not an EXEC-X01 regression.
3. **TEST/PRES multi-suite aggregation:** the previously observed artifact summary that reports only the last structured test suite remains owned by TEST/PRES; X01 does not create another result projection.

## Devtool-native validation

The repository declares first-class test identity and workflow `roadmap-exec-x01`.

Final exact-byte dogfood results on the construction host (target temporarily mapped only for local Linux execution, never packaged):

- `./devtoolw test -- roadmap-exec-x01` → **195 passed, 0 failed, 0 skipped**.
- `python -m devtool --execution-environment desktop-linux invoke roadmap-exec-x01` → **2 stages passed, 195 tests passed, 0 failed, 0 skipped**.
- Devtool structured result: **0 warnings, 0 errors**.
- Pytest emitted one pre-existing LIFE-owned `ResourceWarning` from `devtool/hooks/engine.py:523`.

The artifact uses parent-run structured `validation.tests`; it does not add a plain `validate.sh` and does not recursively invoke Devtool from an artifact hook.

Clean-base artifact replay with the post-ENV-G1 baseline Devtool also passed **195/195** through all apply/test/validation/finalize/commit/reconcile/verification stages with **0 Devtool warnings and 0 errors**.

## Next

Next overlay: **EXEC-X02 — full-plan 15/63; gate-window 2/3; EXEC 2/7**. It owns node-scoped fingerprints, input/dependency/environment/output evidence, candidate qualification, materialization proof and real reusable-result decisions while preserving X01 identities.
