# RM-05 — promise-by-promise implementation audit

Scope: implementation and unit/transaction behavior only; external qualification gates excluded. `PASS` means a test exercises the behavior, not merely that a file exists.

| # | Promised behavior | Status | Behavioral demonstration / boundary |
|--|--|--|--|
| 01 | RM-05/RM-06 merge-window review | PASS | RM-06 depends on artifact handoffs: keep separate transaction boundaries |
| 02 | Canonical versioned ArtifactReceiptV2 identity | PASS | Exact-field schema and source/node/run/attempt matching; drift rejected |
| 03 | Producer binds actual SHA-256 bytes | PASS | Rehash modified ZIP and reject tampering |
| 04 | Consumers bind exact digest and differ from producer | PASS | Mismatched consumer digest refused; duplicates rejected |
| 05 | Typed format dispatch, unknown kind refusal | PASS | Verifier-kinds registry and negative unknown-kind test |
| 06 | ZIP/APK/AAB/JAR/NuGet/web archive structure | PASS, bounded | Required format members checked; provider-level Android signature and package-ID verification not inferred |
| 07 | Wheel RECORD and sdist PKG-INFO integration | PASS, bounded | Existing wheel verifier checks every member hash; sdist metadata identity checked when claimed |
| 08 | Go/native binary recognition | PASS, header-only | Magic header accepted; full Go buildinfo still UNKNOWN pending specialized provider |
| 09 | ZIP path traversal/backslash/duplicates | PASS | Multiple hostile archive fixtures |
| 10 | ZIP bombs, malicious links, malformed CRC | PASS | Expansion, ratio, per-member, link and CRC guards |
| 11 | TAR traversal, special entries, bounds | PASS | Safe member preflight without extraction |
| 12 | Claimed package ID must be verifiable | PASS, fail-closed | Web metadata checked, wrong ID rejected; compiled Android/binary ID claims blocked without proper provider |
| 13 | SHA-256 SBOM reference | PASS | Correct bytes pass, tampering blocked; trust explicitly not inferred |
| 14 | Absent/unsigned/untrusted states | PASS | `UNSIGNED`, `SIGNED_UNVERIFIED` and `VERIFIED` deliberately distinct |
| 15 | Cryptographic provenance adapter | PASS | Actual Ed25519 signature checked against caller-pinned public key; forgery fails |
| 16 | Signature never promotes remote/device/release status | PASS | Result flags remain false even for valid pinned signature |
| 17 | JUnit, pytest, JS JUnit report parsing | PASS | Multiple shards, duplicate tests, failure outcomes, malformed/XXE XML |
| 18 | TRX parsing | PASS | Bad roots/outcomes and duplicate test identities rejected |
| 19 | Go JSON and Cargo JSON parsing | PASS | Started-only truncated reports blocked; terminal outcome counted |
| 20 | Deterministic shard merge and duplicate policy | PASS | Stable sorted output, all cross-shard duplicated cases refused |
| 21 | Exact shard bytes, source, run, attempt | PASS | Tampered report and mismatched attempt refused |
| 22 | Per-node EXO result association | PASS, local | Hash-bound journal matching and contradictions tested; UNKNOWN if absent |
| 23 | Read-only CLI: artifact receipt and test shards | PASS | Canonical CLI schema/options and parser; separately invoke actual CLI |
| 24 | Generated CLI help/completion fixed-point | PASS | Canonical parser + generated surfaces exact-check |
| 25 | Existing artifact registry selectors preserved | PASS | Existing `typed_artifact_verify` unchanged; new receipt route opt-in |
| 26 | RM-00–RM-04 compatibility | PASS, bounded | Existing kernel, parser fixed-point, node scheduler/lineage/retry selected regressions |
| 27 | Exact post-RM-04 source guards | PASS | Whole Git tree, clean worktree and touched-file SHA-256 preimages |
| 28 | Mandatory native `devtool` target validation | PASS | In-process config + DAG graph plan; no nested CLI hook |
| 29 | Auto rollback after validator failure | PASS | Native artifact transaction negative scenario |
| 30 | Git branch and clean commit preservation | PASS | Disposable clone full install, no reset/force-push |

Known deliberately non-claims: GitHub-hosted attestation authority, code-signing CA acceptance, Android manifest package identity without aapt, Go binary buildinfo authority without a provider, signed release readiness, and actual consumer dispatch are **not** supplied by RM-05. Those require separate provider/runner/host modules. Existing already-present `normalize_test_report()` and registry evidence code are reused, not replaced.
