# RM-16 + Android Release Batch HF01 — behavioral promise audit

Base: clean **post-RM15-HF02** Git tree `557f6a39b587b6ac5c2d0737959b4ad5f580406a`. This rebases the paused RM-16 WIP after HF02 without requiring failed RM-13.

## RM-16 promises

| Promise | Evidence | Status |
|---|---|---|
| Typed Actions wrappers for plan, dispatch, watch, resume, artifacts | `wrapper_config` reviews exact operation/inputs/ref, forbids passthrough, requires explicit boolean consent and exact request IDs; `wrapper_resolution` lowers to canonical argv | Implemented, tested |
| Dispatch-once with opt-in resume | Filesystem lock and durable journal, source+workflow+ref+input digest; test proves second invocation issues zero POST, unknown response never triggers retry | Implemented, tested |
| Narrow Termux overview | JSON report + 40-column text view with source, auth, runs, failure classes, artifact status and safe next command; read-only live mode explicit | Implemented, tested |
| Capability inventory | `devtool capabilities --json` reports repo scope, target/job/workflow bindings, wrappers and local tools without dispatch | Implemented, tested |
| Shared ErrorInfoV2 | Distinguishes missing auth, missing workflow, unavailable remote, uncertain request, source drift and unverified artifact; includes safe target/node/path, receipt and next action | Implemented, tested |
| No scope-injecting shell wrapper | Typed argv, no shell commands, no raw passthrough, explicit ref and --yes | Implemented, tested |

## Additional small Android batch hotfix (bundled into RM-16)

| Promise | Evidence | Status |
|---|---|---|
| One GitHub workflow for BKX Embedded and Lite | `release-batch-plan/run` validates 2–16 explicit consumers sharing exactly one registered workflow and repository, then calls `unsigned_dispatch` **once** | Implemented, tested |
| Correct producer SHA (not Devtool HEAD) | Explicit `--source-root` for separate consumer checkout, committed clean SHA, remote ref checked before POST | Implemented, tested |
| No duplicate dispatch on uncertain retry | Batch always uses RM-16 `resume_if_existing=True`, journal/lock and remote correlation; unknown state blocks new POST | Implemented, tested |
| Separate artifact IDs and verification | `release-batch-check/import` requires explicit ID per consumer and one exact run/source SHA; uses each consumer's existing ACTIONS-10 manifest/package/member verification; preflights all before saving any APK | Implemented, tested |
| Independent LIFE local signatures | `release-batch-sign` requires immutable registry ID per consumer and same run, attempt, workflow and authorized certificate; preflights both before invoking existing signer twice | Implemented, tested |
| Strict consent | `release-batch-run`, `release-batch-import`, `release-batch-sign` require explicit `--yes`. Local batch signing never requires GitHub authentication | Implemented, tested |
| No qualification promotion or signed CI secrets | All results remain `qualified_release=false`; no new signing engine, GitHub signing secret, auto-latest-run selection or mixed producer receipt | Preserved, tested |
| Partial failure safety | A failed import/sign may leave earlier immutable receipts. Exception reports count and requires manual inspection; never declares batch complete or blindly retries. No cross-candidate rollback claimed | Implemented, bounded |

### Production prerequisites / external qualification

1. `config/android-consumers.json` must contain *actual* BKX Embedded and Lite contracts, with **identical workflow and authorized LIFE signer**, distinct artifacts/variants, and exact embedded-required/lite-forbidden rclone members. The fixture contracts in tests are not a production BKX registration.
2. The BKX GitHub workflow must build **both APKs in one invocation**, creating independent ACTIONS-10 two-member archives/manifests with the exact artifact names. This patch never generates or rewrites BKX's workflow.
3. A clean local BKX checkout must be passed as `--source-root` to dispatch. GitHub credentials are needed for dispatch/verified import, **not for local signing**.
4. Obtain the actual run ID, attempt, two artifact IDs and SHA from GitHub authoritative data; never guess "latest". Test runs use controlled mocks rather than BKX production artifacts.
5. Full live GitHub run, production LIFE signing, 16 KiB native alignment on real BKX APKs, and Skybind/device acceptance are **external gates**, not implementation claims.
6. A partial batch operation is not atomic across two immutable registry entries. The operator must inspect exact records before resuming missing operations, not rerun the whole batch blindly.

### Full remaining roadmap

- RM-13/R1: prior C/C++ Termux failure unresolved, not a dependency.
- RM-17: configuration migration and atomic single-repository overlay upgrades — next.
- RM-18: multi-repository coordination/recovery, **no extraction**.
- RM-19: **CANCELLED BY USER — no repository splitting/extraction**.
- RM-20: whole-repository **in-place** onboarding and affected-target inventory.
- Earlier RM-06–RM-09 engineering gaps remain open in improvements ledger, separate from RM-16.
