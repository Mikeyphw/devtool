# ACTIONS-08 — Authenticated Android release security and provenance

ACTIONS-08 introduces a **separate, read-only release qualification command**.
A successful build, a GitHub Actions artifact archive digest, or an APK signed
with Android Debug do **not** qualify a release. Neither does a successful
ACTIONS-06 pipeline alone.

## Signing workflow (opt-in, protected)

`.github/workflows/devtool-android-build.yml` now uploads **only the exact
selected module / variant / format** rather than all APKs and bundles in the
repository. Debug APK builds remain debug builds. For `release`:

- GitHub must mark the dispatched ref protected (`GITHUB_REF_PROTECTED=true`).
- Configure the repository's Actions secrets
  `DEVTOOL_RELEASE_KEYSTORE_B64`, `DEVTOOL_RELEASE_STORE_PASSWORD`,
  `DEVTOOL_RELEASE_KEY_ALIAS`, `DEVTOOL_RELEASE_KEY_PASSWORD`.
- Secrets are not workflow inputs, echoed, or recorded by Devtool. Manage them
  directly in GitHub's repository Actions secrets settings. GitHub access controls
  and branch/tag protection must be configured and audited by the maintainer.
- The workflow fails rather than uploading an unsigned release. The selected
  release APK is zipaligned and signed with `apksigner`; release AABs use
  `jarsigner`. Both signature verification commands run before upload.
- The signed **release package** (not the ZIP containing it) is subject to an
  `actions/attest@v4` SLSA provenance attestation. Job permissions include
  `contents:read`, `id-token:write`, `attestations:write`, and
  `artifact-metadata:write`.
- The workflow and template require GitHub Actions runners with the available
  actions/attest capability and account eligibility. Unavailable attestation
  stops a release build; it does not silently skip provenance.

Source control of the release-signing key is forbidden. No default signing
certificate is configured. **Do not copy a sample signer fingerprint into the
trust policy.** Use the stable SHA-256 of the independently managed production
signing certificate.

## Independent release-policy trust anchor

The policy must be stored **outside the checked-out source repository**, owned
by the current OS user, as a regular non-symlink file with mode 0600. Treat it
as a reviewed, trusted authority, not a value proposed by a pull request or
by CI. ACTIONS-08 intentionally cannot create or approve this policy for you.
ACTIONS-09 adds an explicit maintainer-authorized setup command which derives this
policy from the observed remote SHA and your independently owned keystore. See
`docs/ACTIONS_09_GOVERNANCE.md`. Setup is not production release approval.

Copy `docs/templates/devtool-release-policy.example.json` to a file such as
`$HOME/.config/devtool/release-policy.json`, adjust the repository, full
protected ref, exact approved source commit SHA, stable package ID and cert
fingerprint, format, and minimum versionCode, then run `chmod 600` on that
file. Pin a new source SHA for each approved build; never accept an arbitrary
ref or wildcard certificate.

## Usage

After a **real signed release build completes**, download and register the
GitHub artifact with the native ACTIONS-04 command, then run:

```shell
devtool -r "$HOME/Code/devtool" actions download \
  --run-id RUN_ID --artifact-id ARTIFACT_ID --json

devtool -r "$HOME/Code/devtool" actions release verify \
  --run-id RUN_ID --artifact-id ARTIFACT_ID \
  --policy "$HOME/.config/devtool/release-policy.json" --json
```

The existing registered download is mandatory: ACTIONS-08 does not silently
fetch, upload, overwrite, publish, sign, or deploy anything. It performs:

1. independent 0600 owner-controlled policy validation, exact repository,
   protected ref identity, pinned Git SHA and workflow identity;
2. fresh GitHub run completion and successful workflow_dispatch verification;
3. ACTIONS-04 artifact SHA-256, archive safety, canonical registration;
4. ACTIONS-05 APK/AAB manifest and signing verification, exact application ID,
   exact approved certificate, known non-debug signing identity and minimum
   verified versionCode;
5. extraction to a bounded temporary file with a second payload SHA-256 check;
6. `gh attestation verify` on the exact package bytes with pinned repository,
   signing workflow, source commit and ref, SLSA predicate and self-hosted runner
   denial. Nonzero result, missing attestation or missing `gh` fail closed.

Only a successful full verification returns `qualified_release: true` and
`read_only: true`. This means **package/provenance verification**; it does not
certify publication approval, vulnerability scan, device tests, store signing
acceptance or production deployment. The older `actions android collect`
continues registering artifacts as unqualified by design.

### Qualification boundaries

Offline tests exercise positive and negative branches with mock remote
contracts. No real production signing key, eligible attestation account or
live signed release was available in the test environment. The first live
release must be explicitly qualified using GitHub and the user's private
policy. Existing Live HUD debug artifacts cannot pass the release gate.
