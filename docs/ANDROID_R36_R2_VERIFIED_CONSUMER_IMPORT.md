# ANDROID-R36-R2 — Exact-run authenticated real-consumer artifact import

**Status:** source implementation and offline real-archive cross-check. Actual
canonical Android SDK package/signature inspection and persistent registration
are *not yet proven on the operator host*. No Gradle, release signing or device
operations in this implementation.

R35's `devtool.actions_r35_verified_import` remains the canonical entry point.
`--layout r36 --profile mmrl|dustui` selects a reviewed two-archive layout,
which delegates acquisition to ACTIONS-04 (`artifact_list`,
`download_artifact`, `verify_download`, `check_zip`), independent APK inspection
to ACTIONS-05 `android_collect(materialize=False)`, and immutable registration
to `artifact_registry.register_paths` / `verify_record`. The original default
R34 layout, CLI behavior and tests remain unchanged. No new uploader,
dispatcher, registry, provider or signing service is added.

## Admission boundaries

- Pins required from operator: GitHub repository, run ID, attempt, source SHA,
  package ID, debug signer certificate SHA-256 and APK bytes SHA-256.
- Only reviewed GitHub workflows for MMRL and DustUI are accepted; successful
  workflow_dispatch, exact workflow ID/source/attempt and two unexpired,
  unambiguous, SHA-256-bearing GitHub artifacts are mandatory.
- `receipt.json` and the `payload/` APK must match exact member names, DAG
  graph/workflow hashes, task-bound profile, source, run, attempt, producer
  node, APK size and independently calculated hash. Evidence plan is checked
  against the same SHA/graph/tasks. MMRL additionally requires exact
  `verification.json` signer, package, status, hash and non-release claims.
- ACTIONS-04 streams the authentic GitHub artifact ZIP from the authenticated
  API redirect and checks GitHub's published ZIP digest. It refuses duplicates,
  traversal, special members, zip bombs and SHA mismatch. `plan` is read-only.
- ACTIONS-05 independently parses and cryptographically verifies the actual APK
  with Android SDK `aapt`/`apksigner`. The archive is treated as a debug **build
  type**; profile-specific `officialDebug` vs `debug` tasks and paths remain
  fixed and verified by the separate authenticated plan/receipt checks. R36
  artifacts do not contain ACTIONS-05's optional standalone custom-variant
  identity manifest; we do *not* invent one or bypass Android verification.
- Immutable APK/receipt files under `.devtool/artifacts/r36-imports`; registry
  record and bytes reverified offline on `inspect`, idempotent exact re-import,
  fail closed after interruptions (no silent overwrite).
- R36 producer's `authenticated=false` means only producer-controlled evidence;
  it becomes authenticated **only after** independently downloaded ZIP
  archives and Android signature inspection validate on the importing host.
- `remote_actions_artifact_authenticated` is archive provenance, **not**
  production/release signing, reproducibility, trusted attestation, installed
  APK, device qualification or authorization to ship.

## Read-only plan: refreshed MMRL source

Run from the Devtool root (Termux with `gh` already authenticated). The
*plan* does not fetch ZIP bytes, run Gradle or mutate the registry.

```bash
python3 -m devtool.actions_r35_verified_import plan --layout r36 \
  --profile mmrl --repo "$PWD" --github-repo Mikeyphw/MMRL \
  --run-id 41272202977 --attempt 1 \
  --source-sha 85c5f8d75356f36f4b9c488364729aa411588acc \
  --expected-package com.mikeyphw.mmrl.debug \
  --expected-signer-sha256 dff532a898f558951a1285919d043541d1d128ec200d9e61060d3fc09ba9939d \
  --expected-apk-sha256 6a9d192c0c25839b8fbc76bb7806cbf4e6b43cc3b73b1a0cf8750913cc7b9079 \
  --auth gh
```

Review the exact artifact IDs (native APK `13069812994`, evidence
`13069937689`) and ZIP SHA-256 digests below. Then replace `plan` with
`import` in the command above, **only on a host with genuine Android SDK
`aapt` and `apksigner` installed and discoverable on PATH**. This downloads
and stores archives in Devtool's pre-existing ACTIONS-04 registry, then
independently checks APK package/signature and registers APK bytes. It never
installs the APK. Replace `plan` with `inspect` to reverify the stored receipt
and APK bytes offline.

## Independently observed real GitHub artifact inputs (2026-11-01 UTC)

| Consumer | Run / attempt | Artifact ZIP digest | Evidence ZIP digest |
| --- | --- | --- | --- |
| MMRL | `41272202977 / 1` | `30a54894072079dcdd6efcdb780613b77215fb90a303b9d3482010bc21a069ed` | `95280f04d62eea05e701cc240640036a91b50d39b3bdd08a3162a7eab4bda3aa` |
| DustUI | `41258640269 / 1` | `8badc4cbf798bfe67a2174fe81342e79422679d54811d21579c740bd704f7734` | `b949570d92abd75797b100a71a55867ffe4d5739681949c501215f4cb5768d3e` |

Both downloaded ZIP pairs were inspected against their real plan/handoff
schemas in the R36-R2 offline source test. MMRL's APK sha256 is
`6a9d192c0c25839b8fbc76bb7806cbf4e6b43cc3b73b1a0cf8750913cc7b9079`;
DustUI's APK sha256 is `c027e184df105a9bfcc82ec983b640b5df598961aa97265a978c854faf7ef60e`.
DustUI's original producer did not publish an independent signer record; a
trusted package/signature pin is still required before its persistent import.

**Acceptance remaining:** real native ACTIONS-04 authenticated collection,
ACTIONS-05 signature check and persisted registry receipt on the operator's
host, then CI source tests for the exact post-overlay SHA. Keep FC-09,
production signer, Android instrumentation/device acceptance separate.
