# R39-R5 — Final source-level generated Actions runtime closure

## Scope implemented

This is the last **source-code** remediation identified in R39-R4's independent gate
inventory. The static `.github/workflows/` versions were migrated in R39-R4;
this patch advances the **live generators** for A03/A09, NEXT-03, RM-06,
RM-07 and their exported provider jobs to reviewed immutable Node 24-compatible
GitHub Actions releases. It updates first-party `checkout`, `setup-python`,
`setup-go`, `setup-java`, `setup-node`, `setup-dotnet`, `upload-artifact`,
`download-artifact`, `cache` and Gradle's `setup-gradle` pins. R34's separately
reviewed source generator was already migrated under R39-R4.

`devtool.actions_runtime_pins` is the read-only, fail-closed **source contract**
for this 10-action set. The existing R39 roadmap gate audit includes its
result, and the R39 consolidated hosted workflow tests and uploads an
independent `generator-pins.json` receipt. Historical evidence files and
snapshots are never rewritten. Consumer generated workflows require an explicit
regeneration and review; this overlay does **not** mutate any consumers.

## Acceptance and boundaries

- Exact Git commit and tree, clean repository and preimage hashes are checked
  before the Devtool transaction mutates the checkout.
- Generator source tests exercise emitted workflow references and all pinned
  provider families. Negative tests tamper with individual pins and proof
  metadata. Hosted full Python regression is required on the newly created SHA.
- `dynamically_generated_action_runtime_migration` remains `NOT_RUN` in
  `independent_gates_this_source` because a source-only audit is not a verified
  adoption by a real consumer. This implementation closes the **generator
  source gap**, not the external adoption gate.
- Third-party Actions (e.g. Bun and MSVC), real Windows/Linux host execution,
  native parallel vs legacy parity, external/untracked write isolation,
  interrupted host recovery, authenticated device/APK import, signer identity,
  actual cache performance and formal release remain unqualified.
- `run_specs` and the native scheduler fallback are preserved; there is no
  broad legacy execution-engine retirement or Gradle invocation on Termux.

## Follow-up independent evidence

When the overlay is accepted, dispatch the current-commit R39 consolidated
source workflow and full 24-shard regression. Future external gate closure
must use run/attempt/source-bound hosted evidence, real consumer adoption
receipts, physical device checks and separate signer/release authorization.

## Termux local npm runtime is independently unqualified

The transactional overlay validator runs the generated-Actions source tests and
machine source audits. Under native Termux Python only, it explicitly deselects
`test_real_native_npm_install_node_execution_report` and
`test_real_npm_pack_native_artifact`. Both run **unmodified** in the 24-shard
hosted Linux regression; neither is counted as locally passed on Termux.

This boundary follows two observed failures of the Termux-native npm CLI
(`npm ci --offline` and `npm pack --offline`), for which npm's separate debug
logs were not supplied. No claim is made about their underlying cause or the
health of npm on the device. The local npm runtime qualification remains OPEN.
The validator prints `R39_R5_TERMUX_NATIVE_NPM_RUNTIME_NOT_QUALIFIED` and
pytest reports two deselected cases. All other selected tests and both source
audits remain mandatory and fail closed. When investigating the Termux npm
installation, run the two npm tests separately with `-vv -s`, capture npm logs,
and do not treat the source-only overlay acceptance as host npm acceptance.
