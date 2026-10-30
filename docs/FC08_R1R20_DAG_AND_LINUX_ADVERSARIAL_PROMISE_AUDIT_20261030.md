# FC08 HOST-R1R20 — combined DAG verifier adversarial and Linux negative workloads

**Change status:** Source implementation and disposable Linux testing complete. **Hosted qualification: OPEN** until a new GitHub run executes this source commit. Release/device/consumer qualification remains open.

## Authority and merge window

- Reviewed GitHub `main`: `67ba4ea1f9226056600b51c0ef591b1eb5c8782f`, tree `52cc3b2525ba4402493cea27495992c93739eb90` (R1R19 documentation-only commit).
- Prior hosted four-host success: run [41068348030](https://github.com/Mikeyphw/devtool/actions/runs/41068348030) at `641009b8859e00a9c10cc264e2a0a41492b68d0c`, including 5 successful jobs and four lane JSON receipts. The *same original SHA* also passed G1 full 24 shards in [41068356185](https://github.com/Mikeyphw/devtool/actions/runs/41068356185). The subsequent documentation-only R1R19 commit has not been rerun; it remains a separate source identity.
- Merged two adjacent hosted follow-ups because they share `tools/fc08_host_workloads.py`, the existing unchanged 4-lane workflow and fail-closed summary schema. No new GitHub workflow, credential, permission, token, dispatcher or release authority introduced.

## Promise-by-promise assessment

| Promise | Source/test evidence | Status |
| --- | --- | --- |
| Real Linux locked/offline stale-input refusal | After real `uv lock` and `uv run`, alter only a disposable project's version, run `uv lock --check --offline`, require nonzero, and require unchanged `uv.lock`; restore original version, require offline check success | **Delivered locally; hosted OPEN** |
| Linux native wrong-ABI rejection on actual built bytes | Reuse native CMake build/test product `fc08main`, require the real RM-13 `verify_binary` to identify an ELF x86-64 and reject the same binary when declared as ELF aarch64; receipt binds binary SHA-256 | **Delivered locally using actual native build; hosted OPEN** |
| DAG verifier rejects failed upstream jobs | On the real GHA DAG host, invoke the *real* `fc08_host_summary.py` executable with synthetic failed DAG and failed Windows `needs` JSON; require exit nonzero and no promoted output | **Delivered in source; hosted OPEN** |
| DAG verifier rejects incomplete `needs` and replayed attempt | Real summary process must reject missing jobs and a lane receipt with an incorrect attempt even if synthetic control receipts initially form a valid summary | **Delivered in source; hosted OPEN** |
| Positive synthetic control never promotes FC08/release | Baseline verifier call creates synthetic receipts with exact current run/attempt and expects a non-release `HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW` summary. All these receipts are explicitly synthetic; no consumer/device assertion | **Delivered locally; hosted OPEN** |
| Genuine failed/repeated GitHub dispatch, real GitHub token denied by permission | No live dispatches or authenticated write requests in the read-only workflow. Summary-process adversarial cases are **not equivalent** to live dispatch/permission exercise | **OPEN — independent operator/authority required** |
| Negative Linux manager matrix beyond `uv`, Go/other native ABIs | Python uv, Poetry, PDM, Go etc existing successful workloads retained, but only uv stale-lock and x86-64-vs-aarch64 ELF are newly exercised as negatives | **PARTIAL — remaining genuine matrix cases OPEN** |
| Retain prior hosted four-lane positive checks | Existing Windows 8 checks, macOS 3 checks, Linux provider matrix and DAG negative-exit unchanged. New checks extend Linux by 2 (11→13) and DAG by 1 (3→4) | **Delivered in source; hosted rerun OPEN** |
| Receipt and summary fail-closed status and exact identity | New required check names are included in `EXPECTED_CHECKS`; missing check or digest mismatch still rejected; `release_qualified=false`, `device_qualified=false`, host summary `fc08_overall_qualified=false` retained; exact source SHA/tree/attempt unchanged | **Delivered locally; hosted OPEN** |
| Full G1 at post-change SHA | 115 targeted FC08 tests passed locally with 0 skipped, including actual uv offline refusal and full CMake/ELF verification; this is **not** the 24-shard suite | **OPEN — fresh 24-shard G1 required** |
| Native Termux ARM64, Android signing/device, real consumer/BKX R3 and FC09 seal | Intentionally untouched; no source-only authority promotion | **OPEN** |

## Tests / observations

- `python3 -m pytest -q tests/test_fc08*.py` → **115 passed** on disposable Linux source, with the test exercising the actual `fc08_host_summary.py` subprocess.
- Direct manual `build_cmake` followed by `linux_native_abi_refusal` → real CMake build+test and real ELF x86-64 accepted, ELF aarch64 refused on identical binary SHA.
- Direct `uv lock --check --offline` with version drift → nonzero and unchanged lockfile; restoring project version allows a successful offline locked check.
- Exact GitHub source tree and full current working tree reconstructed from content-addressed commit objects; source pin checked against authenticated GitHub metadata.

## Next evidence and scope

1. Apply the exact-commit, clean-tree R1R20 overlay; verify local test and atomic commit.
2. Push and dispatch `.github/workflows/devtool-fc08-host-workloads.yml` at the *new SHA*, checking Linux **13** and DAG **4** checks while Windows and macOS also pass, then require four receipts and valid summary; archive run/job/artifact IDs, attempt, SHA/tree and hashes.
3. Dispatch `.github/workflows/devtool-full-repository-pytest.yml` on the same source SHA; require all 24 shards and fail-closed final summary.
4. Audit real failed/repeated dispatch, token permissions and additional manager/ABI refusals under separate limited authority; native Termux ARM64/device/consumer/BKX R3/FC09 remain untouched.

Do not count the synthetic negative fixture receipts as independently verified live GitHub dispatch denial, and do not convert the successful historical R1R19 review into current-SHA G1 evidence.
