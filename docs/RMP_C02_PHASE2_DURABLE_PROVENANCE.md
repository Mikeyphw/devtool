# RMP-01 / C02 Phase 2 — Source provenance and durable cleanup authorization

Status: **source-bound implementation candidate**, not installed or live Termux qualified. Previous C02 phase was user-reported passing and committed as `806055083ecb5204fa4adca7aec75dfad41862ef`. H0 `RMP-C02` remains open for live verification and full caller audit.

## Security requirement and observed gap

Phase 1 refused unauthenticated subprocess deletion, symlink leaf targets and archive-family deletion. The still-open problem was that unrelated `OperationResult` metadata could request cleanup of an external path after a different apply and that a valid persisted apply record could potentially be repurposed to justify deletion of a different source. Original CLI artifact resolution also erased the caller's symlink leaf path before the cleanup boundary.

## Implemented changes

- `resolve_cli_path(..., preserve_leaf=True)` retains the final input component, without changing its default behavior for other callers. `resolve_artifact_path` rejects symlink input before family selection; artifact-variant discovery skips symlinked duplicates.
- `_delete_original_artifact_after_apply` restricts consumed sources to the exact, unskipped apply result(s) with deletion policy. Other operation metadata or a bare CLI `artifact` argument cannot widen the deletion list. Ambiguous duplicate source ownership fails closed.
- The runner's durable `ArtifactExecutionRecord` now has a separate persisted source-provenance attachment. It records the consumed source's exact path, parent device/inode, file device/inode, size, mtime and ctime. RUN-V3 attests the attachment digest. No raw file content is copied into the receipt.
- The worker checks the persisted execution record, its RUN-V3 catalog identity and attachment SHA-256, then independently checks the durable consumed-source inventory and its own attachment. It requires exact path and file identity equality with the private inherited authority before the pinned directory-FD unlink.
- Ad-hoc multi-overlay bundles update the provenance attachment with the actual component source identities rather than treating the temporary generated bundle as their original source.
- The narrow external consumed-source fast path remains; repository-internal paths still lower through LIFE ownership policy. No retry of uncertain execution and no deletion of sidecars/sibling variants.

## Safety boundaries

This is a *consistency and provenance* guarantee across trusted components, **not** an OS-level sandbox against arbitrary processes with the same write permissions as Devtool. Same-user processes that can edit private RunStore metadata and produce valid attachments can still forge local evidence. Formal campaign closure additionally requires checking external callers and live Termux behavior.

**Do not apply as a general cleanup policy:** only a successful, unskipped apply with an original source (or an explicit `--delete-artifact-on-failure` path) can request post-apply source removal. Existing `--keep-artifact`, transaction rollback and protected cleanup semantics stay in place.

## Verification

- Focused offline suite: 54 passing tests across source authorization, symlink handling, variants, active validation sessions, subprocess recovery/timeouts, cleanup retention and repository boundaries.
- Negative cases: missing/falsified execution reference, missing/altered durable attachment, non-applying result path injection, mismatched durable source inventory, symlinked input.
- Real disposable CLI `apply-overlay` flow participates in those tests, including deleting only the consumed duplicate archive variant.
- Source-bound overlay validation repeats this set. No original protected CI24/G3R/Android/SSH/RS13 campaign reruns are requested.

## Follow-up before C02 closure

1. Install/verify on the actual Termux security branch (not the G3R qualification branch).
2. Confirm live RUN-V3 source-provenance receipts from one disposable overlay and one ad-hoc bundle, including cancellation/failure behavior.
3. Audit non-CLI cleanup entrypoints and symlinked parent directory behavior. No bulk garbage collection without independent explicit authority.
4. Preserve the 308 historical RMP findings and six separate audit obligations as open until their own evidence closes them.
