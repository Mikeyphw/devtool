# RM21 — Native hosted Node/JVM acceptance (non-release)

This upstream phase follows merged RM15 and RM17. It adds a **real hosted
execution** proof for the first-class RM-11 Node and RM-12 JVM providers,
retaining their exact source-review contracts. It does **not** change
StudyFlow, permit release signing, or grant device qualification.

## Two noninterchangeable gates

1. **Hosted committed fixture** (PR-triggered): GitHub-hosted Node 22/npm
   10.9.4 and JDK 21/Gradle 9.1.0 execute a standalone, separately committed
   source fixture. The Node lane performs `npm ci --ignore-scripts`, the reviewed
   `npm run build`, native `verify_js_artifacts`, and an exact manifest/output
   SHA-256 check. The Gradle lane runs the reviewed offline `:desktop:jar`,
   verifies no Android module is in the inspected JVM-only closure, and invokes
   the native RM-12 `verify_jar` class-bytecode/manifest/signature-structure
   validator in addition to independent CRC, path and SHA-256 checks. The
   output **bytes themselves**, source SHA, provider receipt, and JUnit tests
   are archived together. A fixture PASS is **not** independent consumer PASS.
2. **Explicit external consumer** (`workflow_dispatch` with `external_repository`,
   `external_sha`, and lane): checks out the named *public* external repository
   at a literal 40-character commit SHA, compares Git checkout and remote
   identity, reuses the same RM-11/RM-12 source review, executes the native
   command, validates output bytes, and emits a separate non-release receipt.
   Missing or mismatched inputs fail; there is no latest/default-branch fallback
   and no implicit external job on a PR. All external source scripts are
   executable by the hosted runner; run this only for trusted reviewed consumers.

## Native inputs and limits

- Node build: an RM17 `reviewed-composite` project, exact `npm@MAJOR.MINOR.PATCH`
  `packageManager`, committed lockfile, committed Node entrypoints, a reviewed
  `npm run build` script, and an exact `dist/manifest.json` including the SHA-256
  of each owned file. `web` is the default project; the directory is explicit.
- JVM: exact `ci_jvm_mixed_module` identity, static dependency closure, an
  already available GitHub-hosted Gradle version, JDK 21, checked-in dependency
  verification/locking, and an actual JAR containing compiled `.class` files.
  The acceptance command uses literal `--offline --no-daemon --console=plain`.
  Gradle executes **only on GitHub Actions**; never in the Termux installer.
- Existing test adapters, Android task compilation, Compose native packaging,
  and consumer release remain **outside** RM21. A built artifact and a green
  process exit are not an authenticated unit-test or release receipt.
- In the fixture, `:android` is a sibling outside the selected JVM closure.
  The fixture is deliberately dependency-free and uses Gradle configuration on
  demand to avoid resolving Android plugins. It does not prove that an entire
  Android/Compose monorepo builds successfully.

## Source and evidence rules

- Git HEAD must equal the exact supplied SHA, and the tree, tracked sources,
  native provider source digest, and output bytes are checked around execution.
- No repo tokens/secrets are forwarded to executed source commands; the workflow
  uses only read access and `persist-credentials: false`. This is an isolation
  *reduction*, not a guarantee against untrusted code on a connected runner.
- The receipt always includes `release_qualified: false`,
  `signing_qualified: false`, and `device_qualified: false`. Fixture receipts
  also include `independent_consumer_qualified: false`. The external flag
  refers only to pinned source/execution acceptance, not formal attestation.
- The full 24-shard Devtool Python regression remains a distinct mandatory CI
  gate before this change is merged. Real StudyFlow adoption remains blocked
  until an actual exact-SHA consumer run satisfies these contracts and its
  independent authoritative test, release, and device requirements.
