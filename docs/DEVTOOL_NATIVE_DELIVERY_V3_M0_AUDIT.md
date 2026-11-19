# Native Overlay Delivery v3 — M0 source and contract baseline

**Milestone:** M0 — source audit and contract baseline (documentation only)  
**Repository:** `Mikeyphw/devtool`, GitHub origin  
**Audited reference:** `main@8c5ab9bf050f9aaba5d181d0a6819a8e1e0b28a9`  
**Audited Git tree:** `740cc6d1127626ed174595ce2de2d74ddd1b5c5c`  
**Audit observation:** 2026-11-18 / 2026-11-19 UTC  
**Concurrent unmerged work:** PR #31, `feature/targeted-validation-postmerge-20261118@c1ba333798cd4886c1c5a7e752d552fb02837249` (open when audited; not part of main).

This inventory records inspected *source contracts*, not a claim that the full test suite, native apply, Termux/device, or hosted checks have run. Exact M0 delivery and acceptance must be reverified against the resulting feature commit and PR HEAD. The companion [`DEVTOOL_NATIVE_DELIVERY_V3_M0_TEST_MATRIX.md`](DEVTOOL_NATIVE_DELIVERY_V3_M0_TEST_MATRIX.md) maps security invariants to maintained tests and CI. The proposed roadmap is [`DEVTOOL_NATIVE_OVERLAY_DELIVERY_V3_ROADMAP.md`](DEVTOOL_NATIVE_OVERLAY_DELIVERY_V3_ROADMAP.md); its v3 manifest sketch is **not** an accepted schema.

## Canonical component/authority inventory

| Stage and boundary | Source of authority in audited `main` | Observed contract / handoff |
|---|---|---|
| CLI/parser and generated schema | `devtool/cli.py`, `devtool/core_parser.py`, `devtool/core_cli_schema.json` | Native `validate-overlay`, `overlay check`, `apply overlay` and `delivery session` commands; generated schema must remain synchronized with parser. Do not invent new flags or assume proposed commands exist. |
| Artifact verification | `devtool/artifacts.py`, `devtool/artifact_base_contract.py`, `devtool/artifact_trust.py` | Explicit source/branch/origin/base-tree, inventory, checksum and metadata validation; `_schema_version` currently rejects versions >2. No `delivery_contract` v3 recognition at this source. |
| Executable ZIP/PYZ | `devtool/executable_overlay.py` | Explicit profiles `devtool.executable-overlay.v1` and `.v2`; canonical checksummed adapter and complete v2 source/post inventory. Ordinary ZIP may contain a repository `__main__.py`. A PYZ must be preverified by trusted native Devtool before executing unknown `__main__.py`. |
| Executable native validation | `devtool/native_validation_plan.py`, `devtool/launcher_validation_contract.py`, `devtool/validation_*`, `devtool/artifact_tests.py` | Required executable evidence vs explicitly unqualified static mode; `validation.tasks` is exclusive; manifest-disabled built-in phases remain disabled. Native runner owns execution/outcomes. |
| Transaction and commit | `devtool/transactions.py`, `devtool/transaction_worktree.py`, `devtool/cli.py`, `devtool/runners.py` | Existing atomic transaction, reservations, rollback, commit receipts and pending-validation recovery; never implement an alternate Git mutation engine. |
| Native postcommit adoption | `devtool/native_overlay_delivery.py`, `devtool/delivery_session.py` | `adopt-committed` verifies qualified history, completed transaction, original archive digest (where retained), clean repo, commit parent/tree and exact changed payloads; idempotently creates/reuses one existing durable session. |
| Direct PYZ continuation | `devtool/executable_overlay.py` | Executable v2 bootstrap already invokes native adoption after successful native apply; ordinary ZIP still documents explicit adoption. Do not reimplement adoption at M3. |
| Delivery status/recovery | `devtool/delivery_session.py`, `devtool/delivery_cli.py`, `devtool/operator_status.py` | `native_apply.state=COMMITTED` is independent of remote `stage=PLANNED`; resume is read-only inspection and does not replay apply/push/PR. |
| GitHub push and PR | `devtool/delivery_github.py`, `devtool/delivery_provider.py`, `devtool/github_actions.py` | Explicit approval, intent-before-write, nonforce push, exact fork/base/HEAD verification and read-only reconciliation of uncertain operations. Draft PR default. |
| Required CI qualification | `devtool/delivery_ci_policy.py`, `devtool/delivery_ci_registrar.py`, `devtool/delivery_qualification.py`, `config/delivery-required-ci.json` | Committed policy, numeric workflow IDs, event/run attempt/head checks, fork upstream policy and ruleset evidence when available. Missing/skipped/unregistered runs are not passes; full 24-shard regression is separate and on demand. |
| Launcher/runtime compatibility | `devtool/launcher_contract_v3.py`, `devtool/launcher_runtime_compat.py` | Trusted runtime selection, independent target/runtime roots, exact SHA/tree/module/schema attestations and compatible 3.x minimum/major. Verified detached runtime may be used; target branch must be named and clean. |
| Terminal, clipboard, and output | `devtool/native_terminal_policy.py`, `devtool/launcher_contract_v3.py`, `devtool/clipboard.py`, `devtool/delivery_rich.py` | New `native-terminal-v1` bootstrap does not synthesize the older presentation/job variables; it disables supported Textual hosts and uses native `--no-clipboard`. Legacy immutable bootstrap bytes must remain unchanged. Machine output is plain, TTY Rich is conditional. |
| Persistent state | `devtool/storage_layout.py`, `devtool/xdg.py`, `devtool/transactions.py`, `devtool/delivery_session.py` | External durable XDG/legacy authority and writer fence; source/worktree state is not disposable. Open PR #31 proposes validation storage/target-selection changes, not yet baseline. |

## M1–M5 implementation classification at this exact baseline

**Existing (reuse without duplication):** artifact SHA/source/branch verification; v1/v2 executable classification; native validation authority and receipt; atomic apply/commit/rollback; native v2 PYZ postcommit adoption; durable delivery session and reconciliation; authorized GitHub push/draft PR; committed CI policy and observation; launcher 3.x runtime compatibility; supported terminal and clipboard controls.

**Integration only (new wiring over existing engines):** automatic adoption for *ordinary* ZIP from the native apply coordinator; unified ZIP/PYZ postcommit recovery; native delivery CLI continuation; runtime selection/preflight on the native side; approved remote stages and CI continuation invoked from the native coordinator. Preserve one journal per authority and use `status`/`resume` after commit, never reapply.

**New validation/UX (not yet implemented):** schema v3 opt-in `delivery_contract` with strict supported fields and actionable read-only normalization; native source/runtime verification before first execution of an untrusted PYZ; complete crash/fault-injection parity; unified native summaries and versioned CLI schema/completions; end-to-end device/hosted ZIP/PYZ acceptance. These are milestones M1–M5, **not M0**.

## Contract boundaries and source-document reconciliation

1. **Do not accept the proposed manifest in M0.** In `devtool/artifacts.py`, `_schema_version` enforces a maximum of 2. The roadmap's schema 3 example is a shape sketch and requires parser, verifier and backwards-compatibility work in M1.
2. **Do not execute an unknown PYZ to verify it.** `__main__.py` executes first. Native `validate-overlay`/`overlay check` from a trusted Devtool runtime must preverify an untrusted executable archive before the archive is invoked. A locally built trusted launcher package is a distinct, source-attested profile.
3. **Do not conflate two bootstrap families.** `devtool/executable_overlay.py` v1/v2 are native *executable overlays*; `devtool/launcher_contract_v3.py` produces a separate immutable launcher PYZ. Both delegate actual mutation to native Devtool; neither is an independent overlay engine.
4. **Do not conflate source and upstream base identities.** `base_commit` binds the target preapply commit, while a delivery-session `base_sha` binds the PR upstream base; same-repository and fork admission are distinct.
5. **Do not conflate local success with remote success.** Native transaction and qualified validation are required for adoption. PR head and hosted workflows must be verified at the actual server SHA. Release, device and 24-shard full regression need separate evidence.
6. **Current launcher documentation contains historical descriptions.** `docs/DEVTOOL_LAUNCHER_CONTRACT_V3.md` describes earlier `--static-validate`/environment behavior. The current behavior in `devtool/launcher_contract_v3.py` uses manifest-owned full validation (does not inject `--validate`), preserves explicit old presentation selectors for new-profile child environments, and disables Textual hosts. The v3.2 and G0C3 documentation is the more precise compatibility reference. M1/M5 should consolidate the narrative without silently rewriting published bootstraps.
7. **Open PR #31 is not merged evidence.** Its proposed automatic targeted-test selection, validation-session XDG redirection changes and expanded workflow path filters must be reviewed after merge, then M0 matrix/source pins re-evaluated before any M1 change. Preserve the merged PR #29 phase-disabled behavior in all cases.

## Test discovery, workflows and retirement decisions

At the audited tree, the recursive GitHub tree had **2,184 entries**, **714 `tests/**/*.py` files** and **46 `.github/workflows/*.yml` files**; the tree API returned `truncated=false`. These are inventory counts, not test execution counts. Candidate relevant suites include `tests/test_g0a_executable_overlay.py`, `tests/test_g0a_native_delivery_coordinator.py`, `tests/test_g0b_delivery_fork_and_provider.py`, `tests/test_g0c3_launcher_compatibility.py`, `tests/test_launcher_validation_delivery.py`, `tests/test_delivery_ci_policy.py`, `tests/test_validation_manifest_skip_contract.py`, and the canonical artifact/transaction/launcher suites. See the companion matrix.

Current named workflows include `.github/workflows/devtool-launcher-v3.yml`, `.github/workflows/devtool-delivery-s7-p02.yml`, `devtool-delivery-s7-p06.yml`, `devtool-delivery-s7-p07.yml`, and the **manual** `.github/workflows/devtool-full-repository-pytest.yml`. The launcher workflow has a `pull_request.paths` filter and may not run for docs-only M0 changes; a missing run is **not success**. The S7 P02 workflow has a general `pull_request` trigger and runs current CLI/launcher checks. The 24-shard workflow is manual and not a release gate. Required workflow IDs currently committed under `config/delivery-required-ci.json` are .NET-oriented; applicability to the actual PR and GitHub branch rules needs hosted verification before formal qualification.

**Retirement decision for M0: none.** Although S7/G0/test phase names are historical, current tests protect source integrity, fork/PR identity, transaction replay, validation truth, terminal compatibility, and provider behavior. No overlapping live-provider workflow may be removed solely on naming or an offline comparison. Before later consolidation: inspect path triggers, branch/ruleset requirements and current hosted success at the exact PR HEAD; replace obsolete assertions with equivalent current behavioral and negative coverage, then delete only proven duplicates. No test was deleted or disabled by M0.

## M0 acceptance and carry-forward

- **Deliverable:** this source inventory, proposed roadmap, and the companion invariant/test/CI matrix; one consolidated source-bound, documentation-only native artifact for a named feature branch.
- **Required local/native evidence when applied:** exact initial commit/tree, artifact checksum and inventory, native preflight and executable-validation outcomes, committed resulting SHA/tree, transaction/history ID and durable session ID; verify no skipped required steps.
- **Required hosted evidence:** PR head SHA, actual matching checks, required policy applicability and successful current attempts; a green source-only check does not imply device or release acceptance.
- **Not undertaken by M0:** manifest parser changes, Git mutation engine changes, wholesale gate deletion, release/Android qualification, automatic merge, or running the 24-shard full regression.
- **Fail-closed rule:** if current `main`, expected branch, origin, ZIP/PYZ profile, runtime identity, or a required check differs, create a new reviewed source-bound artifact; do not repin or reapply the original immutable package.
