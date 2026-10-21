# RM-03 implementation-promise audit

Source: reconstructed exact post-RM-02 Git tree. This audit assesses actual behavior, not file existence or external qualification gates.

| # | Promised behavior | Implementation check / regression |
|---|---|---|
| 01 | Stable source/target/workflow/node/attempt identity | Workflow spec metadata computed from source and config; target-local integration test inspects binding |
| 02 | Canonical live node events rather than a second bus | Existing EXO events annotated, `run_specs` event interception asserts logical node terminal |
| 03 | At most one terminal for a multi-command logical node | Real two-command passing and failing runner tests assert exactly one parent terminal and no restart on failed suffix |
| 04 | Record duration/error and preserve child/parent relationship | Existing `NodeFinished` and `TaskFinished` carry durations and failure; child tests inspect completion |
| 05 | Source and config fingerprints retained in event stream | `_node_specs` produces SHA-256 config and canonical identities; `NodeDiscovered`/task metadata include them |
| 06 | Typed `remote-run-correlated` job | Real target-local TOML job executes local verifier; no dispatch |
| 07 | Typed `remote-build-verified` job | Bound repository/source/run/attempt/request and observed successful build required |
| 08 | Typed `artifact-verified` job | Actual SHA-256 of in-repository bytes, path/symlink protections and source binding |
| 09 | No successful remote inference from local receipt | All typed results explicitly `remote_authenticated=false`; inaccessible states remain visible |
| 10 | Conservative read-only lineage CLI | Parser/schema/registry identical; EXO projection/receipts join and deterministic repeat |
| 11 | Restart / partial journal correctness | Missing journal `SNAPSHOT_ONLY`, strict sequence and duplicate terminal refusal |
| 12 | Reject mismatched EXO node/source/remote run/attempt | Negative tests mutate each field, duplicate IDs and artifact bytes |
| 13 | Missing remote evidence remains UNKNOWN | Each node defaults to UNKNOWN; no dispatch or fabricated events |
| 14 | Pipeline/federation identities are correlation only | Run-level persisted reference projection, never independent remote attestation |
| 15 | Preserve RM-00/RM-01/RM-02 and EXO behavior | Mandatory native target-plan validation + dedicated suites and existing EXO reducer tests |
| 16 | Exact source-bound, fail-closed overlay | preflight hashes/clean tree, native apply transaction, post-apply behavioral tests, rollback |

**Intentionally not claimed:** authenticated provider acceptance; independently attested remote producer; SBOM/signing; full artifact provenance (RM-05); retry/resume or deduplicated remote dispatch (RM-04). Source fingerprints describe the checked-out commit; if unavailable they are unknown and cannot attach a remote receipt. Operator-visible `LOCAL_EVIDENCE_CONSISTENT` is not a trusted CI verdict.
