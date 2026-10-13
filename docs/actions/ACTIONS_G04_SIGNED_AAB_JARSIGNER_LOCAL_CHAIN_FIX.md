# G4 signed AAB — local LIFE certificate-chain verification hotfix

## Live observation

The real device successfully imported the unsigned bundle from GitHub run
`38445139596` (artifact `11837083625`) as registry `dfb12b7b5269`, with
27 unit tests passed and no release-lint errors. Local `aab-sign --yes` then
failed after prompting for the signing password with the fixed message:
`AAB JAR signature verification failed; cannot trust Android package`.

## Reproduced defect

The `devtool.actions_android._inspect_aab` verification path used
`jarsigner -verify -strict` and treated any nonzero status as invalid JAR bytes.
A real self-signed, cryptographically intact test JAR returns status **4**
(`jar verified, with signer errors.`) under `-strict` because its local
certificate chain is not publicly trusted. That is a *chain trust* failure,
not evidence of damaged JAR signature bytes. `jarsigner -verify -verbose`
returns success for a valid JAR signature, while still failing malformed
signatures; however, it also returns success for completely unsigned JARs or
archives with appended unsigned entries.

## Narrow repair

- Signed AAB verification runs `jarsigner -verify -verbose` and requires its
  unambiguous `jar verified.` status (not merely exit=0).
- It separately requires exactly one matching top-level `META-INF/*.SF` and
  `META-INF/*.(RSA|DSA|EC)` signature pair.
- It rejects tools reporting unsigned entries or listing added unsigned files.
- `keytool -printcert -jarfile` still extracts the signing certificate, and
  the canonical LIFE signing profile must match its SHA-256 fingerprint.
- Bundletool `validate`, package and versionCode inspection, registry source
  identity, read-only signing plan, and private signing flow stay unchanged.
- All results remain `qualified_release: false` and `source_attested: false`.

No signing password, keystore, or signed payload is packaged. Test keystores
are created with disposable credentials in temporary directories and never
shared or registered.

## Live retest

After successful guarded installation, use the *same* unsigned AAB registry
`dfb12b7b5269`. Run a read-only `aab-sign` plan first. Then rerun
`aab-sign --registry-id dfb12b7b5269 --signing-target android_hud --yes --json`
in an interactive Termux session with `DEVTOOL_BUNDLETOOL_JAR` pointing at the
existing validated bundletool 1.18.3 JAR. Inspect the signed registry ID and
SHA-256; independently run `jarsigner -verify -verbose` and `keytool -printcert
-jarfile` on the new signed output. Never paste passwords, keystore bytes,
or complete private signing logs into chat.

This repair is only the source-level and simulated apply acceptance. Actual
Termux signing and independent certificate inspection remain pending until
reported by the device.
