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

## Recovery of a pre-bound PLANNED artifact session

An earlier launcher may create a `PLANNED` durable delivery session already
bound to the correct artifact digest and resulting commit, but omit
`native_apply`. `adopt_applied` now reconciles this state through the existing
canonical native history/transaction/session authority. Recovery must first
verify the **unique** qualified history record, completed native transaction,
exact HEAD/parent/tree and changed-file inventory, original archive digest,
passed unskipped required steps, and identical existing overlay identity.
Only an unprogressed `PLANNED` session with **no remote intents, PR or CI** may
receive the missing `native_apply` evidence. Its session ID and overlay binding
remain unchanged; the journal update is atomic, audited and idempotent. Wrong
or ambiguous history, a failed/skipped step, a mismatched artifact, conflicting
journal receipt or remote progress fails closed with no second session or
Git replay. Existing v1/v2 session contracts remain supported.

A durable session remains bound to its **original exact commit**. If a later
native correction creates another commit on that branch, the earlier session
remains immutable historical evidence and cannot qualify/push the new HEAD.
The new commit needs its own separately verified native delivery session.
Neither this repair nor the original historical session automatically grants
push, draft PR, CI, device or release acceptance.

## Schema-v2 native child receipt compatibility for committed applies

Native Launcher v3.2.1 may persist a completed, source-bound artifact history
entry with `validation_status=ok`, but an empty `validation_plan`: the recorder
previously omitted `artifact-test:<id>` results from step collection despite
persisting them in the original `child_outcomes` list. Current recording now
includes those results as explicit validation-plan steps.

For an **already committed** schema-v2 required-tests-only archive (no restore,
build, project test or other validation surfaces), the existing canonical
`adopt_applied` verifier may accept the **unchanged** native child outcomes as
validation evidence if every required declared Python artifact-test ID matches
one distinct, successful, unskipped child outcome, the commit child succeeded,
and all children have clean outcomes. All other native receipt, transaction,
source parent/tree, inventory, archive hash, session and remote-intent guards
remain mandatory. This fallback does not write or rewrite artifact history;
the delivery session marks `validation_evidence_source=original-native-child-outcomes`
so that a recovered v2 receipt is never misrepresented as a v3 step receipt.
A missing, failed, skipped, duplicate, unexpected or unlisted test refuses.

The previously committed session `9b6de5c872b042d4b49b87f8178a727c`
is bound to `b1cca136879f220a5f5fe2caed5aec0d594ed345`. Never relabel it
as evidence for a later corrective commit. Native delivery and hosted CI must
be qualified at the eventual final PR HEAD through its own verified session.

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

## Source-bound native adoption before remote delivery

Native Launcher v3.2.1 now calls the existing `devtool.native_overlay_delivery`
`adopt-committed` authority after successful native apply and exact postimage
verification and **before** creating or remotely delivering a session. This
reuses Devtool's authoritative completed history, original artifact digest,
completed transaction, unskipped passed validation, source parent/tree, and
committed file inventory. It never generates a replacement history record.
A failed adoption is a post-commit recovery stop; `apply` must never be replayed.

The historical session `0a035632873e4fb3827ebc51ad3570e3` for commit
`53303e7bd4831d42fd65d18540bcdfc050228ef4` has an independently present
completed native history `ed6ec39021e7c90edc0d066dd7594728`, but is already
remote-progressed and lacks a bound `native_apply`. Existing guards correctly
refuse retroactive mutation of that session. Preserve its audit trail; qualify
subsequent source commits with new adopted sessions. Integrating the PR #36
delete-contract test/source into PR #35 does not assert Git ancestry or bypass
hosted exact-PR-HEAD/24-shard regression qualification. This is a content
integration only, not a Git merge or a release authorization.

## Committed hook output directories and Guardian recovery

Devtool's existing native `adopt_applied` authority now permits files changed
under explicitly declared `preflight`, `validation`, or `finalize` hook output
directories when the original native child hook succeeded without skipping.
It uses the same component-boundary rule as `devtool.hooks.isolation`, and
classifies directories from the authenticated Git trees at `HEAD` and `HEAD^`.
Inventory payload paths and file-scope hook outputs remain exact. Renames are
checked as separate delete and add changes. Any unlisted, prefix-lookalike,
root/traversal or symlink path fails closed. Full source parent/tree, payload
posthashes, unique original history, completed transaction and validation
proofs are unchanged. No legacy executable bootstrap was repackaged.

The externally reported Guardian v0.12.5 source commit `3159bb513fd7e1cbe0055c44a608624fd33dd6d6`
and original history `c9ad337cc8b864750f517927f4683cb0` are regression
context, **not acceptance evidence**. This Devtool PR does not open or modify
Guardian or invoke `recover-committed` there. After the merged Devtool
runtime is verified on the Guardian device, authorized recovery of its own
existing, source-qualified history must be performed separately and independently
confirmed. No overlay reapply, journal synthesis or original-commit rewriting.

## Canonical native validator child name (Guardian v0.12.5 receipt)

The embedded validation hook is recorded as `artifact-validate-script` by
`run_artifact_validation_script` and by the original native operation history.
During committed-history adoption, this precise successful, unskipped child
name is required before declared `validation.write_paths` can authorize
descendant outputs under a verified Git-tree directory. The previously used
synthetic spelling `artifact-validation-script` is **not** a valid success
receipt and must not grant authority. This aligns the scope guard with the
actual native execution and recorder, rather than rewriting earlier receipts.

This correction is limited to native adoption and current regression fixtures.
An unchanged Guardian commit, unique authoritative history, parent/tree and
postimage proofs, completed transaction, passed validation steps, safe scope,
and original remote approvals remain mandatory. Re-run only `recover-committed`
after the Devtool correction is merged; never reapply the Guardian overlay.

The canonical validator operation name is `artifact-validate-script`, while
the manifest policy key remains `validation`. These two identities are
intentionally distinct and independently validated during adoption.
