# R39-R4 — Consolidated architecture and roadmap-gate audit

## Applied source-scope improvements

1. First-party GitHub Actions used by 22 remaining historical workflows are
   migrated to explicitly Node 24-compatible majors, including the FC08,
   Android, polyglot, and release-qualification families. Six Android
   workflows keep immutable commit pins, now advanced to verified Node 24
   release SHAs instead of moving major tags. `gradle/actions/setup-gradle`
   moves to v5 (Node 24) for **GitHub-hosted builds only**. The older full Python
   regression and R39 parity workflows already used Node 24-compatible majors.
   Third-party action pins must be qualified independently; this update does
   not imply every external action has been audited.
2. One read-only `roadmap_gate_audit` generates a deterministic source inventory,
   current commit/tree, first-party Action declarations and independent gate
   states. No network access and no test/build dispatch occurs in this audit.
3. One manual hosted workflow runs the R38/R39 contract tests, key NEXT/R35/R36
   source tests, bounded serial real-subprocess parity and synthetic DAG workloads
   and uploads source-bound JSON receipts.
4. **Legacy `run_specs()` stays available.** Bounded serial parity is not enough
   to delete the engine used by native execution for actual subprocesses.

## Which claims are NOT closed by this overlay

| Independent gate | Status before new hosted runs | Owner/evidence required |
|---|---|---|
| 24-shard regression on the newly produced commit | NOT_RUN | Exact-source hosted run with all shards |
| Consolidated source contract workflow on that commit | NOT_RUN | Exact-source workflow with uploaded JSON |
| Cross-platform real host / provider acceptance | NOT_RUN | Current-source Windows and Linux runtime receipts |
| Authenticated operator-host APK import | NOT_RUN | On-operator exact-APK signature/persistence receipts |
| Real consumer adoption | NOT_RUN | Reviewed per-consumer real workflow evidence |
| Android/root/LSPosed acceptance | NOT_RUN | Device run, package/signer identities and JSON receipts |
| Parallel parity & interruption recovery | NOT_RUN | Equivalent controlled real-workload and crash evidence |
| Untracked/external write containment | NOT_RUN | Real filesystem containment/host tests, beyond tracked snapshots |
| Real cache throughput/Gradle improvement | NOT_RUN | Real hosted producer/consumer performance receipts |
| Dynamically generated legacy Action pins | NOT_RUN | Generator-source migration and new generated-output parity; static workflow migration alone is insufficient |
| Unreviewed third-party Actions | NOT_RUN | Per-action runtime/source review or on-run deprecation-free evidence |
| Signing and formal release | BLOCKED | Independent signer, provenance, release policy authority |
| Full legacy engine retirement | BLOCKED | Broad parity and dependency migration proof |

## Historical provenance (not current SHA closure)

- `6426e6e`, run `41361212330`: 7,395 Python tests, 24/24 shards passed.
- `6426e6e`, run `41361215581`: five bounded serial scenarios passed.
- `e3cc547`, run `41351437389`: hosted synthetic DAG benchmark passed.

Historical receipts are immutable source-scoped references. They **do not**
become current-commit PASS simply because the next commit inherits the code.
`roadmap_gate_audit` therefore reports new hosted, consumer, device and release
claims as `NOT_RUN` or `BLOCKED` by design. The gate audit's `PASS` means only
that source inventory and first-party Action declarations passed its checks.

This work is an R39-R4 architecture audit and CI/source-gap remediation, **not**
formal release qualification. No on-device/root or Gradle build runs on Termux.

## R37 MMRL device workflow immutable pins

The consolidated closure also upgrades three older first-party pins in
`.github/workflows/devtool-r37-mmrl-device-source.yml`, which were
missing from the older disposable source snapshot. All three replacements
resolve to the Node.js 24-compatible checkout v6, setup-python v6, and
upload-artifact v7 commits. SHA identity was checked against the GitHub
release refs; the R37 workflow remains source-only (non-release).

Audit regressions require the tracked R37 workflow and check its actual
post-migration pinned references. A source-incomplete local fixture must
not qualify as a complete workflow migration.
