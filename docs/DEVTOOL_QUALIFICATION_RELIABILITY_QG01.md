# QG01 — Qualification Reliability & Hosted G3R

Status: **source implementation; native/hosted acceptance pending**. This document
is a source-bound milestone extension, not release certification.

## Baseline and historical acceptance

- Repository: `Mikeyphw/devtool`
- Preimage branch: `feature/devtool-r06-base-adoption`
- Preimage commit: `3da24f2985051954d61154c088af92a303eacbb3`
- Preimage tree: `2c1a24b559298c94fb56ec9a024ecd78b0c8e6c3`
- Existing PR: #47, same branch, never create a duplicate.
- Historical CI24: run `45101634807`, attempt 1, 24/24 shards, 9,197 tests,
  **accepted for the preimage only**. Preserve its receipt and artifacts.

## Promises

| Key | Observable end-to-end contract | Acceptance |
| --- | --- | --- |
| QG01-P01 | Existing hosted G3R workflow dispatches once for pinned source; no duplicate after uncertain POST | Exact source-bound run and dispatch journal |
| QG01-P02 | Real Android G1 Gradle gate runs on a GitHub Ubuntu runner only | Job succeeds; hosted receipt with checkout SHA/tree, run ID, attempt, real Gradle and artifact proofs |
| QG01-P03 | Verifier rejects wrong run/branch/workflow/attempt/job, missing or malicious archive and changed remote run | Hostile credential-free regressions and live source-bound confirmation |
| QG01-P04 | Termux G3R generates 10 semantic lanes and 2 local native lanes; remote Android G1 is the third native lane | Exactly 13 independently checked lanes; no local Gradle dispatch |
| QG01-P05 | CI24 reads/hashes **all 25** authenticated artifact archives (24 shards + summary), not only metadata | 25 archived bytes verified; digest, source/attempt and summary bound |
| QG01-P06 | Final seal re-observes CI24 and G3R live evidence, strictly checks FS03, preserves real-substrate debt | Automated milestone only; release remains false |
| QG01-P07 | Single upgraded `devop-devtool` CLI owns QG01 lifecycle and safe native continuation | One native ZIP overlay, source-pinned preimage, reversible apply/commit/PR/CI; no replay |

## Operator workflow

1. `devop-devtool overlay QG01 check` — read-only native `validate-overlay`
   and `overlay check`; exact preimage / separate runtime required.
2. `devop-devtool overlay QG01 run --yes` — authorized atomic native apply
   and commit, preserving existing PR #47, WIP, CI24 and session history.
3. Authorize remote push and PR update explicitly via native session resume.
4. Requalify **required CI** and run a **new CI24** on the *new* source commit.
5. `devop-devtool qualification remote-start --yes` dispatches the existing
   G3R workflow once. `remote-resume` verifies the correlated run; never start
   again after an uncertain dispatch.
6. `devop-devtool qualification native` generates G3R native Termux evidence
   only **after** the hosted Android G1 is verified. The `android_g1_gate` is
   expressly excluded from native subprocess invocation.
7. `devop-devtool qualification verify`, then `... seal` reverify G3R and the
   current-source CI24, plus strict FS03. No merge/release authorization.

## Known limitations and external gates

- Cloud build / device / UI / 13-lane native end-to-end and final-seal results
  are **NOT RUN** for this source handoff; CI24 previously passed only for
  `3da24f...`, not the future QG01 result commit.
- A GitHub workflow dispatched on this source requires the workflow to already
  exist on the repository's default branch; QG01 *extends* the existing
  `.github/workflows/devtool-g3r-current-evidence.yml` rather than inventing a
  new workflow ID.
- Existing real-substrate obligations remain open: ENV-A06, ANDROID-A16,
  DIST-A01, DIST-A12, DIST-A13 and TEST-A11. Hosted Gradle is not a physical
  Termux device or release qualification.
- No local Gradle, `./gradlew`, local native Gradle validation tasks, fabricated
  source signatures or missing acceptance waivers are authorized.
