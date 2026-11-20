# M4 merged-source audit and retained assurance matrix

Read-only audited baseline: `Mikeyphw/devtool` `main` commit
`e72869e5258030a78fac6e1551654bfdc758d7a4`, tree
`ce27966adc1cf4f0e349e8a626ecb2debba34adb`.
This audit concerns the source as observed at the cited baseline; it is not a
statement that the M4 changes have been applied or hosted-accepted.

| Roadmap requirement | M3 source status | M4 candidate implementation | Acceptance |
|---|---|---|---|
| Native transaction, exact source/tree, validator, commit, session | Present, `launcher_contract_v3.py`, `native_overlay_delivery.py`, `delivery_session.py` | Unchanged and reused | Native receipts, crash/recovery tests |
| Explicit non-force push, remote intent and read-after-write | Present, `delivery_github.py:push` | Scoped opt-in HTTPS App transport delegated to original `push` | No intent on denied auth; fast-forward/refusal checks |
| Draft PR, same-repo/fork and duplicate POST reconciliation | Present, `delivery_github.py:pr`, `delivery_provider.py` | Add separate App installation clients for base-write and fork-head-read | Same repo + fork negative permission tests, real approved PR |
| Required hosted workflows, exact PR SHA, attempt and policy | Present, `delivery_ci_policy.py`, `delivery_ci_registrar.py`, `observe_ci` | Renewable, repository-scoped App API client | Current run attempt and correct upstream policy |
| GitHub API credentials `auto/env/gh/none` | Present, `github_actions.py:resolve_token` | Add explicit `app` without changing `auto` | PAT/gh/SSH compatibility |
| App JWT, installation, membership, permissions and expiry | Absent | New `github_app.py` | Real App JWT and installation tests, scoped read/write |
| Secure App Git push | Absent | Ephemeral pipe-backed AskPass, origin unchanged, native Git push | Real Termux HTTPS push with secret-leak negative tests |
| Canonical parser | Present schema generated parser | Add delivery `--auth app`, push stays auto/app | CLI schema/help/completion regression |
| Opaque provider result never authorizes mutation | Present fail-closed native session | Keep separate approval; no automatic retry POST | 401/403/404/redirect/unknown-result negatives |
| Full repository regression | Separate manual 24-shard workflow | Leave unchanged | 24/24 shards + strict summary at new PR HEAD |

## Missing or deferred acceptance

- Real head/base installation grant cannot be simulated by a locally generated
  RSA fixture. A user-approved actual App installation and its repository
  selection is required.
- Git-over-HTTPS AskPass process inheritance and terminal log behavior need
  real Termux smoke, not only mocked-process unit tests.
- Native ZIP/PYZ parity, detached runtime exact pin, local commit adoption,
  PR remote proof and 24-shard summary require user worktree and GitHub hosted
  execution. This source-only archive is **not** that evidence.
- Device/release acceptance is separate; M4 does not automatically grant a
  GitHub App admin or delete/recreate privilege.

## Test maintenance

The candidate *adds* `tests/test_github_app_m4.py` and
`tests/test_delivery_m4_integration.py`, and retains the existing
`test_delivery_github`, `test_delivery_session`, `test_delivery_ci_policy`,
`test_delivery_cli_surface`, Actions provider, native v3 M3, and launcher suites.
No current gate is removed or bypassed to obtain green CI. Actual native and
hosted outcomes must be reported separately after execution.

## Consolidated Guardian validator receipt correction

This same M4 native overlay also corrects the earlier M3 committed-history
recovery mismatch: native hook receipts record `artifact-validate-script`,
not `artifact-validation-script`. `delivery_session.py` must accept only the
actual successful, unskipped child and must still read validation settings
from the `validation` manifest key (not `validate`). Existing M3 tests are
updated and expanded with real event, spoofed names, failed/skipped outcomes,
and invalid manifest-policy checks. No existing commit/history is rewritten.

Original Guardian recovery remains an independent post-merge acceptance gate.

## Consolidated DTB-014/015 source changes

This same native M4 overlay updates `.github/workflows/devtool-launcher-v3.yml` to include current hosted regression discovery and modifies `devtool/storage_layout.py` and
`devtool/actions_artifacts.py`. It adds one source-bound regression module
`tests/test_m4_storage_artifact_recovery.py` and the companion maintenance
document `docs/DEVTOOL_M4_BUGFIX_CONSOLIDATED.md`. Exact lock-only XDG
reconciliation never replaces real registry bytes; native verified ZIP adoption
never overwrites an existing archive or skips source identity checks. Existing
source security regression suites remain in the required validation plan.

DTB-006 already has tests-only support in current source; DTB-017 R07A is
not being weakened without an exact current-source reproduction.
