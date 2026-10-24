# FC-02 — RM17/RM18/RM20 guarded transaction engineering

FC-02 revises the in-place multi-repository staged adoption and whole-repository
onboarding workflows. **It does not implement FC-03 classifier improvements or
FC-05 complete onboarding proposals.** RM19 extraction remains cancelled.

## Security and state invariants

- All new commits are constructed off-checkout using a separate Git index, pinned
  parent, reviewed byte hashes and exact Git tree; no ordinary `git commit` may
  unexpectedly choose an unreviewed parent.
- Every publication uses `git update-ref <branch> <candidate> <expected-old>`.
  An unrelated branch update makes the lease fail instead of rebasing or
  overwriting the new commit.
- Multi-repo prepare recomputes expected modified bytes from the reviewed source
  Git objects and source-pinned ZIP. A journal plus `refs/devtool/adoption-prepared/*`
  is *not* sufficient authority for publication; the independently rederived
  preimage and postimage SHA-256 maps must match the candidate commit tree.
- Never use `read-tree --reset -u`, checkout, hard reset or an unguarded
  `os.replace` for publication. Files are read through no-follow directory
  descriptors, old entries moved into a persistent escrow outside the worktree,
  late foreign edits restored using exclusive file creation (no hardlinks),
  new files created exclusively, and the index reconciled separately under Git's conventional exclusive `index.lock` lease.
- A late edit or conflicting path is preserved (in the worktree, verified escrow,
  or both) and the journal records `PARTIAL_COMMIT`. `COMPLETE` is not emitted
  for a dirty or partially synchronized checkout.
- `PREPARING` can be replayed by re-deriving reviewed changes, or explicitly
  aborted before branch mutation. A crash after escrowing a verified original
  but before candidate creation can resume from that escrow. Ambiguous or
  incomplete candidate files require manual review; the reconciler refuses
  to delete or replace them automatically.
- Whole-repo onboarding creates `.devtool.toml` using `O_CREAT|O_EXCL` and
  publishes its reviewed Git commit with the same CAS helper. On a failed CAS,
  the created file is deliberately preserved for review rather than attempting
  a potentially racy unlink. The failure is explicit and may leave a dirty
  worktree; do not mistake that for a completed installation.

## Operational limits

Git offers no cross-repository atomic ref transaction. Recovery compensates
only still-matching branches and never force-resets a newer unrelated commit.
Persisted displaced preimages must not be deleted by automation; they are
intentional recovery evidence, and an operator should review them before
removal. Fully malicious same-user modification of the source manifest,
Git object store and every trust anchor is outside this untrusted-journal
threat model. Hostile writers that ignore Git safety checks may still cause a
manual reconciliation state; they must not cause silent deletion.

## Acceptance and closure boundaries

Adversarial tests cover post-check worktree writes, journal+ref substitution,
extra unapproved Git paths, interrupted `PREPARING`, killed reconciliation after
escrow rename, symlinked parent paths, onboarding HEAD update and concurrent
configuration creation. Existing RM17/RM18/RM20 and legacy NEXT-05 adoption
regressions are required. Native Devtool transaction checks include exact
source, dirty source, wrong source, injected validator failure and rollback.

Engineering verification does not confer real Telegram/Studyflow consumer
acceptance, GitHub-hosted execution or full RM20 feature completeness. The
original promise IDs remain open for externally required evidence in FC-08/09.

## FC-02 Termux escrow restoration portability correction (2026-10-24)

The first source-bound FC-02 Termux installation stopped in
`test_late_edit_after_check_moved_to_escrow_not_destroyed` because the
`README` path was absent after escrow reconciliation. The previous
`os.link()` restoration relied on hardlinks, which Android Termux Python
3.14/filesystems may not support. The corrected path reads and checks the
*opened*, descriptor-anchored escrow original, then uses `O_EXCL|O_CREAT`
through the pinned parent descriptor to restore a byte-identical file,
retaining the escrow original. A concurrent new file wins unchanged and the
original bytes remain available in escrow. Failed copies retain the escrow
original and produce a partial state for explicit review.

The mandatory test forces `os.link()` to be unavailable; a second test
creates a competing new file after the escrow rename, demanding both sets
of bytes survive without overwrites. The host-independent tests do not
substitute for a successful Termux execution receipt.

## Termux Python without `os.link` (follow-up, 2026-10-24)

Device run `67f3c95290fc4effb918441054e756f0` exposed a test-only assumption: `monkeypatch.setattr(os, 'link', ...)` fails before exercising escrow safety if the runtime does not export `os.link` at all. The regression now checks both the missing-attribute and present-but-refused hardlink cases without skips. Production FC-02 restoration already relies on descriptor-anchored, exclusive file creation rather than hardlinks. The observed device result is **failed / not installed**, not a qualification pass.
