# REMED-R04A — Presentation & Evidence Truth

Position: **77/93**  
Gate-window overlay: **7/8**  
Base content: R03B applied and Termux-qualified by user run `ca21a42ef8c14937afbb35b1c5b43535`, commit `2fa53fb`.

## Scope

R04A closes the first presentation/evidence truth slice after R03B.  It does not start R04B and does not claim G3R real-substrate qualification.  The implementation targets production paths where presentation or evidence previously accepted model-only truth:

1. Evidence ledgers that accepted any non-empty evidence string.
2. Textual control-center action specs that duplicated canonical UI action semantics.
3. Android HUD notifications that were public by default instead of private by default.

## Production ingress changed

### Evidence truth

- `EvidenceResolver` now owns retained symbolic evidence aliases used by historical TEST-G3 and DIST-G3 ledgers.
- The resolver expands list-valued `evidence` fields into concrete evidence rows, including optional per-reference expected hashes.
- `DIST-G3` source disposition audit now calls `EvidenceResolver.require_all(...)` for every promise/regression evidence reference.
- `TEST-G3` source and merged disposition audit now calls `EvidenceResolver.require_all(...)` for every promise/regression evidence reference.
- Unknown symbolic strings fail resolution instead of counting as evidence just because they are non-empty.

### Presentation/action truth

- Textual control-center action specs are now canonical UI action registry views.
- Legacy Textual action IDs such as `cancel`, `retry`, `overlay.apply`, and `worker.recover` retain their UI/API compatibility IDs but carry `canonical_id`, canonical label, canonical intent, and canonical destructive semantics from `devtool.ui_core.actions`.
- New canonical action registry entries were added for overlay recovery/rollback/apply, worker recovery, and validation pause/stop.

### Notification privacy truth

- Android live-HUD notifications now use `Notification.VISIBILITY_PRIVATE` for both run and summary notifications.
- `Notification.VISIBILITY_PUBLIC` is no longer present in `NotificationController.java`.

## Duplicate authorities removed

- `TEST-G3` and `DIST-G3` no longer use non-empty evidence strings as the local evidence authority.
- Textual no longer owns independent destructive/action semantics for its wired controls; it projects canonical UI actions.
- Android notification visibility no longer has a presentation-surface override that weakens the canonical private-by-default policy.

## Targeted validation

Run from the R04A construction workspace:

```text
python -m compileall -q devtool tests/test_remed_r04a_presentation_evidence_truth.py
git diff --check
PYTHONPATH=. python -m pytest -q \
  tests/test_remed_r04a_presentation_evidence_truth.py \
  tests/test_ui03_ui04_preferences_tokens_copy.py \
  tests/test_ui07_ui08_confirmation_notification_terminal.py \
  tests/test_pres_r03_textual_parity_qualification.py \
  tests/test_pres_g2_interaction_reliability_remediation.py \
  tests/test_dist_g3_final_seal.py \
  tests/test_test_g3_final_seal.py
```

Result:

```text
70 passed, 1 skipped
```

The skipped test is Textual-host optional on the construction host.

## Findings delivered

Closed at code/proof level in this overlay:

- `DIST-A09` — DIST source disposition audit now resolves evidence references through `EvidenceResolver`; fake/nonexistent symbolic evidence fails.
- `TEST-A17` — TEST G3 source/merged disposition audit now resolves evidence references through `EvidenceResolver`; fake/nonexistent symbolic evidence fails.
- `PRES-A03` — Textual action specs are canonical registry views rather than an independent action semantic authority.
- `PRES-A06` — Android notification visibility is private by default.
- `PRES-A08` partial — Textual and shared UI controls now expose canonical action IDs/destructive semantics; Android semantic notification action routing still needs the later Android app/real-device proof slice.

## Remaining gaps

R04A is a code-level implementation overlay, not the final remediation fixed point.

Still remaining before CORE-G3R can begin:

- `REMED-R04B 78/93` must perform the whole-campaign fixed point and cannot permit CORE-G3R while any finding, promise, historical obligation, regression contract, evidence reference, or duplicate production authority remains unowned.
- G3R real-substrate proof still remains for:
  - `ENV-A06`
  - `ANDROID-A16`
  - `DIST-A01`
  - `DIST-A12`
  - `DIST-A13`
  - `TEST-A11`
- Campaign-level reconciliation items still live before final sealing:
  - `CORE-A07`
  - `ANDROID-A09`
  - `TEST-A08`
  - REM01 `CORE-A02` versus `CORE-A08` final evidence mapping reconciliation
- Presentation/evidence surfaces intentionally left to R04B/G3R fixed point:
  - full PRES-G3R cross-surface diagnostic/action/notification/web/bridge/stream invariant matrix;
  - current-HEAD attestation regeneration across all G3R scopes;
  - every retained source row mapped to exact current evidence with stale-hash failure.

## Position after this overlay

- Completed overlay: **REMED-R04A — 77/93; gate-window 7/8**
- Next overlay: **REMED-R04B — 78/93; gate-window 8/8**
- Next major phase after remediation: **CORE-G3R 79/93**

## TUI Snapshot Compatibility Addendum

This v2 overlay is rebased against the real R03B-qualified snapshot that includes `fix(tui): retain completed results and add status colors` (`1ce6c3dc`). R04A does not modify the TUI result-retention files from that commit (`execution_presentation.py`, `textual_apply_host.py`, `textual_control_center_runtime.py`, `textual_execution_host.py`, or `tests/test_tui_result_retention.py`), but the validator now runs `tests/test_tui_result_retention.py` to preserve the result-retention/status-color behavior while R04A changes canonical Textual action/projection surfaces.

Impact assessment:

- Previous R03B overlay: no touched-file overlap with the TUI commit; the successful Termux run on commit `2fa53fb` proves it preserved that commit.
- R04A v1: touched-file contents were compatible, but its `base_tree` was generated from a sandbox baseline that lacked the TUI commit. v2 corrects the base tree and validation surface.
- Future R04B/G3R work: must treat TUI result retention/status-color semantics as live presentation obligations, not historical UI polish.
