# ACTIONS-A04 promise audit and generator hotfix

## Scope and evidence

Audited the consolidated ACTIONS-UX01, ACTIONS-A02 and ACTIONS-A03 overlays and user-provided native receipts. Review included canonical `devtool` CLI help, the shipped generator, A03/A02/UX01 test contracts, and a disposable target-local job invoked through the real Devtool CLI.

| Claim | Audit disposition | Evidence or next gate |
|---|---|---|
| UX01 local read-only Actions history, exact detail, narrow Textual screen | Delivered for local scope | Native 79/79 with Textual; no live API poll claim |
| A02 exact-run review, selected-artifact verification, pre-dispatch source checks | Delivered for source/local scope | Native 90/90; real remote correlation and artifact qualification still require live GitHub runs |
| A03 adoption review/write/verify and generated wrapper command | Delivered for source/local scope | Native 47/47; tested alias invoking disposable target-local job |
| A03 Ubuntu workflow executes canonical native DAG | **DEFECT FOUND AND FIXED BY A04** | A03 emitted invalid `invoke --json` (exit 2). A04 invokes with global `--machine-file` instead, and tests the actual command and resulting JSON. |
| A03 plan/preflight JSON from real Devtool CLI | Delivered for local scope | Both invoked with real CLI successfully |
| A03 source-pinned Devtool checkout and pinned GitHub actions | Delivered as generated configuration, subject to remote source availability | Existing source/hash checks; live hosted workflow still unqualified |
| A03 evidence archive and SHA256SUMS | Implemented; execution JSON was broken until A04 | Machine JSON now written by canonical interface. Hosted artifact transport/download verification still requires a live run. |
| Consumer adoption for Studyflow, Skybind, Cinderbox, and other repositories | **OPEN, deferred** | No consumer repositories changed and no GitHub workflow dispatched or verified by A03 |
| Platform-specific Android/Go/desktop runner provisioning | **OPEN, deferred** | Ubuntu-only workflow template; additional SDKs and devices may be required |
| Formal H1/H2 remediation, historical N01 authority, U01 substrate and release qualification | **OPEN, separately tracked** | Neither local Actions tests nor this hotfix close those independent gates |

## Root cause

The A03 tests compared generated YAML bytes and parser-generated plans, but never passed its `invoke` command through the real CLI parser. Acceptance therefore missed a generated-workflow runtime error despite 47 passing local tests. The corrected workflow uses the globally accepted `--machine-file` argument before the `invoke` subcommand, letting Devtool create the canonical runner envelope with failure propagation intact.

## Guards

A04 native auto-commit must be a direct child of accepted A03 commit `8706a4330ef42b1eadd2b6762d71ef4d71e17cfb`; preflight checks original A03 file hashes and a clean worktree. The cumulative improvements ledger must match Git HEAD, and the patch is append-only. The standalone native acceptance runs real CLI subprocesses, checks the A03 receipt, hashes sources, and never dispatches GitHub Actions.

## October 19 local GitHub 404 diagnostic (unresolved external access)

User observed `GitHub Actions: GitHub repository/resource not found or not accessible` while operating the Actions provider. This is the bounded message for a GitHub API HTTP 404; the available message alone does **not** identify the failed path. A private/inaccessible repository, incorrect inferred Git `origin`, stale or underprivileged `GH_TOKEN`/`GITHUB_TOKEN`, or a workflow/run/artifact absent in the selected repository can each produce a 404. Do not label this a successful live CI qualification or attribute it to A03 without the failing command and diagnostic evidence.

Read-only native checks (no token output): `git remote get-url origin`; `gh auth status`; `gh api repos/OWNER/REPO --jq .full_name`; `python3 -m devtool -r . actions diagnose --github-repo OWNER/REPO --auth gh --json`. `--auth gh` bypasses environment-token precedence; compare with `--auth auto` to identify a stale environment credential. For repository-accessible but workflow-inaccessible results, inspect the exact workflow identifier with `actions workflows --auth gh --github-repo OWNER/REPO --json`. Never blindly repeat a mutation/dispatch with `--yes` after an uncertain outcome. Keep diagnostic permissions read-only.

## Additional A04 installer and GitHub diagnostic evidence

A disposable A04 apply with the repository-local RUN-V3 store ran its tests and committed changes, but its post-commit LIFE cleanup refused source deletion because the persistent catalog no longer attested the artifact execution. The cleanup refusal is correct and was **not** bypassed. A separate identical install using Devtool's explicitly supported external `DEVTOOL_XDG_DIR` preserved RUN-V3 metadata across the self-update: six apply stages, 32 tests, automatic Git commit, and bounded cleanup succeeded. The A04 installer selects `~/.local/state/devtool` for this command when `DEVTOOL_XDG_DIR` is not configured; this does not overwrite an existing preferred root or migrate/delete older repository-local records. Default repository-local self-update state reconciliation is tracked as **OPEN**.

User's `actions diagnose --github-repo Mikeyphw/devtool --auth gh --json` returned `ok: true`, repository accessible, seven workflows accessible, and exit code zero. Hence the original GitHub 404 does not demonstrate a repository-wide authentication failure. The original failing command and exact resource are still needed to classify it. `--auth auto` remains untested by that result.

## A04 Termux native validation follow-up — 2026-10-19

The first native application attempt failed and rolled back successfully: 3
`tests/test_actions_a04_runtime.py` subprocess fixtures inherited validator
`DEVTOOL_*EXECUTION_ENVIRONMENT=host-android`. `host-android` is a
*diagnostic substrate*, not a supported execution selector, so the nested
`plan`, `invoke` and generated wrapper runs refused before producing receipts.
The generated Ubuntu workflow source itself is unchanged. The corrected fixture
sets the supported `host` selector for its nested host-local CLI subprocesses,
including a negative test deliberately injecting the invalid inherited value.
A04 remains **not native-accepted** until the amended artifact passes Termux
validation and writes a source-bound native acceptance receipt.
The failed run `b50b6ec21739438886184db2fdf7b295` retains its evidence and was
transactionally rolled back; this patch does not waive artifact validation.

### A04 Termux nested-substrate correction (second validation attempt)

Native validation failed again (30 passed, 3 failed) because a `host` selector resolves
back to diagnostic `host-android` when artifact validation sanitizes the
Termux host markers. The fix must bind child fixtures to `native-termux`
only when the running Python executable has the real Termux prefix and restore
that prefix within the test-only child environment; generic unsupported
Android hosts remain unsupported. No production execution router is changed.

Earlier native attempts rolled back; do not count them as acceptance.

### A04 third Termux validator correction — negative test identity isolation

Native run `64ba8185f62044a7a43d9314ab447977` ran 35 of 36 tests successfully and
rolled back after the unknown-Android-host *negative test* failed to raise.
The fake non-Termux executable `/usr/bin/python3` left `sys.base_prefix` bound to
Termux on the real device. The child-host selector legitimately accepts either
verified executable or base-prefix identity as positive evidence of Termux.
The new negative test resets **both** identity fields; it first asserts that a
real Termux base prefix must be recognized and then checks that a wholly
unknown Android interpreter cannot be upgraded to Termux. This is test-harness
isolation, not a weakening of native execution safety. Native A04 qualification
remains OPEN pending a successful installation and receipt.
