# ACTIONS G6 — Local release truth and offline evidence verification

## Scope and authority

The canonical, read-only command is:

```sh
devtool -r "$HOME/Code/devtool" actions release local-evidence \
  --registry-id 26529ad5f699 \
  --expected-sha f3185b6aac56d1ac29b5fac93bdda88fc4480842 --json
```

For a registered locally signed AAB specify `--format aab`. The command **never**
contacts GitHub, dispatches a workflow, changes registry records, uses the
private signing key, signs an APK, or attempts publishing. It reopens an
existing registered signed APK/AAB and its matching registered **unsigned**
parent; compares exact stored repository, workflow path, run ID, rerun attempt,
source commit, file digests, package/version and signing-identity metadata;
then reruns the existing LIFE-backed APK/AAB certificate and manifest inspection.

The optional `--expected-sha` is an explicit 40-hex source pin. Without it,
G6 verifies internal consistency but cannot independently establish the
correctness of the recorded GitHub source. The registry is a local record,
**not** a remote cryptographic attestation. An attacker able to rewrite the
registry and all artifact bytes could invent a new internally consistent story;
ACTIONS-08 protected workflow and authenticated provenance remain distinct.

## Evidence fields and nonpromotion

`registered_artifact_lineage_verified`, `local_signature_verified`, and
`github_artifact_import_verified` can become true when evidence supports them.
`github_provenance_verified`, `source_attested`, `qualified_release`, and
`published` are **always false**, regardless of local signing success. G6 cannot
silently upgrade the release; a completed GitHub build is not an attestation.

G6 also rejects a signed parent with `qualified_release=true`, debug/unknown
certificate, a mismatched LIFE fingerprint or identity, missing AAB lint/unit-test
receipts, a stale attempt, unsafe path, changed digest, duplicate registry ID,
parent/source mismatch, or a renamed/symlinked file. It refuses to proceed if
bundletool/apksigner cannot independently inspect the package.

## Compatibility, acceptance, and deferred gates

- Existing successful ACTIONS-10 unsigned APK + local LIFE signer, G4 AAB lane,
  G5 CI workflows, protected-release policy/workflow and native debug Gradle
  baseline remain unchanged.
- The user explicitly excluded GitHub Free private-repository paid branch
  protections from required G6 completion; no policy bypass is added.
- The G1 live negative matrix, G2 live crash/cancellation exercises, G3 GTK/HUD
  runtime, G4 live AAB build and G5 full hosted matrix still require separate
  device/GitHub evidence. G6 only source-qualifies its offline verification.
- A 6/6 native overlay transaction, focused tests and guarded single Git commit
  establish installation, not production release qualification.
