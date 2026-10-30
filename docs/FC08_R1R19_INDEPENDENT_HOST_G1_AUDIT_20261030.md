# FC08 HOST-R1R19 — Independent hosted/G1 evidence review and scoped gate closure

**Date:** 2026-10-30. **Source:** [`641009b8859e00a9c10cc264e2a0a41492b68d0c`](https://github.com/Mikeyphw/devtool/commit/641009b8859e00a9c10cc264e2a0a41492b68d0c); **tree:** `01dd82e57eebb61aaabd0f937edc365a1dcaf8b3`. **Repository:** `Mikeyphw/devtool`.

**Decision:** Close the **hosted four-lane workload evidence capture + independently checked receipt integrity** and the **current-commit G1 24-shard Python regression**. This is a scoped evidence closure, **not overall FC08 qualification, consumer/device acceptance, FC09 final seal, or release authorization**. Both `fc08_overall_qualified` and `formal_release_qualified` remain `false`.

## Independent evidence and source identity

- [FC08 run 41068348030](https://github.com/Mikeyphw/devtool/actions/runs/41068348030) is `success`, 2026-10-30, same source SHA as G1. All five jobs passed (DAG, Linux, Windows, macOS, fail-closed summary), and five authentic GitHub artifact ZIPs were retrieved and compared byte-for-byte via SHA-256 against their reported GitHub Actions archive digests. Raw JSONs were read and checked, **not inferred from green job badges**.
- The four lane receipts all have `schema=devtool.fc08.host-real-workload.v1`, same `repository/ref/SHA/tree/run_id/attempt=1`, `release_qualified=false`, `device_qualified=false`, and matching canonical `checks_sha256` values. Raw receipt bytes match the `receipt_sha256` entries in `host-summary.json` (Windows is CRLF encoded, so hashes must use raw bytes rather than re-serialized JSON).
- Summary schema `devtool.fc08.host-real-summary.v1`, `status=HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW`, with exactly four expected lanes, expected check lists and `release_qualified=false`. This independent review examines the byte integrity and API source tuple; it does not rewrite that producer status.
- Windows ran eight named checks: locked cold/warm restores, build, TRX+Coverlet coverage, NuGet pack, publish artifact, native MSVC CMake build/test, nonzero propagation. The coverage record shows **2 candidate XMLs with identical SHA-256 hashes**, one selected using VSTest attachment evidence. Other hosts execute their declared native workloads and negative-exit propagation.
- [G1 full regression run 41068356185](https://github.com/Mikeyphw/devtool/actions/runs/41068356185) is `success`, with 24 shard jobs and 1 fail-closed summary job. Its 25 GitHub artifacts were downloaded; all archive digests matched GitHub's reported SHA-256s. The receipt and 600 JUnit XML/log pairs were independently parsed and their internal hashes checked. Counts: **600 unique test modules, 6,855 primary pytest cases, zero failures/errors/skips, plus 75 passing pytest subtests**. Seven JUnit suite headers include those additional 75 subtests; module `receipt.json` values count primary testcases only. The G1 summary accurately reports primary pytest tests, not subtest expansion.

## Promise-by-promise gate disposition

| Requirement | Disposition | Evidence / reason |
|---|---|---|
| Exact source/run/attempt, authentic GitHub ZIP integrity, raw receipt hashes | **PASS (scope closed)** | Five FC08 ZIP digests, four canonical checks hashes, four raw-receipt joins, summary and authenticated GitHub run/jobs |
| Hosted Linux provider workloads | **PASS (workload evidence)** | `linux-polyglot.json` lists Python+uv/Poetry/PDM, Go, Node, Rust, Maven, CMake, Meson and negative-exit |
| Hosted Windows .NET/MSVC | **PASS (workload evidence)** | `windows-dotnet-msvc.json` includes all 8 expected checks, TRX and selected Cobertura hashes, MSVC build/test |
| Hosted macOS native and unsupported-host rejection | **PASS (workload evidence)** | `macos-native.json` includes native CMake/CTest, unsupported Windows host refusal |
| GitHub Actions DAG execution and negative-exit | **PASS (bounded observation)** | `github-actions-dag.json` and five successful jobs |
| Current-source full G1 24-shard Python regression | **PASS (scope closed)** | Run 41068356185, 24 raw shard artifacts, 600 JUnit/log matches, 6,855 pytest cases and 75 subtests |
| Repeated/failed dispatch and GitHub permission-denial scenarios | **OPEN** | Present DAG receipt says `FANOUT_SCHEDULED_NOT_YET_AUTHENTICATED`; no adversarial independent result |
| Linux locked/offline/ABI negative scenarios | **OPEN** | uv/Poetry/PDM `lock+run` is not an offline-lock/ABI refusal qualification |
| Windows signed authority and independent raw TRX/Cobertura/compiled bytes | **OPEN** | The published Windows artifact is a compact JSON receipt, not raw build, test, signing or executable provenance |
| Native Termux ARM64 CTest/Meson/owned-temp/recovery | **OPEN** | Hosted Linux/macOS are not native Termux proof |
| Android signer/installed identity/instrumentation | **OPEN** | No real device or signing receipts in these runs |
| Actual Telegram/Studyflow/BKX consumers and rollback/forward recovery | **OPEN** | Current reviewed consumer repo/source and post-change receipts needed |
| FC09 168-promise crosswalk, independent seals and release authority | **OPEN** | Separate full scope; no automatic promotion from hosted G1 or FC08 |

## Non-inference boundaries

- The GitHub API artifact digest proves the archive bytes match what GitHub published, not that the underlying TRX/Cobertura/compiled native bytes are independently reproduced or signed. The JSONs include hashes of such outputs but do not contain the original payloads. Do not claim raw-artifact or signer verification.
- All job results are source/attempt specific. Running on a future commit requires requalification of the affected scope.
- G1 source tests cannot qualify native Termux or real Android hardware. A consumer simulation is not consumer adoption acceptance.
- This audit makes no GitHub repo mutation or release decision. Source documentation still needs operator-authorized commit; the bundle's optional helper is guarded by the exact SHA/tree and clean working tree.

## Next practical gates (ordered)

1. **Record scoped evidence closure** in cumulative `docs/DEVTOOL_IMPROVEMENTS.md`, preserving R1R18's historical status and adding a new R1R19 entry. Do not retroactively rewrite old evidence.
2. **FC08 remaining hosted adversarial work:** GitHub DAG repeat/failed dispatch and permission denial, then Linux lock/ABI negatives. Collect authenticated source/job/artifact metadata and independently validate actual receipts.
3. **Separate native/consumer acceptance:** true Termux ARM64, Android signer/installed instrumentation, then source-pinned real Telegram/Studyflow/BKX adoption plus rollback/forward recovery and independent authority. Do not infer from workflow success.
4. **FC09 promise matrix + formal closure:** audit the full 168 promises including cancelled scope, collect missing independent evidence, or obtain an explicit owner-approved change to that scope. Formal release remains blocked until the required gate authority approves.

## Reproducibility

Bundle includes the 30 original downloaded archive ZIPs (five FC08, 24 G1 shards, one G1 summary), `evidence_manifest.json` linking each GitHub artifact ID to expected archive digest, and `verify_offline.py` to verify all raw archives and recalculate counts. Re-run `python3 verify_offline.py` in the bundle root before recording the result. Independent API metadata queried on 2026-10-30; replay of the local verifier checks the captured archive bytes and structural claims, not future GitHub availability or keys.
