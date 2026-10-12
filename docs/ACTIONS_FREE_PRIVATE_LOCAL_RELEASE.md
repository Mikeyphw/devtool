# Local Android signing for private GitHub Free repositories

This lane is deliberately **not** an alternative to protected GitHub Actions
release qualification. The `.github/workflows/devtool-android-build.yml`
`GITHUB_REF_PROTECTED` checks, ACTIONS-08 attestation verification, and
GitHub release setup/readiness gates remain unchanged.

GitHub Free private repository rulesets may reject setup with HTTP 403. When
that happens, leave the repository private and run a local signed build using
the **existing** canonical Devtool LIFE Android signing identity.

## Requirements

- Devtool source, committed tracked files, private existing LIFE release signer
  with its pinned keystore / alias / cert SHA-256 and offline backup.
- Local Gradle launcher and native compatible Android/JDK build toolchain.
  Devtool's platform/environment doctor may be needed on Android/Termux.
- For APK verification, compatible `apksigner` and `aapt`/`aapt2` tooling.
  For AAB verification, `jarsigner`, `keytool`, and a compatible `bundletool.jar`
  assigned to `DEVTOOL_BUNDLETOOL_JAR`.
- Password available from the existing LIFE private secret source; for the
  local environment-backed provider, an interactive TTY prompts discreetly
  if the environment variable is absent. No secret is persisted by these commands.

## Preview, build, verify

From Termux (replace the ID if yours differs):

```sh
REPO="$HOME/Code/devtool"
devtool -r "$REPO" actions release local-build \
  --signing-identity devtool-android-release --json
```

A preview verifies local source cleanliness, project selection, and registered
signing identity without loading passwords or executing Gradle.

**After checking the preview and your keystore backup:**

```sh
devtool -r "$REPO" actions release local-build \
  --signing-identity devtool-android-release --yes --json
```

The command prompts for the local password when needed, executes `assembleRelease`
with the canonical Devtool Gradle signing init script and the target's existing
Gradle signing environment, verifies the APK signature/manifest against the
registered LIFE certificate, validates application ID and versionCode, and
reports the SHA-256 of the exact package inspected. It does not upload or
publish anything. Build subprocess output is intentionally withheld because
some Gradle plugins can print environment secrets.

To re-verify an APK that already exists (no password or GitHub needed):

```sh
devtool -r "$REPO" actions release local-verify \
  --signing-identity devtool-android-release \
  --artifact "$REPO/android/devtool-live-hud/app/build/outputs/apk/release/app-release.apk" \
  --json
```

Use `--format aab` for App Bundles, and `--application-id` if the application's
ID cannot be discovered statically. `--minimum-version-code` defaults to 1.
`--project-dir`, `--module`, and `--signing-target` select non-default projects.

## Trust boundaries and status labels

- `local_signature_verified: true` means APK/AAB signing and manifest inspection
  passed against the LIFE-pinned identity; this is **not** an attestation that
  the binary came from a particular source commit. The build records the local
  HEAD at launch but reports `source_attested: false`.
- `qualified_release: false` is mandatory for all locally produced/inspected
  packages. Never silently upgrade local checks to ACTIONS-08 success.
- `actions release verify` remains the only CI attestation qualification
  authority and still requires protected-ref workflow run, successful dispatch,
  canonical registered archive, pinned independent policy and cryptographically
  verified GitHub attestation.
- Never copy signing keys/passwords into Git, ZIP handoffs, terminal logs or
  GitHub Actions secrets while the private Free ruleset restriction persists.

## Deliberate limitations

No automatic signing-key rotation, GitHub secrets upload, repo visibility change,
branch protection bypass, release publication or remote evidence fabrication.
Local Gradle execution consumes repository build scripts; use only a trusted
checkout. Keep signing-key backups secure, and confirm the signing certificate
matches the identity used for any previously published app.
