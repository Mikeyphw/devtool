# FC08 HOST-R1R14 — Promise/evidence audit

Source preimage: `7ab73e150662a5fd92f61c22c97dabd0cb872874` (`main`), tree `8d0156a48277cbc4338cd7d3442e36d3bff01404`.

| Promise | Evidence and status |
|---|---|
| Windows subprocess can emit Unicode checkmarks | Devtool CLI child receives `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1` only for Windows host; cp1252 counterexample and UTF-8 success tests. **Source-delivered; Windows hosted gate open**. |
| Real .NET/MSVC and failure propagation retained | No .NET/MSVC commands suppressed, outputs fabricated, or success forced. Existing negative-exit branch retains real exit semantics. **Source-delivered; Windows host unverified**. |
| Four-lane evidence gate remains strict | Only all four matching successful jobs with exact verified artifacts can yield the `HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW` receipt. Explicit failing-lane reasons, no release claim. **Source-delivered; new-host run open**. |
| Hermes `tools` package collision resisted | Host summary imports its exact sibling by source file, not package namespace; collision regression included. **Source-delivered; native validation open**. |
| Native transaction safety | Exact HEAD/tree and clean-worktree preflight, per-path preimage hashes, atomic apply + rollback contract. **Must pass native installer**. |
| Broader release qualification | G1 24 shards at R1R13 preimage passed (`40735681180`); new commit G1, BKX R3, device/consumer, FC09 **OPEN**. |

No change to production runtime, Devtool's installed IPC, or the Go spool.
