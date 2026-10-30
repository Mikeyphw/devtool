# FC08 HOST-R1R25 — bounded, opt-in real dispatch-cycle evidence

## Scope and source authority

The only accepted base is `Mikeyphw/devtool` HEAD `191d6ee8c96accef4358db096b82b1db6a7f7ce5`, Git tree `165949ea399864ccff6e293829ff2a5caa0ea994` (R1R24). R1R25 is a **new** source change; the old green host/G1 results below **do not** certify this source.

At that exact R1R24 source:

- GitHub FC08 [run 41122945028](https://github.com/Mikeyphw/devtool/actions/runs/41122945028) attempt 1: DAG, Linux, Windows, macOS, and fail-closed summary all `success`, and exactly five workflow artifacts listed. Linux receipt ZIP `13009022987` and consolidated summary ZIP `13009336928` downloaded and checked against published artifact SHA-256. Both bind to SHA/tree/attempt; Linux has 15 checks with real ELF malformed rejection and Cargo frozen refusal. Other 3 raw lane archives are inventoried from the GitHub Actions API, but not newly independently inspected in this audit. Do not misrepresent five jobs green as five raw ZIPs independently audited.
- GitHub G1 [run 41122952245](https://github.com/Mikeyphw/devtool/actions/runs/41122952245): all 24 shards and summary PASS, 605/605 modules, 6,915 tests, zero failures/errors/skips. Full shard archive re-audit is **open**.
- Both workflows originate from the same source commit. `fc08_overall_qualified=false`, `release_qualified=false`; signing, device and consumer gates remain separate.

## New implementation and explicit approval

**Do not grant Actions write to the established four-lane workload workflow.** R1R25 adds two isolated workflows and a Python entry point:

- `.github/workflows/devtool-fc08-dispatch-cycle.yml` is **manual only**, requires its `authorize_real_dispatch` boolean input to be explicitly true, and limits `actions: write` to a single bounded job. No release artifact or consumer automation is dispatched.
- `.github/workflows/devtool-fc08-dispatch-probe.yml` is **manual only**, with **read-only** permissions, one Linux job, zero publish/install/release actions, a source-bound correlation and deliberately deterministic outcomes: scenario `success` exits zero; scenario `failure` exits 37. The failure is intentional evidence; it is not a Devtool software error.
- `tools/fc08_dispatch_cycle.py` uses Devtool's actual `dispatch_workflow(... resume_if_existing=True, expected_source_sha=...)` and a private temporary durable receipt journal. It validates hosted identity, exact `main` SHA and clean checkout; makes exactly one POST for each of two distinct inputs, repeats the same request through the journal with **zero new POSTs**, then verifies **correlated** remote run ID, workflow ID, source SHA, branch, event, first attempt and requested final conclusion. Timeouts and unknown results **fail closed**.
- HTTP 204 uncorrelated POST results are never assigned a guessed run ID and are never retried. Remote run completion is bounded to 90 polls x 4 seconds maximum (roughly six minutes), and receipts never store the token, payload contents or signed URLs.
- A success receipt explicitly retains `fc08_overall_qualified=false` / `release_qualified=false`, `api_failure_retry_proven=false`, and other false device/consumer gates. A failed job writes a bounded **unqualified** diagnostic receipt (category only) and exits nonzero; it cannot declare a missing probe successful.

## Proof boundaries

| Promise | Evidence | Status |
|---|---|---|
| Preserve existing four-lane read-only workflow and source | No modification to existing FC08 workflows; source-pinned overlay | Implemented / source audit |
| Manually authorized dedicated GitHub write scope | `workflow_dispatch` boolean + job-only `actions:write` | Implemented / hosted execution **OPEN** |
| Real successful and intentionally failed GitHub workflow runs | Two actual probe dispatches and remote completed run identity verification | Implemented / hosted execution **OPEN** |
| Duplicate request safely reused without second POST | Real Devtool dispatch journal plus explicit dispatch call count and request ID equality | Implemented / hosted execution **OPEN** |
| Real HTTP permission denial | Prior R1R21–R1R24 DAG evidence from read-only token shows 403 | Already hosted PASS; never conflated with write-scope result |
| Transport failure, API 5xx, or uncertain 204 retry lifecycle | No automatic retry and no guessed run association; no additional live retry | Safety implemented; **failure-retry proof OPEN** |
| No recursive workflow fanout | Probe never dispatches anything; both workflows manual only | Implemented; hosted receipt **OPEN** |
| Adversarial offline contract suite | Tests of identity, wrong conclusion, 204, journal reuse, hard refusal and workflow scope | **PASS locally (19 focused / 182 FC08)** |
| New exact-source FC08 and G1 | Run both on R1R25 SHA after application | **OPEN** |
| Termux ARM64, signer/device, BKX R3, consumers, FC09, release | None of these changed | **OPEN** |

## Safe operator sequence

Apply the source-pinned R1R25 ZIP using `--keep-artifact` from `/sdcard/Download`, verify the resulting commit is integrated on `main`, push, and run FC08 plus full G1 on the new commit. Then, **only if real dispatch proof is desired**, manually invoke `devtool-fc08-dispatch-cycle.yml` with `authorize_real_dispatch=true`. This deliberately starts two short GitHub Actions runs (one expected success, one expected failure), whose artifacts and linked source must be reviewed before closing live-dispatch requirements. A normal all-green repository CI is **not** evidence of those two remote runs. No release/consumer approval is implied.

## Operational debt kept open

- `artifact_cleanup_failure` on Termux external `/sdcard/Download` after already committed apply (R1R23), not diagnosed to root cause; use `--keep-artifact`. Do not delete-by-force or blindly reapply an already-committed overlay.
- Five Python 3.14 HTTPError `ResourceWarning` test-fixture warnings. These did not block FC08, and remain independent test hygiene.
- R1R24 24-shard raw JUnit artifact independent review has not been repeated.

## Termux isolated-worktree import repair (R1R25 replacement package)

The initial R1R25 overlay run `122b9b64b2d44c28ae6f9f41f96835c7` failed at test collection, **after** passing the exact R1R24 base-tree and preflight checks. Termux imports the third-party Hermes Agent `tools` package instead of the repository's `tools` path. This means the installed tests were not executable; no live dispatch was attempted. An isolated worktree can contain the reviewed source and still have different global import ordering.

The replacement R1R25 package (not an incremental overlay) binds the test import to `Path(__file__).resolve().parents[1] / 'tools/fc08_dispatch_cycle.py'` via an explicit importlib file spec. A separate test verifies the loaded module's concrete source path. The `fc08_dispatch_cycle.py` runtime, its two workflows and all GitHub token permissions are identical to the original R1R25 package.

Promise accounting: exact-base preflight unchanged; package-name collision fixed in source; local tests re-run; transactional installation and hosted FC08/G1/opt-in dispatch evidence remain independently gated. Preserve `release_qualified=false` until real device/consumer criteria are met.
