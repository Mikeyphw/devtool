# Native Delivery v3 · M3 committed apply and session continuity

**Scope:** opt-in overlay manifest `schema_version=3`, verified source/runtime
preflight from M2, and *local* transaction-to-session continuity only.
Release, device, remote push, PR and hosted CI are **not** authorized by
this milestone. Existing v1/v2 ZIP and executable overlay bootstrap bytes,
legacy commit/apply and delivery session APIs remain unchanged.

## Canonical authority sequence

1. Native Devtool validates a complete v3 overlay and its executable required
   checks using the existing artifact/transaction/validation engines.
2. Native apply commits the exact source-bound mutation, closes its transaction,
   and saves the **native applied-commit history** with transaction and
   validation-step receipts. No M3 code applies, patches, rolls back or commits.
3. For a single successful v3 `apply-overlay`/`ao` invocation, the native CLI
   invokes `native_delivery_continuity.adopt_after_apply` **after history**.
   The new coordinator checks commit-result identity and unresolved native
   transactions, then delegates exclusively to `delivery_session.adopt_applied`.
4. Canonical adoption enforces one qualified native history/transaction, clean
   branch at the resulting HEAD, source parent/tree, committed changed paths and
   SHA-256, artifact digest, unskipped required validation steps and one durable
   delivery session. For v3 it additionally stores the exact resulting commit,
   tree, parent, changed paths/Git blobs and passed step outcomes in
   `native_apply` without altering legacy v1/v2 record shapes.
5. `native_apply.state=COMMITTED` and `stage=PLANNED` are separate facts. M3
   grants **no** remote authorization or CI qualification. An already-progressed
   existing session may retain its later remote stage; it is never reset.

## Interrupted post-commit adoption

Native CLI post-commit failures are reported as
`NATIVE_M3_POSTCOMMIT_ADOPTION_PENDING`, **not** as a rollback or a reason to
rerun `apply`. The existing Git commit may already be durable. Inspect the
native transaction/history and recover by calling the canonical session
adoption authority from a trusted Devtool runtime:

```sh
python3 -B -m devtool.native_overlay_delivery recover-committed \
  --repo "$TARGET_WORKTREE" --overlay "$ORIGINAL_ARTIFACT_PATH"
```

The artifact archive may have been cleaned after native apply; history is
retained and is authoritative. Recovery reads the committed current HEAD and
unique history record, verifies there are no unresolved native transactions,
re-attests the M1 declaration and source-bound native receipt, then **reuses**
the session or adopts once. It never replays a transaction or creates a second
Git commit. If a previous session has unconfirmed remote intent, source drift,
conflicting upstream identity or a corrupt journal, recovery refuses instead
of silently retrying or changing the session.

The existing immutable executable v2 bootstrap calls `adopt-committed`
post-apply. This command now detects an already committed v3 manifest and
reconciles the exact pinned base repository/SHA through the same native
authority. Explicit contradictory `--base`, `--base-repo`, `--head-repo`, or
`--base-sha` arguments are rejected **before** session mutation. Legacy
v1/v2 behavior remains as before; the executable adapter itself is not
modified or repackaged.

## Limitations and acceptance

- A v3 overlay must request actual native commit + required validation.
  Deferred or failed validations cannot qualify until their existing native
  finalization succeeds; this milestone does not invent a second deferred
  validation orchestrator.
- Only ordinary single-file overlay entrypoints are automatically adopted;
  atomic multi-overlay bundles and resumable validation remain under existing
  native transaction semantics, with no false M3 success.
- M3 does not add push/PR/CI automation; M4 will reuse the existing native
  delivery session commands and committed required-CI policy.
- Tests use real temporary Git repositories and mocked persisted native history
  to cover exact commit/session idempotency, removed archives, failed/skipped
  checks, pending transaction, wrong origin, ambiguous sessions, changed HEAD,
  and legacy opt-out. The actual native transaction + hosted exact-PR-HEAD
  checks must be independently observed before final acceptance.
- Termux must use the supported native stdout/stderr, no full-screen hosts,
  and `--no-clipboard`; CI and device/release evidence remain distinct.
