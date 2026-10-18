# RMP-01 / C02 — External consumed-source cleanup boundary

Status: **phase 1 user-reported PASS and committed** on `feature/rmp01-security-remediation` (`8060550`); **phase 2 candidate not yet installed**. H0 claim remains open pending on-device and full-caller qualification. See `docs/RMP_C02_PHASE2_DURABLE_PROVENANCE.md`.

## Confirmed fault and scope

The 2026-10-17 Devtool source snapshot allows `python -m devtool.artifact_cleanup_worker --repo-root R --reason ... /outside/file.zip` to unlink external files without a completed apply. A direct helper call on a symlinked archive resolves the link and deletes its referent. Both were reproduced only with disposable files under an isolated temporary root; no user files were touched.

### Fix

- Post-apply CLI cleanup now requires at least one unskipped apply operation with a recorded source artifact.
- The separate worker CLI refuses requests without a private inherited file descriptor containing bounded explicit file identities and repository/reason/run context. This is a process-local capability and **not a general security boundary against arbitrary code running as the same OS user**.
- The worker checks inode/device, size, ctime/mtime and parent identity immediately before unlink. Leaf symlinks and nonregular files are protected, and the unlink uses a pinned parent directory FD.
- Only an explicitly consumed source is removable. Implicit sibling download variants and sidecars are preserved. `artifact_cleanup_paths` still supports artifact-family discovery for other callers, but no longer confers deletion authority on this cleanup worker.
- Existing LIFE cleanup receipts, fresh-process policy loading, active-session isolation for external consumed files, repository-internal canonical LIFE authority and failing-operation classifications remain.

### Test and evidence

- Original source repro: symlink target removed while link survived; a no-apply external file removed by direct helper.
- Candidate: `tests/test_rmp_c02_consumed_source_authority.py` contains seven negative/positive checks (unauthorized direct worker, symlink, sibling and sidecar retention, identity replacement, authority substitution, verified worker receipt, missing apply result).
- Related old `test_artifact_matching.py` explicitly changes its previously unsafe "delete the whole duplicate family" expectation to "delete only the consumed source"; it remains a separate historical behavior contract change, not a silent test removal.
- Focused regressions: 48 passed, including LIFE, repository boundary, active-session and prior worker tests. No remote substrate or release claim.

### Outstanding before C02 H0 closure

1. Verify that original user-supplied symlink input is retained from CLI parsing through runner path normalization; `resolve_artifact_path` currently returns fully resolved paths. A symlink-to-valid-overlay input may still be treated as the target file by the *apply* runner before cleanup.
2. Audit caller provenance beyond the in-process operation result: same-user Python callers could fabricate results or an inherited descriptor. A stronger binding to the durable `ArtifactExecutionRecord` is recommended before asserting adversarial identity authorization closure.
3. Run the targeted tests on the actual Termux worktree and validate the post-apply flow on a disposable overlay. Preserve existing qualifications; avoid replaying old CI, G3R, SSH or Android campaigns.
4. Evaluate whether deliberately saved download variants need a distinct, explicitly confirmed garbage-collection operation (not implicit post-apply source deletion).

No status in the 308-row historical ledger is changed by this candidate. RS13 remains unapplied and separate. UX/TUI remains deferred.
