# ACTIONS G0–G8 — delivery cross-check and operational hotfix

Audit date: 2026-10-13. Inspected the user-supplied `devtool-20261013-131349-532361.tar.gz` at Git `50570cb` (G8 rebased overlay applied). This is a **source and offline regression audit**, not a completed live-device qualification.

## What was checked

- Original G0 frozen obligation ledger: **88** requirements, preserved; one paid private GitHub Free protection rule excluded by operator decision.
- G8 source gate: all eight G0–G7 source-wave pins matched; `source_contract_consistent=true`, **8 required live/historical gates remain pending**, `campaign_closed=false`.
- Existing ACTIONS-00–10 plus G0–G8 Python test modules: **411 passed, 7 skipped (Textual unavailable)** on the supplied snapshot.
- Separate clean-checkout artifact regression `tests/test_artifact_v2.py`: **22 passed, 2 failed** before hotfix. Both reported nested Devtool apply failures but had **two different causes**.

## Promises vs implementation (no invented live signoff)

| Wave | Source-delivered scope | Evidence still missing / explicit scope limits |
|---|---|---|
| G0 | 88-item baseline, SHA-256 evidence inventory | Cross-campaign full V105 native seal; new native debug regression |
| G1 | Dispatch ambiguity view, source/attempt-aware run and artifact checks, ZIP alias rejection | Live 401/403/429, expired artifacts, ambiguous POST recovery; operator deferred |
| G2 | Four-worker bounded *remote* frontier, stop marker, fail-closed restart behavior | Real crash/restart/run-store lineage, actual scheduling/cancel traces; no remote process kill |
| G3 | Read-only Textual DAG inspector and typed GTK/HUD stop command parity | Fresh native phone/desktop runtime acceptance (headless/JVM alone not enough) |
| G4 | Unsigned AAB job + unit tests/lint receipts, bundletool-backed import, LIFE local signing | Real remote AAB and Termux bundletool + second Android variant/project |
| G5 | Reusable Python/Go/Rust/Java × Linux/Windows fixture workflow, source-bound eight-cell fan-in | Actual hosted 8-cell run/rerun; **generic arbitrary-project adapters are not part of this fixture lane** |
| G6 | Signed→unsigned registered lineage and independent LIFE certificate verification | Positive device result supplied by user; more live negative scenarios remain |
| G7 | Opt-in metrics, runner inventory profiles, read-only GitLab discovery, draft-prerelease upload | Optional service/live checks; self-hosted dispatch and GitLab execution explicitly not implemented |
| G8 | Read-only source/obligation matrix, historical V105 and required gate preservation | Eight required live/historical gates; previously unresolved legacy nested apply tests (now addressed below) |

ACTIONS-10's GitHub unsigned APK → Termux import → local LIFE signing → independent certificate check was successfully reported and device-tested by the user. `qualified_release` and `source_attested` remain false. No GitHub Pro/private ruleset features are required to complete this personal-project workflow.

## Proven hotfix items

1. **Real transaction defect:** in a clean, temporary Git repository, `git_status_identity` detected new untracked `.devtool/private/run-v3/...` and `.devtool/private/cleanup/receipts/...` files that Devtool itself wrote during an isolated apply. It falsely concluded the primary checkout was externally edited and refused final integration. G8's two previously cited nested apply failures included this cause. The runtime fix exempts *only untracked* files inside these two specific Devtool-owned receipt trees. Tracked edits and unrelated untracked files remain disqualifying; explicit regression tests cover both.
2. **Outdated test expectation, not a security bug:** `test_cli_ignore_checksum_applies_overlay_without_disabling_manifest_checks` assumed `--ignore-checksum` should allow an intentionally incorrect canonical mutation SHA-256. Devtool's canonical mutation guard rightly refused. The test now asserts failure and rollback, keeping SHA-256 checks **mandatory** for mutations; the runtime has not been weakened.

Before hotfix, the separate artifact suite reproducibly failed **2/24**. With the narrow runtime fix plus corrected legacy contract and four additional negative/positive tests, the focused suite passes **28/28**. The combined Actions + artifact suite passed **439, 7 skipped** in the audit container.

## Boundary

This hotfix is unrelated to GitHub API behavior, G4 bundle production, G5 Windows or Rust, Android HUD runtime, private signing or production release status. Those tests cannot be honestly closed offline. See `ACTIONS_REAL_WORK_DEVICE_TESTS.md` and `tools/actions_real_work_tests.py` for casual real-work acceptance and generated JSON notes.
