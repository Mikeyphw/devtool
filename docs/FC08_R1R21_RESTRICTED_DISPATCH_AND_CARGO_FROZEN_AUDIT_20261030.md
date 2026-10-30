# FC08 HOST-R1R21 — restricted live dispatch refusal and Cargo frozen-lock negative

**Base:** GitHub `Mikeyphw/devtool` `main` commit `be46c12b3dcd57568fda3ef2300cdc80d2956113`, tree `100fc5147433f7840df7c3d699de0ee28f429d28`.

**Observed prior host evidence:** [FC08 `41084486679`](https://github.com/Mikeyphw/devtool/actions/runs/41084486679) and [G1 `41084493660`](https://github.com/Mikeyphw/devtool/actions/runs/41084493660) both completed successfully on **that exact source**. FC08: 5/5 jobs, Linux 13 checks, DAG 4, Windows 8, macOS 3, fail-closed summary. G1: 24/24 shards, 6,867 tests, 0 failures/errors/skips and final 25th summary job. Earlier R1R20 ledger's `hosted OPEN` is a historical statement; current receipt evidence supersedes it only for source `be46c12`.

## Why these adjacent gates were merged

Both additions use the existing reviewed `tools/fc08_host_workloads.py` producer, source-bound receipts, and the existing four-lane workflow. A new token privilege, new release authority, unrestricted dispatch controller, or duplicate workflow would add unnecessary risk. This source-only patch does not split repos or change release policy.

## Promise-by-promise audit

| Promise | Source evidence | Status |
| --- | --- | --- |
| Observe a genuine restricted GitHub API dispatch rejection | In the actual DAG job, a single GitHub `GITHUB_TOKEN` with `contents: read` and explicit `actions: none` accesses the reviewed *separate* FC08 observation workflow's source on the same commit, then attempts exactly one `workflow_dispatch`. Only real authenticated HTTP **403** is accepted. | **Implemented, unit-tested; hosted HTTP status pending** |
| Do not create an uncontrolled recursive workflow | Denial POST targets `devtool-fc08-host-evidence.yml`, not the running `devtool-fc08-host-workloads.yml`; one attempt, no retries. Any accidental 200/204 response **fails the qualification** and must be treated as an unexpected side effect. No user PAT or actions-write token. | **Implemented; hosted pending** |
| Do not confuse unreadable/missing workflow with permissions denial | Require authenticated 200 source file GET at the exact `GITHUB_SHA`; reject missing secret, wrong run/ref/source, 401, 404, 429, 5xx, timeouts and other unexpected statuses. No raw token/header/response body in receipts or failures. | **Implemented, unit-tested** |
| Truthful dispatch scope | A 403 proves a *negative permission boundary* for this read-only token only, **not** real successful/failed/repeated dispatch lifecycle, deduplication, independent permission audit, or release authority. | **Limited by design; separate dispatch lifecycle gate OPEN** |
| Real Cargo frozen offline stale-lock refusal | Reuse real tracked Rust fixture and its genuine `Cargo.lock`. Check `cargo metadata --no-deps --format-version 1 --frozen` succeeds initially; modify only throwaway package version; require nonzero; confirm lock bytes unchanged; restore version and require success. | **Implemented and unit-tested; live cargo-host pending** |
| Retain prior native/hosted checks | Existing Linux 13 / DAG 4 checks gain one each, becoming Linux **14**, DAG **5**. Windows 8 / macOS 3 unchanged; Linux Go, Maven, CMake/Meson, locked Python managers, uv and ELF negative retained. | **Source contract + 129 focused FC08 tests pass** |
| Keep four-lane fail-closed receipt | Exact source SHA/tree/run/attempt, checks digest, non-release status, and missing-check refusal retained. Consolidated success still `HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW`. | **Source unchanged; hosted pending** |
| Fresh full G1 (24 shards) at post-R1R21 source | Existing G1 passed 6,867 tests at R1R20 commit only; **never** transfer that PASS to a different SHA. | **PENDING** |
| Device/consumer/signer and release | Native Termux ARM64, Android signing/device, BKX R3/real consumers, FC09 and formal release are not changed by R1R21. | **OPEN** |

## Preflight and follow-up

- This overlay is designed for **exact `be46c12` commit and tree**, and a clean checkout. It updates only workload source, the existing hosted workflow, new regression test, this audit and cumulative ledger.
- No credentials or device mutations in offline tests. Local FC08 suite: **129 PASS** with command-scoped Git ownership exception for the uploaded older archive. Real Cargo is unavailable locally, so the Cargo subprocess negative is **mock-protocol tested only** until GitHub Linux runs it; GitHub HTTP is mocked in offline tests by design.
- The GitHub API documentation requires **Actions write** for workflow dispatch. GitHub Actions workflow syntax specifies that permissions not listed are `none`; R1R21 explicitly sets `actions: none`. A 403 is therefore expected, but **must** be observed rather than fabricated. [Workflow API](https://docs.github.com/en/rest/actions/workflows), [permissions syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
- After applying: push, run `devtool-fc08-host-workloads.yml`, require DAG **5** and Linux **14** checks and the four-host summary; run `devtool-full-repository-pytest.yml`, require 24/24 shards, 0 failures/errors/skips. Archive real run/job/attempt IDs and downloaded artifact digests.
- If GitHub denies with 404 instead of 403 or the Cargo frozen lock behavior differs, record the precise failure as **OPEN** and adjust only after the real evidence is examined. **Do not weaken the gate, grant `actions: write`, or promote a simulated denial.**
