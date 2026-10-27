# PRES/EXEC runtime report-to-source proof migration

**Base source:** `5cfcd3cce55cf2e5a1a3663fcbb12d0a80b9bd82` (2026-10-15).  This change is a new source revision; pre-change hosted/native evidence does **not** qualify the revised revision.

## Authority and proof schema

`devtool/source_bound_proofs.py` issues version-1 executable receipts for historical PRES-X01–X08, PRES-G1, PRES-G2 and EXEC-P082. A receipt has `schema_version`, `id`, `status`, `source_paths`, `source_digest`, and `contract`. `source_digest` covers the path names and bytes of the *imported* implementation files and of the verifier code. This is provenance, not a SHA allowlist. The code must also execute the actual behavior; a source digest or a file being present is never accepted as a pass.

The caller verifies that imported module paths resolve **inside exactly the requested repository root**. Missing packages, missing/substituted modules, failing behavior, absent required receipts, failed receipts, or violated canonical validation semantics fail closed. No arbitrary caller-supplied proof list can cause `build_*gate` to return a positive result.

PRES-G1 keeps its existing renderer-only/default/fallback/parity semantics, but replaces the old `pres-report-window-complete` filename gate with `pres-source-closure-complete`, backed by the seven current-source probes. PRES-G2 retains its ten interaction/reliability checks and adds source-bound PRES-X01–X08, G1 and G2 receipts. Neither runtime gate enumerates or reads `PRES*_REPORT.md`.

EXEC-G3 keeps the existing EXEC-X04 promise ledger and exported gate-obligation checks, and replaces the `EXEC_X01...REPORT.md` substring search for `EXEC-P082` with a **bounded non-Gradle Python execution** plus canonical required/failed/skipped/missing and applicability verdict checks. This is an EXEC-X01 semantic boundary check, **not** a claim that Gradle, instrumentation, signing or other real substrates were exercised.

`required_pres_gate_reports()` and `required_pres_g2_reports()` are retained callable as compatibility stubs and now return `()`. The `present_reports` keyword is tolerated but ignored as authority. Machine reports retain the older `missing_reports` field (always empty after migration) and add `source_proofs`, `missing_proofs`, `source_proof_schema_version` and `source_proof_digest`. Existing `passed`, `owns_truth` and `machine_output_changed` safety fields remain.

## Fail-closed regression coverage

`tests/test_source_bound_pres_exec_migration.py` verifies full source/file digests, proves that attempts to read historical Markdown fail a negative control, rejects mismatched repository roots and missing receipts, detects false-positive presentation status and semantic validators treating generic `SKIPPED` as successful, and checks the complete EXEC-P082 receipt. The existing PRES-G1/G2 tests have been migrated to assert source-bound checks instead of historical file existence.

The historical reports, ledgers, public compatibility shims and archival receipts are **intentionally retained** until downstream and off-repository consumer migration is proven. Phase C presence contracts are preserved for this batch. No FS03/target-seal or native-device/hosted-CI qualification claim is advanced by this change.

## Verification after application

```bash
python3 -m pytest -q tests/test_source_bound_pres_exec_migration.py \
  tests/test_pres_g1_textual_replacement_proof_gate.py \
  tests/test_pres_g2_interaction_reliability_gate.py \
  tests/test_exec_g3_final_execution_seal.py
python3 -m devtool.exec_g3_gate
python3 -m devtool final-seal --strict
```

A code-changing commit requires a new 24-shard GitHub workflow run **and** fresh native G3R evidence for its exact Git commit/tree; formal release remains blocked by `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, and `TEST-A11`.
