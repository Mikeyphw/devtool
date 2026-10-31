# CORE-R30 — consolidated cleanup, OCI and source-evidence overlay

## Source identity and merge window

- Target `Mikeyphw/devtool` snapshot/GitHub main at `eb7e9ed2e24a19ac2aa89a13676e3d56b287c9d8`, tree `cd2a61ca43afa08bfc83ba5459e82aa80a32a071`, clean at intake.
- Reviewed adjoining scopes: CORE-R30 (cleanup + OCI + provenance reliability) and ANDROID-R31 (hosted Android build/artifact import). Shared identities and artifact evidence justify using R30 contracts as R31's predecessor, **not** merging signed Android/consumer adoption into cleanup transaction authority. R31 remains separate.
- Existing CLI runner requires durable RUN-V3 execution record AND sealed source provenance for fresh-process deletion; no alternate/unrestricted deletion is introduced.
- Earlier Wave B/D GitHub full G1 failure on `7f2f97b` had embedded Android resource-digest drift, generated guides drift, and canonical authority mismatch. Snapshot `eb7e9ed` includes subsequent OCI corrections; historical failure is not current-source proof. R30 adds read-only drift admission and independent CI but does not claim a current hosted PASS in advance.
- User-provided R1R29-H1-R2 native Termux receipt at `0103419de2c27d8434e8d374f7dd03f977624d8f` confirms actual CMake/CTest, Meson, ELF and owned-temp assertions. Shared-storage controlled canary confirms a user-approved scratch file was created and removed; this evidence is historical for `0103419`, **not** the R30 source or original downloaded artifact cleanup.

## Changed behavior

1. `devtool/artifact_cleanup_worker.py` outer catch no longer returns `str(exc)` from any exception. Bounded, **fixed allowlisted** descriptions preserve legacy refusal families (`authority`, `durable execution provenance`, `durable consumed-source inventory`, `attachment integrity`). Adversarial arbitrary secret-bearing exceptions cannot appear in emitted JSON. Both source-plan early exit and worker outer exception are covered. All identity, symlink, provenance and fd-based safety protections unchanged.
2. `tools/core_r30_cleanup_replay.py`: explicit **synthetic** test of actual fresh-process cleanup worker with a private local Git RUN-V3 fixture, an exclusively new O_EXCL ZIP under an optionally shared-storage scratch dir, and durable source authority. Positive, replaced-after-snapshot and uncredentialed refusals are followed by a **new synthetic authority** for only those caller-approved fixtures. Never reauthorize a user artifact or silently recover failed real applies. Residues, if any, require manual review. All output is categorized/redacted and cannot qualify original apply lifecycle.
3. `tools/core_r30_contract_audit.py`: read-only exact-SHA/tree/clean-worktree admission, canonical OCI-05 image manifest and embedded source-digest loader, generated guide `--check`, and unchanged-source recheck. Its output is atomic, false release flags. It does not rewrite manifest, regenerate guides or execute Docker/Android jobs.
4. `.github/workflows/devtool-core-r30-source-qualification.yml`: manually dispatched Ubuntu source/cleanup CI with no `actions:write` permission and source-bound receipt/JUnit collection. Not a substitute for 24-shard G1 or FC-08 real host CI.
5. New focused adversarial/positive tests and append-only cumulative improvements/crosswalk.

## Promise-by-promise disposition

| Promise | Source-local gate | External gate |
|---|---|---|
| Exact snapshot/tree + original-path integrity + transactional rollback on failure | Manifest, preflight, full-file SHA256, after-extract tests | Actual Termux `apply-overlay` PENDING |
| No raw exception/path or injected secrets in outer cleanup worker refusal | New direct monkeypatched adversarial tests + legacy authority suite | Real user failure receipt PENDING |
| Actual worker on a temporary, source-authorized ZIP | Real subprocess + private durable RUN record; positive/drift/no-authority cases | Termux shared-storage replay PENDING |
| Strict no new artifact deletion outside proven disposable identities | O_EXCL + dirfd worker; no fallback os.unlink; sibling preserved | Original `/sdcard/Download` cleanup root cause OPEN |
| OCI-05 embedded runtime bytes and generated CLI docs not stale | `load_android_oci_contract()`, generator --check; read-only source gate | R30 exact-source hosted checks PENDING |
| New source-bound G1/FC08 and downstream ANDROID-R31 adoption | Not in source-only overlay | New SHA 24-shard G1, four real hosts, signed-device-consumer evidence OPEN |
| FC09 independent 168-promise formal review | Crosswalk preserves false flags | OPEN; never asserted from synthetic receipts |

## Run only after successful transactional installation

```sh
cd "$HOME/Code/devtool"
python3 tools/core_r30_contract_audit.py \
  --repo "$PWD" --out "${TMPDIR:-$PREFIX/tmp}/core-r30-contract.json"
# OPTIONAL: creates only disposable owned ZIP fixtures inside selected directory
python3 tools/core_r30_cleanup_replay.py \
  --repo "$PWD" --scratch-dir /sdcard/Download \
  --yes-create-owned-fixtures \
  --out "${TMPDIR:-$PREFIX/tmp}/core-r30-cleanup-replay.json"
gh workflow run devtool-core-r30-source-qualification.yml --ref main
# Also run the existing FC08 four-host and G1 24-shard workflows.
```

## Caveats

- A synthetic durable RUN record is **not** attestation of an actual overlay transaction or external release owner.
- The original user archive remains untouched. If the private fixture's identity changes unpredictably, it stays on disk and the redacted receipt shows only its synthetic basename; do not force deletion on refusal.
- OCI/guide admission is read-only and reports failure; it does not automatically resynchronize a drifted copy (which requires a separately reviewed source change and new seal).
- Avoid treating R30 CI success as formal FC-09, Android signer/consumer or current native Termux qualification.


## R1 hotfix — actual Termux installer collection failure

- **Actual failure:** transaction `02ac4d53af04488586fa75b209a18ea7` passed clean source and exact-tree manifest, then failed at pytest collection because Hermes Agent's installed `tools` package shadowed Devtool's `tools/` directory. No worker assertion was executed; the original R30 install was not accepted.
- **Repair:** load `core_r30_cleanup_replay.py` and `core_r30_contract_audit.py` via exact source-local file paths in their tests, with explicit source identity. A foreign-package regression injects `sys.modules['tools']` to verify no fallback to the global package. No modifications to worker lifecycle, actual artifact deletion rules, or host permission assumptions.
- **Scope:** full R30 replacement pinned to `eb7e9ed2e24a19ac2aa89a13676e3d56b287c9d8` / `cd2a61ca43afa08bfc83ba5459e82aa80a32a071`, NOT a delta onto a failed transaction. Native Termux validation and fresh hosted source qualification remain required; do not claim success before those receipts exist.
