# REMED-R02B — Validation & Test Truth v2

## Scope position

- Roadmap item: **REMED-R02B**
- Gate-window position: **4/8**
- Full-plan position: **74/93**
- Applicable base: post-REM01 fixed-point hotfix and **REMED-R02A v2**
- User-provided device commits retained as source context: R01A `7f7b6fd`, R01B `b5a8cce`, REM01 hotfix `e16ef1d`; R02A v2 was applied before this overlay.

This v2 artifact supersedes both:

- `remed_r02b_validation_test_truth_v1.zip`
- `remed_r02b_validation_test_truth_hotfix_v1.zip`

It is intentionally source-bound directly to the post-R02A-v2 tree so it can be applied when v1 has **not** been applied.

## Canonical scope reviewed

R02B is the validation/test half of REMED-R02. It inherits the corrected R02A audit result: R02A delivered local scheduler/RUN truth for `EXEC-A04`, `EXEC-A05`, and `EXEC-A07`, with `EXEC-A06`/`EXEC-A13` partial and `EXEC-A01`/`EXEC-A02`/`EXEC-A08` not claimed. R02B therefore concentrates on validation-state projection, canonical test receipts, failed-history selection, direct/library receipt binding, and Android count truth.

Maximum current promise ranges retained for this remediation window:

- `CORE-P084`
- `EXEC-P087`
- `ENV-P131`
- `ANDROID-P111`
- `LIFE-P127`

## Production changes

### Validation is explicitly projection state

`ValidationSession.as_dict()`, validation plan payloads, and validation event log entries now carry:

- `authority = "projection"`
- `canonical_authority = "EXEC/RUN validation-reference-state"`

This prevents UI/API consumers from mistaking repo-local `state.json`, `validation-plan.json`, or `events.jsonl` for final execution authority. The canonical reference projection remains the private state written by `validation_authority.write_private_state()`.

`record_phase_result()` no longer persists process argv in `AttemptRecord.argv`. A validation session can still preserve phase status/verdict/control UX state, but it no longer stores a replayable process command as independent execution authority.

### Test run history now has canonical RUN-V3 receipts

`test_runstore.persist_test_run()` now writes an immutable canonical receipt under:

```text
state_dir()/run-v3/test-run-receipts/<repo-key>/<run-id>.json
```

The old `state_dir()/test-runs/<run-id>.json` path remains, but is marked as a projection and contains a pointer to the canonical receipt:

- `authority = "projection"`
- `canonical_authority = "RUN-V3 TestRunReceipt"`
- `canonical_receipt_path`
- `canonical_receipt_id`
- `canonical_receipt_sha256`

`run_specs(...)` now passes its authoritative scheduler/RUN `event_run_id` into `persist_test_run()`, so direct/library ingress cannot persist a timestamp-fallback receipt detached from the actual execution identity.

### Failed-test history derives from canonical receipts

`load_failed_test_ids()` now prefers the latest valid canonical TestRunReceipt. `.devtool/test-history.json` remains only as a compatibility projection for pre-R02B history and manual workflows without a receipt.

A latest valid canonical receipt with zero failures is authoritative and suppresses stale projection failures. Canonical receipt bytes are SHA-checked before the receipt can drive failed-test selection; tampered receipts are rejected as selection authority.

`save_failed_test_ids()` marks the repo-local file as a projection rather than authority.

### Legacy selection lowering carries immutable selection identity

`apply_test_selection()` now attaches `canonical_test_selection` metadata whenever normal runner selection is lowered. This binds legacy CLI/config selection syntax to a stable immutable selection projection with a `selection_id`, resolved check IDs, target, variant, and project root.

The framework-specific argv lowering remains compatibility lowering; it is no longer the only persisted selection identity.

### Android instrumentation count truth

Android required-zero-test and setup/readiness failures no longer fabricate failed testcases. They fail through `status`, `returncode`, and `failure_kind`, while preserving truthful testcase counts:

- `executed = 0`
- `failed = 0`
- `total = 0` where no testcase ran

This directly addresses the count-truth portion of `ANDROID-A05` and `ANDROID-A06`.

## Delivered and deferred findings

Fully delivered by this overlay:

- `TEST-A02` — duplicate global test-run store reduced to projection with canonical receipt pointer.
- `TEST-A09` — failed-test selection derives from verified RUN/TestRunReceipt when available; repo-local file is compatibility projection and cannot override a latest clean canonical receipt.
- `TEST-A13` — production writes canonical durable TestRunReceipt binding repository, run identity, operations, testcase results, selection refs, environment refs, timing, logs/reports, and digest; direct/library `run_specs(...)` binds the receipt to the scheduler `event_run_id`.
- `ANDROID-A05` — setup/install/runner readiness failures no longer fabricate failed testcase counts.
- `ANDROID-A06` — required zero-test behavior preserves executed=0 and failed=0 while failing through execution/verdict dimensions.

Partially adopted by this overlay:

- `EXEC-A01` / `EXEC-A02` — validation repo-local state is now marked projection and process argv is removed from attempts, but the existing validation resume UX still persists phase/progress/session projection state for compatibility. Final closure requires the later G3R audit to verify every validation consumer treats the private reference state and RUN records as authority.
- `TEST-A01` — normal selection lowering now carries an immutable selection identity, but not every non-Python/framework-specific frontend is fully proven through one canonical selection compiler.
- `TEST-A03` — canonical receipt preservation is added, but some runner-local latest report files can still be produced as convenience projections.
- `TEST-A05` / `TEST-A06` / `TEST-A07` — not fully implemented here; profiling/resource/shard timing convergence remains partially owned by previous TEST-X03/X04 work and must be requalified later.
- `TEST-A10` — R02A provided RER scheduler adoption for local specs; full TEST scheduling reuse across all frontends remains partial.
- `TEST-A14` / `TEST-A15` — existing guards/retry semantics are retained but not fully re-authored in this overlay.
- `ANDROID-A03` / `ANDROID-A04` / `ANDROID-A07` / `ANDROID-A08` / `ANDROID-A10` / `ANDROID-A11` — Android count truth is improved, but full shared TestResult/TestRunReceipt emission, active-test crash context, detailed typed taxonomy, and run-scoped Android evidence still require requalification and/or later implementation.

Deferred/not claimed:

- `EXEC-A08` universal fresh/rerun/rerun-from controls.
- Remote/provider parity and cleanup/retention items owned by R03.
- G3/G3R evidence-resolution/final-seal correctness items owned by R04 and later gates.

## Production ingress changed

- `run_specs()` test-result persistence path through `test_runstore.persist_test_run()`.
- Normal runner test selection lowering through `test_selection.apply_test_selection()`.
- Failed-test selection through `test_selection.load_failed_test_ids()` and canonical receipt verification in `test_runstore`.
- Validation session persistence through `validation_sessions.save_session()`, `append_event()`, and `record_phase_result()`.
- Android instrumentation required failure handling through `android.instrumentation`.
- Devtool wrapper target `./devtoolw remed-r02b` as native validation entry point.

## Targeted validation

Executed locally on the reconstructed post-R02A-v2 source tree with v2 applied directly:

```text
94 passed, 0 failed
```

The targeted suite included:

- `tests/test_remed_r02b_validation_test_truth.py`
- `tests/test_validation_sessions.py`
- `tests/test_exec_g2_validation_proof_gate.py`
- `tests/test_test_selection.py`
- `tests/test_test_runstore.py`
- `tests/test_android_instrumentation.py`

Additional adversarial evidence folded into v2:

- `tests/test_remed_r02b_validation_test_truth.py::test_direct_run_specs_persists_test_receipt_with_event_run_id`
- `tests/test_remed_r02b_validation_test_truth.py::test_clean_canonical_receipt_with_zero_failures_suppresses_stale_projection`
- `tests/test_remed_r02b_validation_test_truth.py::test_tampered_canonical_receipt_is_not_failed_selection_authority`

Native artifact validation is declared as:

```bash
./devtoolw remed-r02b
```

## Remaining before next major phase

- Next overlay: **REMED-R03A — Provider & Lifecycle Authority**
- Remaining remediation implementation overlays before G3R: **4**
- Next major phase after remediation overlays: **CORE-G3R at 79/93**
