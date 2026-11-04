# DN03-R6 — Native typed TRX evidence (non-release)

## Why R6 exists

Hosted DN03-R5 run 41767446183 passed six .NET jobs and R4 verification.
The two test jobs created `dist/pure/results.trx` (21 passed) and
`dist/animal/results.trx` (8 passed), but their typed node evidence still said
`test: NOT_RUN` even though native RM-09 validated the reports.

## Added contract

- The native RM-06 job's existing `node-evidence.json` now records `tests.format=trx`,
  a nonzero count, exact report SHA-256 and `test: PASS` **only** when a
  .NET test node declares exactly one TRX output whose RM-06 source/run/attempt
  handoff and byte digest have been verified, and the report passes independent
  RM-09 and NEXT-02 parsing. Non-test nodes remain `NOT_RUN`.
- `verify_node_evidence` reparses staged TRX bytes and cannot accept a caller
  editing only the claimed test count, digest, reported PASS or receipt hash.
- DN03-R3's node verifier records parsed counts and the TRX digest in the R3
  receipt, which already binds `node-evidence.json` and the reviewed host receipt.
- DN03-R4 downloads the actual declared bytes from the current run, reparses
  them independently, checks the R3 TRX claim, and binds them in its cross-node
  review. The result distinguishes verified test nodes and cases from build-only
  nodes without treating an absent test report as success.
- Malformed, zero-test, missing, symlinked, altered, duplicate-ID, conflicting
  counter, skipped, timed-out and failed TRX reports are fail-closed.
- When reviewed `ci_dotnet_coverage=true` is selected, the exact staged Cobertura
  XML is independently reparsed and its SHA-256 is bound into the R3/R4 TRX
  evidence alongside the already declared native coverage artifact; missing,
  malformed, empty or inconsistent reports are refused.
- No extra .NET restore/build occurs on the user's Termux device. All real
  .NET execution remains hosted.

## Trust boundaries and remaining work

These are source-bound *local* and downloaded same-run checks, not an
independent GitHub artifact origin attestation. `remote_authenticated=False`,
`release_qualified=False`, and the NEXT-02 external effective gates remain
`UNKNOWN`. The existing R5 workflow is pinned to the older Devtool source and
is intentionally **not silently upgraded** by this change. A new consumer
workflow must be generated from the R6 Devtool commit and accepted on GitHub
before any CI cutover. Coverage instrumentation remains opt-in through the
existing `ci_dotnet_coverage` contract; R6 binds any declared real Cobertura
output and never infers line coverage from TRX test counts. Do not remove the previous, proven R5 workflow during rollout.
