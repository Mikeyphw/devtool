# ACTIONS-G8 — Cross-wave reconciliation and qualification handoff

**Status: source implemented; final campaign closure NOT asserted.**

G8 adds a read-only audit report. It preserves the **88 original requirements**
with their original historical statuses and the explicit paid private GitHub Free
exclusion. Source evidence for G0–G7 is pinned to the **post-G7 source snapshot**
using SHA-256; these file pins prove only the installed source matches the
recorded implementation baseline. They do not establish that a GitHub request
completed, Android code ran on device, a test suite passed, or an APK is an
attested production release.

## Read-only commands

```sh
devtool -r "$HOME/Code/devtool" actions qualification report --json
devtool -r "$HOME/Code/devtool" actions qualification check --json
# Returns exit 2 while required live / historical gates remain open:
devtool -r "$HOME/Code/devtool" actions qualification check --require-complete --json
```

`report` is a source and obligation snapshot; `check` additionally refuses a
source mismatch. `--require-complete` explicitly fails if mandatory final
acceptance is not backed by evidence; it **does not** run or manufacture those
live tests. Both commands are offline and do not read your GitHub credentials,
keystore, or private signing material, nor mutate repository or registry data.

## G8 acceptance matrix and current blockers

`ACTIONS_G08_QUALIFICATION_MATRIX.json` contains all eight post-G7 source
wave anchors, nine outstanding qualification gates, and their exact historical
source-of-truth boundaries. Eight of the nine are required for a complete
campaign; the optional G7 extension live acceptance is reported but not a
required personal-project gate.

| Gate | Acceptance not established by this source overlay |
|---|---|
| G0 | Native Termux debug Gradle regression following shared build changes |
| G1 | GitHub live negative matrix (HTTP 401/403/429; 204 ambiguous dispatch; artifact expiration/corruption) — deferred by operator |
| G2 | Real restart/concurrent resume/stop/cancellation ownership and run-store correlation — deferred |
| G3 | Textual/GTK/Android HUD live cross-surface traces — deferred |
| G4 | Remote AAB workflow, bundletool import + local signature, second Android variant/project — deferred |
| G5 | Real GitHub 8-cell Python/Go/Rust/Java Linux/Windows matrix, including rerun fan-in — deferred |
| G6 | Independent local certificate/lineage negative scenarios; positive `local-evidence` already reported by user |
| G7 | Optional runner trust/API, GitLab and draft-upload live acceptance — deferred; optional |
| G8 | Cross-campaign broad regression and historic V105/R01–R36/G3 requalification — deferred |

**Historic V105 obligations are external, not deleted.** The pre-Actions V103
report listed 5,146 collected / 5,007 passed / 130 failed / 1 error / 8 skipped.
The V104 Termux full suite was interrupted at its time budget, with interim
3,167 passes, 76 failures and 1 error. The G3 ledger documented 352 bad pins
of 2,062 across 10 paths. These are historical handoff facts, not newly
reproduced failures or silently repaired evidence. The signed APK from
ACTIONS-10 passed local signature verification but remains
`source_attested=false, qualified_release=false`.

## Reproducible on-device validation plan

1. Apply G8 source-guarded overlay. The embedded validator runs the full Actions
   suites and selected cross-module producer→consumer tests on the device.
   Textual presence is mandatory on Termux. Record the actual counts and
   warnings from the Devtool transaction, not this document.
2. Complete G1's previously deferred GitHub negative matrix with observed live
   safe failure cases. Do not deliberately retry an uncertain POST.
3. Run G2 restart/stop and G3 actual mobile/GTK acceptance, with observed run
   IDs and exact outcome. Keep skips and unsupported platforms explicit.
4. Push and exercise G4 AAB and G5 complete hosted matrix; collect source-, run-,
   attempt-, digest- and artifact-bound receipts and inspect failures, not only
   positive responses.
5. Re-run the native Termux debug Gradle baseline and cross-campaign broad
   regression; reconcile each original V105 R01–R36 failure and historical G3
   reference with *new* evidence instead of overwriting the old ledger.
6. Check G6 negative evidence and evaluate optional G7 services only if in scope.
   Keep release qualification false unless every *separate* protected release
   authority genuinely reports success; paid GitHub private protection is out
   of scope for this personal project.

### Source-only regression boundary

The G8 overlay itself runs `tests/test_actions*.py`, selected execution/registry
consumer suites, and its own negative ledger tests. Missing Textual in a Linux
CI container may generate expected skips; the Termux installer fails if Textual
is unavailable. This is a targeted source qualification **not** a complete
native V105 5,000+ test suite, a device acceptance suite, or a final release seal.

## Hard guarantees

- Do not mutate `ACTIONS_BASELINE_OBLIGATIONS.json`, V105 history, or
  `ACTIONS_G00_G01_EVIDENCE_INDEX.json` to make an old gate appear passed.
- No automatic GitHub dispatch, credential use, or unsafe cancellation from G8.
- A source SHA pin does not verify behavior; a local signed APK does not establish
  GitHub source attestation or a qualified release.
- `campaign_closed=false` remains authoritative until an independently reviewed
  evidence-reconciliation process proves every required live/historical gate.

## G8 authoring-environment note (not a gate override)

A separate exploratory `test_artifact_v2.py` run from the modified G8 authoring
checkout had **two nested-apply failures** with the diagnostic *Primary checkout
worktree changed while the isolated transaction was running; refusing
integration*. The other 65 tests in that exploratory subset passed. This
source tree is intentionally modified while constructing G8; do not claim
those two were reproduced in a clean installed checkout, nor claim they passed.
Rerun them as an isolated clean-checkout follow-up in the broad G8 acceptance
matrix. A separate non-nested producer→consumer set passed 43 tests.
