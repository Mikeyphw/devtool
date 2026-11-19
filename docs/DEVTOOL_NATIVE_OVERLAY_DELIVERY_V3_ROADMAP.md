# Devtool Native Overlay Delivery v3 — Implementation Roadmap

**Status:** Proposed; no repository changes or CI claims are implied by this plan.  
**Target:** `Mikeyphw/devtool` — current native overlay pipeline and self-executable `.pyz` profiles.  
**Goal:** Bring the Launcher v3.2 source-bound, end-to-end delivery experience directly into native overlay application without creating a second apply engine, session journal, Git/GitHub client, or CI engine.

## Architectural baseline

The merged Devtool code already owns manifest parsing (`devtool/artifacts.py`), executable overlay v2 classification and canonical bootstrap (`devtool/executable_overlay.py`), atomic overlay transactions and commit, executable validation plans (`devtool/native_validation_plan.py`), post-commit adoption (`devtool/native_overlay_delivery.py` and `devtool/delivery_session.py`), push/PR orchestration (`devtool/delivery_github.py` and delivery-session commands), and committed required-CI policy (`devtool/delivery_ci_policy.py`). Launcher v3.2 (`devtool/launcher_contract_v3.py`) coordinates many of these existing actions and adds the self-executable source/runtime contract.

**Design decision:** Introduce an *opt-in* manifest `delivery_contract` for delivery v3. Do not change the meaning of existing `validation`, `apply`, `inventory`, `repository`, or `executable_overlay` fields. An ordinary overlay remains an ordinary overlay unless it opts into this delivery capability. Existing ZIP, v1, v2 and immutable launcher artifacts remain byte-for-byte valid under their original contracts.

## Manifest design (proposed, not implemented)

```json
{
  "schema_version": 3,
  "type": "overlay",
  "base_contract_mode": "exact-tree",
  "base_commit": "<exact target pre-apply HEAD>",
  "base_tree": "<exact target pre-apply Git tree>",
  "repository": {
    "remote": "git@github.com:owner/repository.git",
    "branch": "feature/source-bound-delivery",
    "enforce_remote": true,
    "enforce_branch": true
  },
  "inventory": {"format": 1, "entries": ["<real format-1 inventory entries>"]},
  "validation": {"required": true, "tasks": ["<real bounded native task IDs>"]},
  "apply": {
    "atomic": true,
    "requires_clean_worktree": true,
    "commit": {"enabled": true, "strategy": "single", "message": "feat: source-bound delivery"}
  },
  "delivery_contract": {
    "version": 1,
    "runtime": {
      "compatibility": {"minimum": "3.2.0", "major": 3},
      "resolution": "trusted-verified-compatible",
      "verify": ["commit", "tree", "launcher-module-blob", "cli-schema-blob"]
    },
    "session": {
      "after_commit": "adopt-or-resume",
      "bind_to": ["artifact-digest", "transaction-id", "result-commit", "branch"]
    },
    "remote": {
      "base_repository": "owner/repository",
      "base_branch": "main",
      "base_sha": "<verified upstream PR base SHA>",
      "push": "requires-explicit-approval",
      "pull_request": {
        "mode": "requires-explicit-approval",
        "draft": true,
        "title": "feat: source-bound delivery"
      }
    },
    "ci": {
      "policy_path": "config/delivery-required-ci.json",
      "observe": "on-explicit-request",
      "exact_pr_head": true,
      "qualification": "required-workflows-only"
    },
    "recovery": {
      "after_commit": "resume-only",
      "unknown_remote_outcome": "read-only-reconcile"
    }
  }
}
```

**Important:** This is a *shape sketch*, not a valid complete manifest or currently accepted CLI schema. The real inventory and corresponding source/post hashes must be fully supplied; the `validation.tasks` values must be actual registered native tasks; permitted shapes will be formalized in Milestone 1. Existing v2 fields should not be duplicated inside `delivery_contract`. Source `base_commit` (pre-apply target) and `remote.base_sha` (upstream PR target) are distinct identities.

## Milestone 0 — Source audit and contract baseline

**Outcome:** One precise map of what already works and what is genuinely missing.

- Inventory the canonical native apply, artifact parsing, executable v1/v2 profiles, transaction receipts, delivery-session adoption, required-CI and CLI schema paths.
- Reconcile tests, CI workflows, documentation and test discovery against current `main` and the currently reviewed feature PR; record the exact SHA/tree before editing.
- Classify each proposed feature as **existing**, **integration only**, or **new validation/UX**. Preserve meaningful security and regression tests; consolidate overlapping tests and remove genuinely obsolete handoff-specific assertions only with equivalent current coverage.
- Record security invariants: target worktree is a clean named branch; verified origin and source/tree/blobs; artifact checksum/inventory; no implicit base changes; no reset, rebase or force push; no automatic clipboard; no full-screen host.

**Acceptance:** Published component/contract inventory and a test matrix mapping each invariant to at least one maintained current check. No code behavior changes in this milestone unless required to correct contract documentation.

## Milestone 1 — Versioned declarative manifest contract

**Outcome:** The native verifier can parse, normalize and explain v3 delivery declarations without performing mutations.

- Add the optional `delivery_contract.version=1` extension, with strict supported keys, types, bounds, and fail-closed unknown required capabilities.
- Bind runtime requirements, exact artifact/source identity, session policy, upstream base identity, remote approval boundaries, CI policy and recovery rules.
- Reuse existing root `repository`, `base_commit`, `base_tree`, `inventory`, `validation`, `apply` and executable-profile fields; disallow conflicting duplicate declarations.
- Provide normalized read-only `inspect`/`check` projections with an actionable reason for each refusal; never treat static-only checks, skipped tests or a missing CI policy as passed execution.
- Keep v1/v2 acceptance unchanged. New v3 fields are not silently ignored by old runtimes.

**Acceptance:** Positive and adversarial parser tests (unknown fields, incompatible versions, malformed SHA, path traversal, aliasing, conflicting declarations, missing executable validation, wrong branch/origin, CI policy mismatch). Existing v1/v2 fixtures still pass unchanged.

## Milestone 2 — Verified runtime and preflight parity

**Outcome:** Native apply can select the latest compatible *trusted* Devtool runtime under the same safety properties as the v3.2 launcher.

- Resolve a verified Devtool >=3.2 runtime from explicitly trusted worktrees/cache; allow a verified detached-HEAD runtime while requiring a named target branch.
- Attest runtime commit, tree, launcher module blob and CLI schema blob before importing or invoking native execution; validate compatibility independently of user-controlled PATH or working-directory aliases.
- Preserve separate runtime and target worktrees; reject drift/dirty target state and incompatible runtime contracts before local mutation.
- For `.pyz`, require **native preverification before first execution** of any untrusted archive. Do not rely on the archive's embedded `__main__.py` to prove its own trust.
- Use real stdout/stderr, supported plain/native terminal options, no Textual/fullscreen apply host, no clipboard copying and no invented environment knobs.

**Acceptance:** Positive native check with clean exact source; negative tampered runtime/source/module/schema, detached target, dirty worktree, symlink/traversal, untrusted bootstrap, runtime drift, and wrong-origin cases fail before mutation.

## Milestone 3 — Atomic apply, commit and post-commit session continuity

**Outcome:** A single native apply may complete *local* transaction, executable validation, commit, and durable session adoption; recovery never replays an already committed overlay.

- Reuse the native transaction, validation and commit engine. Do not duplicate extraction, rollback, validation scheduling or Git mutation logic.
- After a qualified commit, invoke the existing `adopt-committed` authority automatically from the native coordinator (for ordinary ZIP and verified executable PYZ alike).
- Journal and bind artifact SHA, native history/operation/transaction IDs, validation outcomes, source HEAD/tree, result parent/tree, exact changed paths/blobs and resulting commit to a **single existing delivery session**.
- If post-commit adoption is interrupted, recover the committed receipt read-only and resume adoption; do not roll back committed work or rerun apply.
- Keep `native_apply.state=COMMITTED` distinct from the remote delivery session `stage=PLANNED`.
- Refuse ambiguous/duplicate/conflicting sessions, unresolved native transactions, failed/skipped required checks, and changed HEAD after commit.

**Acceptance:** Crash/fault injection before apply, during validation, before/after commit, during adoption, and on repeated invocation; exactly one session for one qualified commit; correct rollback for precommit failures; no extra commit or reapply after commit; ordinary ZIP and PYZ contract parity.

## Milestone 4 — Explicit remote push/PR and exact hosted CI qualification

**Outcome:** Native delivery can continue a committed session through approved push, draft PR and optional CI observation, using existing remote journals.

- Remote mutations require **separate explicit operator approval**; a manifest alone cannot authorize push, PR, force push, merge, release or Actions dispatch.
- Use existing source-bound native delivery-session push/PR operations, journaling remote intent before network writes; verify origin/fork identity, upstream base repo/ref/SHA, exact local HEAD and server-returned PR head/base.
- Default to draft PR. Never force push, rebase, reset, auto-merge, silently switch upstream, or POST twice after an uncertain response.
- On push/PR ambiguity or connection failure, use read-only GitHub reconciliation and preserve the existing session for operator approval; do not infer success.
- Load required workflows from committed `config/delivery-required-ci.json` for the correct base repository/commit (including fork workflows). Verify exact PR HEAD, event, workflow ID, run attempt, check conclusion and branch/ruleset applicability when visible.
- When the policy is absent, changed or unavailable, distinguish **NOT QUALIFIED**, **BLOCKED** and **WAITING** from **PASSED**; never infer formal qualification from arbitrary green checks. Full 24-shard repository regression remains a separately requested acceptance scope and is not run by default on Termux.
- Expose native `status`, `resume`, `deliver`, `ci`, `report` paths. After commit, use these rather than re-running `apply`.

**Acceptance:** Real hosted PR at the exact actual HEAD, exact required CI checks with bounded polling, fork/same-repo identity tests, duplicate POST/fork hijack/drift/CI registration gap/failed, cancelled and skipped negative cases, and durable recovery after remote uncertainty. Release and device acceptance stay distinct.

## Milestone 5 — Migration, UX and launcher-optional cutover

**Outcome:** Native overlay delivery is the default *first-class* path for new packages while launcher v3.2 remains a compatible optional wrapper.

- Extend native CLI and canonical generated schema/help/completions with a minimal, documented `check → apply → status/deliver/ci` experience. Define supported names from the real parser; do not assume proposed options already exist.
- Support both ordinary `.zip` and verified `.pyz` without changing executable v1/v2 bootstrap bytes; maintain read-only inspection of older immutable artifacts.
- Implement a stable terminal-native summary: source identity, targeted tests requested/executed/skipped, native transaction and commit, session ID, PR URL and actual HEAD, CI scope, and unresolved steps. Use supported no-clipboard/no-fullscreen controls.
- Update docs, examples, test discovery, workflow path filters and CI requirements. Remove only genuinely obsolete launcher-specific duplication when equivalent native checks cover the same risk; do not remove a failing test to make CI green.
- Validate one complete source-pinned ZIP route and one preverified self-executable PYZ route through local native checks, Termux device smoke and hosted PR checks. Do not claim device/hosted results from Linux-only tests.
- Document precise compatibility and retirement criteria. Keep Launcher v3.2 usable until all native acceptance gates have passed; avoid abrupt deprecation or forced repinning.

**Acceptance:** Same native outcome and evidence for ZIP/PYZ, round-trip resume after interruption, no regression for legacy archives, no automatic clipboard/fullscreen behavior, and a complete auditable acceptance report.

## Validation strategy and required gates

| Lane | Runs where | Scope | Required evidence |
|---|---|---|---|
| **Local/Termux gate** | Native target runtime | Bounded manifest-declared smoke, file/source integrity, preflight, transaction/recovery | Native transaction ID, executable outcome, commit/session receipt; no full regression |
| **Hosted focused regression** | PR workflows | Parser, compatibility, source, runtime, session and GitHub negative cases; interactive PTY/storage integration | Exact PR HEAD + current run attempts/jobs and passing test outcomes |
| **Full repository regression** | Explicit hosted acceptance request | 24-shard (or current canonical) suite with source-bound summary | Separate correlated request and fail-closed aggregate; not implied by PR checks |
| **Device/release** | Actual device / release pipeline | Termux behavior, signing, deployment, release-specific gates | Separate evidence; never inferred from local or hosted checks |

**Global refusal gates:** malformed or untrusted executable; missing/mismatched SHA or blobs; incompatible runtime; dirty/unnamed target; source drift; conflicting session; failed or skipped required executable validation; ambiguous remote mutation; wrong PR HEAD; missing required CI policy when formal qualification is requested; skipped or missing required workflow; release claims without independent evidence.

## Delivery format for each milestone

Deliver **one consolidated native, immutable, source-pinned overlay per milestone**, not stacked historical Pxx/HFxx hotfixes. For each deliverable report: repository/branch; starting and resulting HEAD/tree; launcher/runtime identity; exact file inventory; changed/consolidated/deleted checks and reasons; local/native validation; durable session; pushed PR and exact hosted CI; Termux executable command; remaining acceptance and limits. Use native recovery/session continuation after commit, not manual Git patches or reapplication.

## Definition of done

- Native ZIP/PYZ delivery can be completed without a launcher-specific orchestration path.
- Existing native engines and authoritative journals are reused, not duplicated.
- Source, runtime, artifact, validation, commit, session, PR and CI evidence remain independently verifiable.
- Push/PR/CI remain explicit, resumable and safe under partial failure.
- Old overlays and launcher v3.2 continue working without silent repinning.
- Termux performs only bounded smoke validation; hosted regression and on-demand full suites preserve security and regression assurance.
- All claimed acceptance is tied to real exact-HEAD receipts, with device/release claims kept separate.
