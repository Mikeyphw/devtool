# REMED-R04B — Whole-Campaign Fixed Point

Position: **78/93**  
Gate-window overlay: **8/8**  
Base content: R04A presentation/evidence truth and schema-6 cleanup hotfix are applied (`c2b54b0`).

## Scope

R04B is the final implementation overlay before **CORE-G3R 79/93**.  It does not claim unavailable real-device/provider qualification.  Its job is to make the pre-G3R fixed point executable and fail-closed:

1. retained source-disposition evidence must resolve to current repository bytes or an exact retained source-row digest;
2. stale evidence hashes must fail instead of silently remaining satisfied;
3. retained G3 attestations must match their current disposition manifests;
4. presentation surfaces must have one explicit diagnostic/action/notification/bridge/stream matrix; and
5. real-substrate obligations carried to G3R must remain explicit rather than marked green.

## Production ingress changed

- Added `devtool.remed_r04b_fixed_point` as the reusable R04B audit authority.
- Added wrapper command `remed-r04b` and workflow `remed-r04b` so the fixed point is runnable through normal Devtool wrapper/workflow validation, not just through ad-hoc tests.
- Added `REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json`, a generated stale-hash manifest binding **1,354** retained source rows and **3,893** evidence references across CORE, ENV, ANDROID, LIFE, DIST, and TEST G3 source-disposition ledgers.
- Added `REMED_R04B_PRESENTATION_SURFACE_MATRIX.json`, a hash-bound cross-surface matrix for machine, terminal, TUI, desktop, Android HUD, Tasker, IPC, web, and bridge surfaces.

## Duplicate authorities removed or pinned

- Historical source rows no longer rely on non-empty evidence lists as implicit proof at the R04B fixed point.  The R04B audit recomputes and compares a manifest root over each ledger, row id, evidence reference, resolved alias, path, and SHA-256.
- Presentation evidence no longer relies on separate renderer reports alone.  R04B records one matrix that binds diagnostics, actions, notifications, bridge state, and streams to current evidence files.
- Candidate G3 attestations are not treated as fresh by status text.  R04B validates the current disposition-manifest digest fields they retain.

## Targeted validation

```text
python -m compileall -q devtool/remed_r04b_fixed_point.py tests/test_remed_r04b_fixed_point.py
git diff --check
PYTHONPATH=. python -m pytest -q --tb=short tests/test_remed_r04b_fixed_point.py tests/test_remed_r04a_presentation_evidence_truth.py tests/test_remed_r04a_validation_schema6_cleanup.py tests/test_tui_result_retention.py
```

## Findings delivered

Closed at code/proof level in this overlay:

- R04B fixed-point evidence binding: source evidence across retained G3 ledgers is current-byte/source-row hash bound.
- R04B presentation fixed point: cross-surface diagnostics/actions/notifications/bridge/stream invariants are centralized in one hash-bound matrix.
- R04B current-attestation fixed point: retained CORE/ENV/ANDROID/LIFE G3 attestations are checked against their current source disposition manifests.
- R04B G3R boundary: `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, and `TEST-A11` remain explicit real-substrate carry items, not source gaps.

## Current overlay gaps

None at source level after targeted validation.  Native Termux apply/validation remains the only qualification step for this artifact.

## Position after this overlay

- Completed overlay: **REMED-R04B — 78/93; gate-window 8/8**
- Next major phase after remediation: **CORE-G3R 79/93**
