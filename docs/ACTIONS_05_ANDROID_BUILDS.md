# ACTIONS-05 — Android build and package inspection (not release qualification)

This provider does **not** create a remote workflow in your GitHub repository. Copy
`docs/templates/devtool-android-build.yml` to the Android application's
`.github/workflows/devtool-android-build.yml`, review it, commit and push it. The
remote `workflow_dispatch` definition must match the selected input keys.
The example workflow does **not** configure release signing; use restricted
repository secrets and an explicitly approved stable certificate through your
own policy before requesting verified release output.

The `-r` repository's GitHub `origin` identifies `OWNER/REPO` automatically.

```
devtool -r "$HOME/Code/skybind" actions android discover --json
devtool -r "$HOME/Code/skybind" actions android plan --module app --variant debug --format apk --ref main --json
devtool -r "$HOME/Code/skybind" actions android run --module app --variant debug --format apk --ref main --yes --json
devtool -r "$HOME/Code/skybind" actions watch --run-id <RUN_ID> --json
devtool -r "$HOME/Code/skybind" actions artifacts --run-id <RUN_ID> --json
devtool -r "$HOME/Code/skybind" actions android collect --run-id <RUN_ID> --artifact-id <ARTIFACT_ID> --format apk --variant debug --expected-package io.example.app --download --json
```

`android collect` inspects the exact selected package with Android tools and
materializes it under `.devtool/artifacts/android-verified/` only after
artifact digest, run status, manifest, and signing validation succeed. An
ambiguous multi-package ZIP requires an exact `--member` path. `apksigner`
and `aapt` or `aapt2` are required for APK inspection. AAB requires Java,
`DEVTOOL_BUNDLETOOL_JAR`, `jarsigner`, and `keytool`. Release collection also
requires `--expected-package` and `--expected-cert-sha256` and fails closed if it cannot confirm it.
The registered result is still **unqualified**: ACTIONS-08 release gates and
signing/provenance attestation are not implemented here.

ACTIONS-05 requires a clean, committed local checkout and insists that the
selected GitHub `--ref` resolves to the exact local HEAD observed during
planning. Branches can move between plan and dispatch; the dispatched source
is later established by GitHub's remote run commit. This is not a source lock.

No actual Android build, live authenticated dispatch, or native Android tool
result has been claimed from this test environment. Read-only operations
are safe without `--yes`; remote mutation is explicitly gated.
