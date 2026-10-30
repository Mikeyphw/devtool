# FC08 HOST-R1R26 — Real definitive API rejection and bounded retry

## Merge-window decision and source

Source pinned to `Mikeyphw/devtool` commit `7db44b65986359d2dd6c334e1b79bf7018c5c0b6`, Git tree `064851e9651b8ac1593c65e0cd9f1e98d3a585d1` (R1R25). Two adjacent open dispatch scopes were reviewed together: (1) proof of **definitive GitHub API rejection followed by an explicitly approved retry**; (2) safety against retries after **ambiguous 5xx/timeout/accepted-uncorrelated**. These share the same journal and HTTP error classifier, so one bounded implementation covers them while keeping their qualification levels separate. No other workflows gain write permission.

R1R25 hosted evidence on the source commit:

- FC08 real-host [41131733123](https://github.com/Mikeyphw/devtool/actions/runs/41131733123) PASS (five jobs); full G1 [41131737862](https://github.com/Mikeyphw/devtool/actions/runs/41131737862) PASS (24/24 shards, 606/606 modules, **6,935 tests**, zero failures/errors/skips). These are observations of the **R1R25** SHA and are not a qualification of R1R26.
- Real opt-in dispatch controller [41132526228](https://github.com/Mikeyphw/devtool/actions/runs/41132526228) PASS; controlled success [41132554289](https://github.com/Mikeyphw/devtool/actions/runs/41132554289) PASS; deliberately failing probe [41132578271](https://github.com/Mikeyphw/devtool/actions/runs/41132578271) failed with deliberate exit code 37.
- Controller artifact `13013427574` SHA-256 `1109bc5ef928313ffe1b7d1723a5314e02569c1242271629aa082b60baef26cf`, downloaded and read: first dispatch POST count 1 for each scenario, repeated same request without another POST, exact run IDs and source SHA, `api_failure_retry_proven=false`, `release_qualified=false`. This proves successful/non-successful completed workflow runs with deduplication, **not** a retry of a rejected API request.

## R1R26 implementation

- `.github/workflows/devtool-fc08-dispatch-rejection-retry.yml`: a *new, isolated manual-only* workflow requiring a boolean opt-in. `actions: write` is limited to its one job; existing FC08 main workload remains `actions: none`, probe workflow remains read-only. Uploads sanitized JSON as `if: always()` and fails if absent. No Gradle task, signing, release, or consumer action.
- `tools/fc08_dispatch_rejection_retry.py`: source-verified hosted main workflow uses the reviewed `actions_dispatch.dispatch_workflow` and filesystem-locked journal. First POST has a deliberately **nonexistent Git ref** injected **only after** reviewed source preflight, leaving the intended journal input digest unchanged. The code only treats the **actual HTTP 422 branch** of `GitHubActionsClient.dispatch` as definitive rejection. A 403, 5xx, timeout, malformed 200/204, unknown exception, or an unexpectedly accepted missing ref cannot qualify and must not be turned into a safe retry.
- A definitively rejected request must be durably journaled as `rejected-by-api`, with zero correlated runs; only then may Devtool issue one valid request using the same source, inputs, approved workflow and existing journal. That valid POST must yield a correlated run ID and a completed successful remote run matching source SHA/ref/workflow/attempt. An immediate repeated request must reuse the same request ID and run ID, with **zero new POSTs**.
- Test module `tests/test_fc08_r1r26_api_rejection_retry_20261030.py` directly exercises Devtool's actual client HTTP response parser and actual journal dispatcher with a **fake local transport**, plus wrong-host/source, symlink/nonempty state, Hermes import collision, workflow permission scope and redacted error receipts. Controlled 502/504/timeouts leave a `dispatch-outcome-unknown` journal, and repeated request reuses its unresolved journal with **no POST**. No network is used by tests.

## Promise-by-promise status

| Promise | Evidence | Status |
|---|---|---|
| Exact-source transactional safety | Git SHA/tree preflight, per-file original hashes, clean worktree, rollback-on-validation | Implemented; exact-base installer gate |
| Preserve read-only existing workloads | New manual workflow only, original permissions unchanged | Implemented; source review |
| Real API 422 definitive rejection | Invalid ref, exact GitHub 422 classification, rejected journal receipt | Implemented; hosted run **OPEN** |
| Safe one-shot retry after proven rejection | One valid POST; source/correlation/run/attempt verified | Implemented; hosted run **OPEN** |
| Subsequent no-POST journal reuse | Third call uses same request ID/run ID, no POST | Implemented; hosted run **OPEN** |
| Unknown 5xx/timeout/204 never blind redispatch | Offline local HTTP 502/504/URLError simulation and journal safety; previous R1R25 204 guard | Offline safety PASS; **real 5xx/timeout qualification OPEN** |
| Definite rejection does not authorize retry for 403 or unknown 5xx | Explicit exact HTTP 422 classification only | Offline safety PASS; hosted 422 OPEN |
| Source/output redaction | Error category only, no raw response/header/token | Local contract PASS |
| Native Termux ARM64, Android signing/hardware, BKX R3, real consumer adoption | Unchanged | OPEN |
| FC09/release qualification | No automation or implied authorization | OPEN |

## Accept and run

Source-only testing: `python3 -m pytest -q tests/test_fc08_r1r26_api_rejection_retry_20261030.py tests/test_fc08_r1r25_real_dispatch_cycle_20261030.py`, followed by whole `tests/test_fc08_*.py`. New exact-source FC08 and 24-shard G1 GitHub workflows must run on the R1R26 commit. **Separately** and only if the operator explicitly authorizes one real rejected POST and one valid probe, invoke:

`gh workflow run devtool-fc08-dispatch-rejection-retry.yml --ref main -f authorize_real_dispatch=true`

Review controller run, artifact, and remote valid probe; do not claim HTTP 422 verified just because the normal FC08/G1 workflows are green. Even a successful hosted 422 retry does **not** demonstrate live 5xx/timeout recovery: the safe behavior on uncertain outcomes is never to retry blindly. `fc08_overall_qualified=false` and `release_qualified=false` remain.
