# DT-RH01-F01 — hosted regression and CI24 observation evidence

Base: PR #50 exact head `d2818ec39823b10b0a800622f33471d941de13aa`, tree `a708d85be6275568dbfa7e68bc9fd70447d70bb6`. Original committed transaction and session are historical evidence; this follow-up uses a new native transaction on the same branch, never replays the old overlay.

- DT-20261124-35: migrate the two M4 integration fixtures to real local Git sources, least-privilege read then write scope, no write scope/intent on unapproved preview, and the real four-probe/diagnostic/confirmation sequence. Do not weaken production push authorization. Test all combinations of existing/new branch and workflow/nonworkflow changes.
- OP-20261124-09 support: native CI24 `qualify-status` exposes durable run identity and a separately identified last observation. An observer exception retains the last recorded qualification state and dispatch identity, but records a bounded failure code, not a fabricated pending/success verdict or raw provider text. A later successful observation replaces the last failure projection. Unknown/unbound failures never select a different run or authorize dispatch. The operator consumes this native evidence, not an independent CI verifier.
- The configured required check policy is distinct from all hosted workflow results. A green required-CI receipt does not excuse the Launcher v3 and GA source failures on the old head. Requalify the new head in required CI, the broader hosted regressions, and independent CI24. No local Gradle; native validation uses Python tests only.

Old DT-RH01 disposition JSON and session artifacts are retained unchanged as historical evidence. This file records source intent, not remote or release acceptance.
