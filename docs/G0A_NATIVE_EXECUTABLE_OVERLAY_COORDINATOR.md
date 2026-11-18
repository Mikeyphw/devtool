# Native executable overlay and delivery continuation — Milestone A

Roadmap authority: `DEVTOOL_NATIVE_OVERLAY_DELIVERY_ROADMAP.md` (2026-11-17).
Target for this source-bound package: `Mikeyphw/devtool`, PR #29, HEAD `325737fc5904bc1155b077d41c429c7a71eb9ec7`, tree `8ea1d0a8c48f6fa9034b71037c343d426feb8f75`.
These SHA values identify the immutable **target source** of this one artifact, not a globally pinned Devtool launcher. Always verify the latest compatible launcher from `main` before a new delivery.

## A1 — Native executable ZIP/PYZ classification

Executable overlays must declare `executable_overlay.profile` and a versioned checksummed bootstrap. New producer packages use `devtool.executable-overlay.v2`; the previously published v1 bootstrap remains byte-for-byte unchanged and is accepted by its original contract. Neither a `.pyz` extension alone nor a root `__main__.py` alone opts an archive into this profile. Ordinary ZIP overlays may still modify repository `__main__.py`.

V2 requires the canonical `__main__.py` adapter, exact-tree `base_commit` and `base_tree`, enforced origin and named branch, a complete format-1 inventory, and exactly corresponding `touched_paths`, `source_sha256`, and `expected_post_sha256`. The native artifact verifier checks the manifest and the bootstrap SHA-256 and size. It rejects undeclared executables, path aliases/traversal, symlinks, duplicates, excessive decompression, and inventory/metadata collisions. Explicitly inventory-owned `.devtool-artifact/validation/` files remain repository payload; executable bootstrap members do not.

The same classified member map is reused by native validation, preview/diff, extraction, the lifecycle planner, inspection, and patch-equivalence. An executable package is not silently rebased or rewritten into an ordinary ZIP. Nested bundles validate individual member overlays through the same authority. ZIP checksums establish integrity, **not** author/publisher trust.

**Do not directly execute an untrusted PYZ.** Any Python archive executes its `__main__.py` before the native verifier can inspect it. Prefer `devtool validate-overlay PACKAGE` and `devtool overlay check PACKAGE` from a trusted runtime before any direct execution. Once preverified and trusted, v2 supports:

```sh
python3 PACKAGE.pyz check --repo TARGET
python3 PACKAGE.pyz apply --repo TARGET --yes
```

The thin adapter delegates preflight and atomic apply/validation/commit to **native Devtool** using real subprocess stdout/stderr. Its v2 apply path then calls `devtool.native_overlay_delivery adopt-committed` to adopt the successful native receipt. It does not implement GitHub mutation, rollback, a second transaction engine or remote workflow dispatch. It does not enable fullscreen/Textual apply hosts or clipboard copying.

## A2 — Validation plan and evidence

`devtool.native_validation_plan.normalize()` is read-only. It normalizes declared executable validation policy without choosing or running its own test runner. Exclusive `validation.tasks` cannot also promise script/tests/refs/builtin phases; implicitly defaulted legacy phases are **not** reported as executed in a tasks plan. Required executable validation needs real checks; optional-only tests cannot qualify it. Static-only requires an explicit justification and all built-in execution phases explicitly disabled; static-only is **not** a pass of runtime validation.

Native Devtool retains hook SHA, paths, timeout and write protections. Adoption of a required validation artifact requires a completed native transaction and at least one real, passing, unskipped `validation_step_outcomes` entry; a positive summary cannot conceal skipped or failed steps. This is a projection of native evidence, never a separate test engine.

## A3 — Native delivery checkpoints, post-commit recovery

Devtool remains authoritative for `validate-overlay → overlay check → atomic apply → validation → commit/rollback`. The existing private delivery-session journal and Actions dispatch journal remain authoritative for push, PR and CI. Native post-commit adoption verifies the original artifact path and digest using immutable history (including when Devtool deleted the consumed archive), origin, named branch, clean checkout, exact commit parent/tree, changed-file inventory and payload hashes.

An adopted session stores the native `history_id`, `operation_id`, `transaction_id`, validation state, source tree and artifact identity inside the **existing** delivery-session record. Native status and resume expose a read-only `native_apply.state = COMMITTED` while the separate remote delivery stage can still be `PLANNED`. Repeated adoption reuses the same journal; exactly one existing planned session for the same commit can be reconciled. Conflicting/ambiguous sessions, unresolved remote intent, source drift, corrupt records, skipped required checks and incomplete native transactions are refused. No package is reapplied after its committed receipt is adopted.

For an ordinary ZIP package, run native check/apply followed by `python3 -B -m devtool.native_overlay_delivery adopt-committed --repo "$REPO" --overlay "$ORIGINAL_ARTIFACT_PATH" --base main`. The archive may already have been removed by successful Devtool cleanup. Afterward use native `delivery session status/resume/push/pr/ci` and `actions` commands. Remote mutations always need explicit authorization and independent hosted verification.

## Current evidence and outstanding qualification

Local Python regression and isolated native PYZ direct-execution tests cover positive/negative classification, source hash binding, v1 compatibility, native apply/validate/commit/cleanup, native transaction provenance, session reuse and replay refusal. Native production repository apply, GitHub push/PR reconciliation and the 24-shard full regression **for the resulting Milestone A commit** are still pending. The earlier 24-shard success on R4 does not qualify an uncommitted R3 change.

No local Android Gradle, device, release or fork-to-upstream qualification has been performed. Milestone B's fork provider and separate hosted CI scopes, Milestone C's operator UI integration, and G4 post-merge qualification remain separate roadmap milestones. Crash injection at every stage and terminal/device evidence beyond the isolated tests must not be inferred from passing unit tests.
