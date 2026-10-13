# ACTIONS G4 — unsigned Android App Bundle candidate and source-bound evidence

**Status:** source implementation and offline tests only until real GitHub + Termux acceptance.

This overlay adds a **new**, explicitly unqualified AAB candidate lane to the existing GitHub Actions provider. It leaves `.github/workflows/devtool-android-build.yml` (protected signed release), `.github/workflows/devtool-android-unsigned-release.yml` (successful ACTIONS-10 APK), and the native Termux debug build unchanged.

## New CLI

- `devtool actions android aab-plan --ref main --json` — source-checked, nonmutating plan.
- `devtool actions android aab-run --ref main --yes --json` — explicitly dispatch AAB workflow using the existing Actions-02 provider and receipt.
- `devtool actions android aab-check --run-id N --artifact-id N --expected-sha COMMIT --download --json` — read-only archive, manifest, source, lint, test, *native bundletool* and unsigned-state validation. On subsequent checks, omit `--download` if archive already cached.
- `devtool actions android aab-import --run-id N --artifact-id N --expected-sha COMMIT --download --json` — same validation, exclusive output and immutable artifact registry entry.
- `devtool actions android aab-sign --registry-id ID --json` — LIFE signer preview, entirely local.
- `devtool actions android aab-sign --registry-id ID --yes --json` — local JAR signing using the registered LIFE keystore, bundletool and JAR signature/certificate verification. Never sends private credentials to GitHub.

**Native prerequisite:** Set `DEVTOOL_BUNDLETOOL_JAR` to an explicitly reviewed, locally installed compatible `bundletool.jar`. There is no silent download and no acceptance without the independent tool.

## Exact CI contract

Workflow `.github/workflows/devtool-android-aab-candidate.yml` requires a checked-out commit equal to `GITHUB_SHA` and has `contents:read` permission only; it does not load signing secrets. The Gradle property `devtool.unsignedReleaseBundle=true` is accepted only when the **single** requested task is `:app:devtoolBundleUnsignedReleaseCandidate`. Its dependencies are `bundleRelease`, `testDebugUnitTest`, and `lintRelease` in one Gradle graph. Ordinary release tasks retain their signing requirement.

The exported archive must have exactly four ordinary files:
`unsigned-release.aab`, `unit-tests.json`, `lint-results-release.xml`, `manifest.json`.
The manifest pins workflow path, exact commit, run/attempt, branch/tag, expected repository/application ID, file size, three SHA-256 digests and bounded actual unit-test/lint counts. The native importer requires the successful GitHub workflow_dispatch run, matching workflow ID, attempt and SHA, GitHub artifact digest/ZIP safety, identical sidecar values, and independent native `bundletool validate` + manifest identity/version extraction. It also proves the bundle has no JAR signature before local signing.

## Trust limits and deferred gates

- **Never** set `qualified_release: true` or `source_attested: true` for this lane; GitHub checkout and a workflow-local manifest are not cryptographic source attestations.
- CI success and lint/test JSON are claims from that pinned workflow source, not a proof of protected-branch governance.
- `testDebugUnitTest` validates the debug unit-test variant **against the same source**, not the exact release binary. Release lint runs against the release variant. This is the strongest accurate binding without introducing instrumentation or a separate release unit-test suite.
- No live AAB has yet been built, imported, or locally signed by this overlay's authors. Live CI, native Termux bundletool and JAR signer output must be evaluated by the user.
- Cross-project and custom-flavor matrices still need a separate actual-variant fixture; the AAB candidate is deliberately restricted to the known Devtool HUD app. Existing generic ACTIONS-05 `android plan/run/collect` remains available.
- Existing ACTIONS-10 verified local signed APK and native Termux debug build remain regression anchors, not requalified by these source tests.
- G1 live negatives, G2 restart/cancellation and G3 actual GTK/HUD acceptance remain deferred, not counted as G4 passes.

## Minimum device exit evidence

1. Exact new GitHub run + attempt and pinned source commit. Job log shows bundle, lint, unit tests, and artifact upload success.
2. `aab-import` report returns verified ZIP, a canonical AAB-specific artifact registry ID, `bundletool_verified:true`, and exact test/lint receipts.
3. `aab-sign` plan, actual locally signed AAB and independent signer SHA-256 match from the existing LIFE identity.
4. One non-Devtool Android project or custom flavor build and negative toolchain/unsupported-variant evidence, tracked separately; without these G4 remains partially qualified.
5. No changes or regressions to ACTIONS-10 APK and protected release signing behavior.

