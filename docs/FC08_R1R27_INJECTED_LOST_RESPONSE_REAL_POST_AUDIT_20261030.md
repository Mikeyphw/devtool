# FC08 HOST-R1R27 — real inert POST + injected response loss, durable unknown journal

## Exact base and source qualification boundaries

- **Only valid overlay base:** `Mikeyphw/devtool` commit `60ae811b7b7cc594e494664e49b5f7b06d452bc2`, Git tree `6987838cacdf320789b10fcf225059166de910c5` (R1R26). The new R1R27 source must run its own GitHub FC08, full G1 and separately authorized lost-response experiment before any hosted claim.
- R1R26 hosted FC08 [41139968324](https://github.com/Mikeyphw/devtool/actions/runs/41139968324): five jobs PASS, Linux 15, DAG 5, Windows 8, macOS 3, final fail-closed summary PASS.
- Same-source G1 [41139973357](https://github.com/Mikeyphw/devtool/actions/runs/41139973357): 24/24 shards, 607/607 modules, 6,954 tests, zero failures/errors/skips. This count comes from the summary log, **not** an independent fresh download/reparse of every G1 shard archive.
- R1R26 manually authorized [real rejection/retry 41139987350](https://github.com/Mikeyphw/devtool/actions/runs/41139987350) PASS with real success probe [41140021930](https://github.com/Mikeyphw/devtool/actions/runs/41140021930). Original evidence ZIP `13018672126` independently downloaded and SHA-256 checked: `590e98097273bdbb430c3b6a41b3dde14689f1fd9237ac55e00b17b95e53fa01`. `rejection-retry.json` confirms first real HTTP 422, durable `rejected-by-api`, no run ID, then one valid POST correlated to probe `41140021930`, and no additional POST on journal resume. Its `real_5xx_or_timeout_proven=false` and `release_qualified=false` remain authoritative.

## Reviewed two-scope window

1. **R1R27 (this overlay):** close the source-code and injected-response-loss safety gate using an actual remote GitHub no-op probe. A controlled fault is inserted **after the POST response is received**, inside the test client; it is not a real transport outage. Require no redispatch and no inferred journal correlation. Real GitHub execution is opt-in and remains pending until separately run.
2. **Next gate (not merged):** genuine server 5xx/transport timeout and independent operator reconciliation, plus native Termux ARM64. These require different authority/environment and cannot be proven by injecting a post-response exception. Keep OPEN rather than folding synthetic evidence into real-failure status.

## New source implementation

- `.github/workflows/devtool-fc08-dispatch-unknown-response.yml`: `workflow_dispatch` **only**, explicit `authorize_real_dispatch=true` input default false, job-scoped `actions:write`, checkout with `persist-credentials:false`, source-bound runtime and always-upload diagnostic receipt. The established four-lane host workflow retains `actions:none`; the probe workflow retains read-only permissions and does not recurse.
- `tools/fc08_dispatch_uncertain_outcome.py`: imports the reviewed `fc08_dispatch_cycle.py` by its *file path* (not the globally ambiguous `tools` package). `LostResponseClient` sends **at most one real POST** to the existing no-op success probe. Upon a valid GitHub 200 response with returned run identity it stores that identity **only in ephemeral fault-injector memory**, deliberately raises `GitHubDispatchOutcomeUnknown`, and requires the actual Devtool journal to retain `dispatch-outcome-unknown`, `run_id=null`, correct source SHA and input digest. Calling real `dispatch_workflow(...resume_if_existing=True)` again before and after independent GET-only remote verification must return the same request ID, `dispatched=false`, `safe_to_redispatch=false` and **zero new POSTs**.
- The separate `remote_proof()` check uses the injector-observed run ID from the original 200 response; it **does not write it to the uncertain journal**, infer it from search candidates, or authorize replay. If the real response was 204, malformed, a definitive denial, 5xx or timeout, the runner refuses success. Runner failure receipts preserve only allowlisted categories, no raw API bodies, credentials, filesystem paths or exception messages.
- `tests/test_fc08_r1r27_uncertain_dispatch_response_loss_20261030.py`: offline HTTP decoder + journal tests for success 200 injected loss, 204, 403, 422, 502, 504, timeout and malformed 200; forged journal correlation, unknown remote tampering, bad source, symlink/nonempty journal, foreign Hermes Agent `tools` import, workflow scopes and error-category redaction. No test sends a real POST.

## Promise-by-promise disposition

| Explicit promise | Evidence / acceptance | Status |
|---|---|---|
| Exact SHA/tree and clean-worktree apply | Installer source pin / preflight / atomic rollback | Implemented; installer acceptance required |
| Manually authorized isolated GitHub write scope | Workflow boolean input, job-only permission, no recursive fanout | Source implemented; hosted pending |
| One real no-op POST | Counted GitHub dispatch response in opt-in worker | Hosted pending |
| Controlled response lost after genuine accepted 200 | Fault injector throws before dispatcher receives result | Offline tested; hosted pending |
| Durable journal records unknown, uncorrelated request | Exact journal state/digest/request ID/run_id-null | Offline tested; hosted pending |
| Two resumes without new POST | Actual Devtool `dispatch_workflow` RM-16 journal scan and lock | Offline tested; hosted pending |
| Separately observe genuine remote completion | The *injector*-observed ID used only in independent GET probe; not journaled | Offline tested; hosted pending |
| Actual GitHub 5xx/transport timeout | Would require a genuine external fault; no synthetic promotion | **OPEN** |
| Automatic redispatch of unknown outcome | Explicitly forbidden; no operator reconciliation authority inferred | **PROHIBITED / never promised** |
| R1R26 true HTTP 422 rejection + valid retry | Original GitHub run + original receipt ZIP SHA | **PASS at R1R26 SHA only** |
| Full same-SHA hosted FC08/G1 for new source | Must be rerun after R1R27 push | **OPEN** |
| Native Termux ARM64, Android signer/instrumentation, BKX R3, actual consumers, FC09, release | Separate hardware and authority gates | **OPEN** |

## Security and operational limitations

- `fault_type=INJECTED_RESPONSE_LOSS_AFTER_REAL_ACCEPTED_200` is deliberate; `real_network_timeout_or_5xx_proven=false`, `real_unknown_outcome_reconciled=false`, `unknown_outcome_retry_authorized=false`, `fc08_overall_qualified=false`, `release_qualified=false`, `device_qualified=false`, `consumer_qualified=false` must remain false even on an otherwise successful receipt.
- Never print a bearer token or complete GitHub error body, and never infer an unknown journal's `run_id` from timestamp/branch/workflow alone. The externally observed remote ID is **not** a reconciliation or dispatch identity binding for the actual unknown journal.
- Existing Termux `/sdcard/Download` `artifact_cleanup_failure` after committed apply remains open; installation must use `--keep-artifact`. The Python 3.14 HTTPError mock `ResourceWarning` issue is separate; no claim of a fix here. Raw G1 shard re-verification and true native/consumer qualifications remain open.

## Operator procedure

After a successful R1R27 overlay application against exact R1R26 base, check branch and clean state, push, run `devtool-fc08-host-workloads.yml` and `devtool-full-repository-pytest.yml` on the new `main` source, then **separately** opt into `devtool-fc08-dispatch-unknown-response.yml` with `authorize_real_dispatch=true`. This deliberately starts **one actual GitHub probe run**, even though the test runner discards its API response. Inspect the controller receipt and the real remote probe before updating this evidence gate. Neither CI alone nor locally injected exceptions qualify a real GitHub timeout or 5xx.
