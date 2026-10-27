# RM-11 — JavaScript/TypeScript hosted-provider source promise audit

**Base:** post-RM-10R1 clean Git tree `f4968d890163b9de114d4cbfd9042e284cea07df`. RM-11 and RM-12 merge-window reviewed; separate transactional boundaries because Node lock/script policies cannot be safely reused as Maven/Gradle reactor policies.

**Engineering outcome:** a bounded, source-owned Node/web monorepo provider integrated into the existing native Devtool DAG. The following table audits **behavior**, not path existence. Local tests (including real Node/npm) establish engineering behavior and policy; **no live GitHub, external signer, package registry or remote runner qualification is claimed**.

| Original RM-11 promise | Engineering delivery | Behavioral evidence / precise boundary |
|---|---|---|
| Tracked `package.json` and workspace discovery | **Delivered (bounded)** | `test_manager_lock_workspace_graph_and_affected`: real Git-tracked root and shallow declared workspace manifests; duplicate/missing/symlink/out-of-repo paths refused. No arbitrary recursion or dynamic package source evaluation. |
| npm/pnpm/Yarn/bun manager and lockfile identity | **Delivered (bounded)** | Four format fixtures and `test_mixed_managers_only_when_explicitly_selected`. Requires exact root `packageManager` version, matching trusted lock, explicit manager argv; mixed locks refused for unselected inspection, and can be used only with matching explicit selection. bun binary locks refused (only JSON text `bun.lock`). |
| pnpm affected package graph | **Delivered** | `test_manager_lock_workspace_graph_and_affected` computes internal workspace dependency topological order and downstream affected dependents; unknown `workspace:`/`file:` links and cycles rejected. No unproven automatic pruning of the native full DAG is claimed. |
| Source-owned typecheck/lint/test/build/pack scripts | **Delivered (reviewed templates)** | Exact direct package-manager `run` argv and reviewed literal script allowlist; no shell snippets, eval, lifecycle pre/post hooks, unreviewed package-manager configuration or script-shell overrides. `test_scripts_injection_hooks_cycle_and_symlink`, `test_refuse_changed_tracked_script_before_native_install`. Third-party tool configuration executes in the caller's own source-authorized job and is not granted Devtool elevated authority. |
| Frozen install per isolated GitHub job | **Delivered** | `workflow_yaml` pins setup-node and manager setup, native node receives exact reviewed job spec, checks Git HEAD working source unchanged, performs `npm ci`, frozen pnpm/yarn/bun install with lifecycle scripts disabled, aborts on any nonzero, and rechecks source digest before staging. Real offline npm native node test and mock install failure checks. No implicit cross-job `node_modules` sharing. |
| Node version / host matrix | **Delivered (bounded)** | Node 20/22/24, two reviewed Ubuntu/macOS hosted profiles, 48-job cap; `test_host_matrix_and_node_versions_isolated` validates 18 jobs in six isolated cells, correct `needs`, and refusal of unreviewed version. Windows/Termux hosted execution intentionally refused by inherited runner bridge. |
| Lock-keyed store-only dependency cache | **Delivered (opt-in)** | `test_manager_workflow_actions_cache_lock_and_other_formats`: trusted lock/source digests and exact toolchain/host profile in cache key; only static `runner.temp/devtool-node-store/<manager>` content-store path, no source/credential directories, no dynamic path injections. Disabled by default; source drift invalidates graph and key. |
| JUnit/Vitest/Jest-compatible test normalization | **Delivered (bounded)** | JUnit and Jest JSON readers require positive actual passing count, reject failures/zero, and provide byte digest. `test_js_verification_fail_closed`, `test_real_node_test_produces_junit`, `test_real_native_npm_install_node_execution_report`. Vitest source scripts must use fixed JUnit reporter; Jest JSON is schema checked. |
| Sharded JS results per DAG node | **Delivered (bounded)** | `test_sharded_junit_by_owned_node_and_failed_zero_report`: `ci_js_shard=i/n` binds reviewed Vitest `--shard`/output filename to exact node owned path; stage receipts use the graph, Git SHA, run ID, attempt, producer and SHA-256 payload, rejecting cross-cell handoffs through inherited RM-06 contract. |
| Web app bundle root and content verification | **Delivered (bounded)** | Native runner synthesizes owned `dist/manifest.json` from exact declared `dist/` files after build. `test_js_verification_fail_closed` rejects tampered files, missing entries, wrong/moved root or declared/manifest mismatch. Does not imply deployment or hosting. |
| Package tarball safety and identity | **Delivered** | `test_real_npm_pack_and_tar_traversal_refusal` uses real npm-generated package bytes, checks package name/version inside tar and rejects traversal, external links, missing manifest or forged identities. SHA-256 receipts are local structure, not registry signature. |
| Native ownership and source identity | **Preserved** | Direct target-local jobs, graph/source SHA, Git HEAD check, per-node byte handoff. No second scheduler and no elevation of hosted/GitHub/device/release trust domains. |
| Independent live hosted acceptance | **Unqualified external evidence** | setup-node, setup-bun, Corepack action setup are pinned but no remote GitHub Actions workload run was executed here. This is **not** counted as missing engineering functionality or claimed as successful qualification. |

## Policy boundaries (not silently shipped as promises)

Explicitly reviewed shallow workspace patterns, manager pins and script command templates are the supported interface. Arbitrary JS/npm scripts, dynamic plugin installers, install hooks, Yarn plugin paths, binary bun lockfiles, unmanaged external versions, Windows-hosted bridge, and source-ownership-unproven DAG pruning are refused. Expanding to arbitrary ecosystem behaviors requires a separate reviewed contract and tests, not a blanket compatibility claim.

## Acceptance distinctions

- **Implementation:** verified for the bounded promise interfaces listed above.
- **Local behavior:** native real npm package install, Node JUnit output and npm packed archive verified, together with positive/negative tests of other manager contracts.
- **External execution:** GitHub multi-OS Node/pnpm/Yarn/bun Actions runs not independently witnessed, left unqualified.
- **Release authority:** never implied by structural archive checks, byte hashes or passing scripts.


### RM-11R1 Termux portability acceptance (2026-10-22)

The RM-11 mocked direct-job test previously compared the test project's fixed
`npm@10.9.2` pin with ambient host `npm --version`, causing unrelated Termux
installations to fail the required validator before `run_specs` was reached.
The mocked execution now installs a version-only temporary npm shim on its
private PATH; the real offline npm acceptance test instead pins its temporary
project to the installed npm patch version and still runs the real frozen
installer. A negative native test injects a wrong manager version and verifies
that Devtool rejects it before dependency installation. The production
version guard remains untouched. Device acceptance is pending.


### RM-11R2 — Termux offline lockfile fixture portability (2026-10-22)

- **Observed device failure:** `test_real_native_npm_install_node_execution_report`
  failed before the native runner, while calling host npm `install --package-lock-only
  --offline`. The terse npm stderr pointed to a private debug log; the exact
  host npm failure code was not captured, so no unsupported root-cause claim
  is made.
- **Resolution:** Replace this extra lockfile-regeneration step with a complete
  deterministic npm v3 lockfile for two local workspaces. Preserve the real
  `run_node` -> `npm ci` frozen install, real Node JUnit test, SHA-bound handoff,
  and runtime exact manager-version check. Use an isolated empty npm cache and
  user npmrc; enforce offline and ignore-scripts during the live test.
- **Behavioral proof:** deterministic lock contents, source-change mismatch,
  actual offline `npm ci` and native test, existing wrong-pin refusal, plus
  Devtool transaction refusal and rollback. No production version guard change.
- **Evidence limit:** final Termux acceptance is still pending user device run.


### RM-11R3 — Portable real npm smoke and rejection (2026-10-22)

Termux RM11R2 again failed the mandatory real native npm test, this time at
`npm ci`; the user's log showed only a debug-log path, so the exact npm runtime
error cannot be inferred. RM11R3 uses a complete v3 lock for a self-contained
package with no registry dependencies or workspace symlink requirements, while
retaining a **real** `npm ci` offline, real Node JUnit output and native SHA-bound
handoff. Workspaces and affected dependency graphs remain validated by
independent tests. The live npm store is outside the source checkout and user/
global npmrcs are isolated. A new adversarial test makes sure failed frozen
installation refuses the downstream native script and artifact handoff.

This change does **not** assert a real four-manager hosted acceptance; external
GitHub execution and actual Termux device validation remain separate gates.
