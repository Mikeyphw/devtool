# C3 — Immutable launcher compatibility and current-source regression gates

**Milestone:** G0 / C3; **repository:** `Mikeyphw/devtool`; **branch:** `feature/g0-native-validation-phase-contract-20261117`.

## Compatibility policy

- **Already published v3.1/v3.2 ZIP/PYZ files are immutable.** Their manifest, bootstrap bytes and source/runtime pins must not be re-signed, re-pinned, rewritten or silently upgraded. Packages with no `devtool.bootstrap_profile` continue to use and verify the original byte-for-byte `bootstrap_content` contract. Ordinary ZIP overlays are handled by Devtool's native artifact verifier, not by renaming them to PYZ.
- **New version-aware launchers** declare `devtool.bootstrap_profile: native-terminal-v1` together with `runtime_compatibility` and the exact immutable source SHA, tree, launcher module Git blob and CLI-schema blob. Their bootstrap only verifies candidate runtime Git identity/ABI and forwards to `devtool.launcher_contract_v3.main`; it contains no shadow overlay engine, GitHub API engine, PR mutation or CI-dispatch logic. Compatibility still requires a trusted Git-origin and independently verified current runtime, and never silently changes the manifest's original provenance.
- New bootstraps no longer synthesize `DEVTOOL_EXECUTION_PRESENTATION` or `DEVTOOL_JOB_UI`. Devtool's C1 CLI owns ordinary native-terminal presentation; the launcher uses the supported `DEVTOOL_TEXTUAL_APPLY_HOST=0` and `DEVTOOL_TEXTUAL_EXECUTION_HOST=0` host boundaries only. No clipboard writes occur unless the operator explicitly requests them, and native invocation passes `--no-clipboard`.
- Profile mismatch, downgraded/unknown profile, ABI-major mismatch, tampered PYZ bootstrap/payload, wrong source Git identity, or unsatisfied runtime compatibility must fail before native mutation. Native atomic overlay validation, commit, durable sessions, approval-bound remote push/PR and Actions observation remain with their existing authorities.

## Audit of historical gates and decisions

| Existing check / surface | Current finding | C3 action and rationale |
|---|---|---|
| `tests/test_launcher_contract_v3.py` historical UI assertion | Assumed two deprecated presentation selectors were *always overwritten* even after C1 changed the CLI default | **Updated**, not deleted: asserts actual native child Textual-host disablement while preserving the operator's explicit settings. All remaining source, signing, immutable-package, replay and terminal tests retained. |
| `tests/test_textual_github_ui_contract.py` launcher UI assertion | Same obsolete mandatory environment-rewrite expectation | **Updated**, not deleted: require no synthesized selectors, two supported child controls and no auto-clipboard. GitHub Textual projection and authorization tests retained. |
| `tests/test_launcher_runtime_compat.py` versioned roundtrip fixture | Built the bootstrap from a runtime dictionary rather than the resulting immutable manifest (now carrying a bootstrap profile) | **Updated** to construct from the verified manifest; published legacy no-profile fixtures remain independently tested. |
| `.github/workflows/devtool-delivery-s7-p02.yml` | Existing always-on PR job already checks adoption, canonical CLI and generated parser surface | **Consolidated additional current C3 behavioral coverage into this existing job**. No extra workflow, job or runner added; source-bound v3 launcher tests, runtime compatibility and negative tests now run here. Previous tests remain. |
| `.github/workflows/devtool-delivery-s7-p06.yml` | Distinct read-only, credentialed cross-consumer evidence and artifact receipts | **Retained intentionally.** A legacy label alone does not justify dropping a provider-backed security gate. Removing it would need independently proven replacement and current branch-protection/ruleset review. |
| `.github/workflows/devtool-delivery-s7-p07.yml` | Distinct provider-backed integration and historical consumer qualification | **Retained intentionally** for the same reason; full coverage and receipts are not equivalent to local source-only tests. |
| Existing A/B/C1/C2 native negative suites | Cover package classification, dirty worktrees, ambiguous sessions, remote intent replay, fork namespace, required CI/run artifacts and Textual read-only projections | **Preserved**, no broad deletion of version-/handoff-named safety checks. The new C3 fixture covers real current behavior rather than checking an obsolete handoff label. |

No genuinely obsolete GitHub workflow can be safely deleted without confirming branch rulesets and replacing its live-provider evidence. C3 optimizes the *current always-on* check by extending its existing selection rather than starting another workflow. If branch rulesets become available, separate retirement review can remove only redundant gates with equivalent modern hosted proof.

## Validation and acceptance

The C3 overlay is exact-source-bound to the C2 PR HEAD. Its native validator runs current C3/launcher and A/B/C security regression selections in the runtime's supported Python environment, without invoking Gradle or the manual 24-shard workflow. Actual Termux `validate-overlay`/`overlay check`/atomic apply, durable session adoption, approved PR publication and hosted checks remain mandatory acceptance steps. By operator instruction the full 24-shard regression is **deferred until C1+C2+C3 have all committed**, and no run is triggered by C3 build or delivery tooling.

`main` may advance independently of this feature branch. A future merge/release must account for intervening `main` launcher changes without reset/rebase/force-push or automatic merge; this package pins the target C2 source commit, **not** a permanent Devtool launcher version or `main` commit.
