# ANDROID-R35 — Verified exact-run R34 artifact import (non-release)

**Source-only overlay baseline:** `20d4d6fcf3be808f41dbae5f0b56fadf4548e825`, Git tree `1e8b4ee67ac9d3ba2f7808d46decf4a3685e938a`. Scope: Devtool only. No Gradle on Termux, no consumer changes, no deployment, release or device qualification.

## Integration owners, not replacements

`devtool.actions_r35_verified_import` is a thin R34-specific coordinator. It uses the existing ACTIONS-04 `artifact_list` / `download_artifact` / `verify_download` APIs for authenticated exact-run GitHub ZIP acquisition, ACTIONS-05 `android_collect(materialize=False)` for independent SDK package/signature inspection, and `artifact_registry.register_paths` / `verify_record` for immutable local APK registration. No new GitHub dispatcher, scheduler, registry or generic Android executor is added. R31 consumer handoff restrictions remain unchanged.

An explicit `--run-id`, `--attempt`, `--source-sha`, stable debug application ID and `--expected-signer-sha256` are required; `plan` never downloads, writes a file or makes a POST. `import` looks up exactly three R34 artifacts (`node-01-evidence`, `node-02-evidence`, `debug-apk`), refusing missing, ambiguous, expired, mismatched or hashless archives. It validates bounded member paths/types/CRC using ACTIONS-04; requires exact source/run/attempt/workflow from the GitHub API; crosschecks source/fixture/graph/hosted lint/build/bootstraps/native staged output/hosted registry snapshot/standalone APK bytes and independently inspects package/signature via ACTIONS-05. The resulting APK and lineage receipt live under `.devtool/artifacts/r35-imports` with exclusive no-overwrite creation. A re-import of the same exact record verifies the registered bytes and returns the original receipt; an interrupted import cannot be silently overwritten. `inspect` is offline and read-only (it does not revalidate GitHub's latest API observations).

`remote_actions_artifact_authenticated=true` in an R35 imported receipt means the three GitHub ZIP archives were checked against the published digest **at collection time** and their native R34 evidence was cross-validated, not that a privileged signing authority, trusted attestation, independently reproducible build, installed APK or release gate was satisfied. Its APK certificate identity must also be checked by locally available `aapt` + `apksigner`. If these tools are missing, import is refused rather than treating a producer's JSON assertion as independent signing evidence. Keep the debug signer pinned to the actual selected run, not a previous run's ephemeral debug key.

## Source-checked reference hosted run

R34-R1 GitHub workflow `41230694663`, attempt 1, source `20d4d6fcf3be808f41dbae5f0b56fadf4548e825`. Separately downloaded three real Actions archives and checked their cross-records with the R35 validator: **PASS_R35_REAL_R34_ARCHIVE_CHAIN_SOURCE_INSPECTION**. APK SHA-256 `4c7bf7befe4eae72fa988d10ec01b4f0c1a7d7f470f945831f0596ebed93f160`, byte size 8313, signer certificate SHA-256 `225044f45869aaa34421adcb9a9d093613e7fd41f9812d82bd606b7b88119bb0`. Their source native graph hash is `cd48e3b6c22ff8d6556f0e892dea5808c415f544e9464079579a750eb23848d5`. This cross-record inspection **does not** establish that the operator's Termux persistent registry has received the APK; that is a separate native acceptance gate.

### Operator CLI — read-only preview, then explicit import

```zsh
cd "$HOME/Code/devtool"
python3 -m devtool.actions_r35_verified_import plan \
  --repo "$PWD" --github-repo Mikeyphw/devtool \
  --run-id 41230694663 --attempt 1 \
  --source-sha 20d4d6fcf3be808f41dbae5f0b56fadf4548e825 \
  --expected-package org.devtool.r33.fixture \
  --expected-signer-sha256 225044f45869aaa34421adcb9a9d093613e7fd41f9812d82bd606b7b88119bb0 \
  --auth gh
# Inspect the selection. The following explicit import downloads and mutates the private local registry.
python3 -m devtool.actions_r35_verified_import import \
  --repo "$PWD" --github-repo Mikeyphw/devtool \
  --run-id 41230694663 --attempt 1 \
  --source-sha 20d4d6fcf3be808f41dbae5f0b56fadf4548e825 \
  --expected-package org.devtool.r33.fixture \
  --expected-signer-sha256 225044f45869aaa34421adcb9a9d093613e7fd41f9812d82bd606b7b88119bb0 \
  --auth gh
# For a local immutable receipt query, replace import with inspect; no GitHub credentials needed.
```

**Local tool prerequisites:** supported Python + Devtool CLI dependencies, authenticated `gh`, and actual Android SDK `aapt`/`apksigner` on PATH (for independent package inspection). These tools may be provisioned on an explicitly authorized isolated workstation rather than Termux; R35 does not run Gradle. GitHub Actions archives expire after their configured retention; R35 must refuse expired runs and require a fresh exact-run replacement, not swap identifiers.

## Targeted overlay validation and acceptance boundaries

- Native overlay targets tests: `test_r35_verified_import.py`, ACTIONS-04 archive and attempts, ACTIONS-05 Android role checks, R31 handoff, registry lifecycle, R34 integration and destructive fixed-point audit. Existing test suites only; no Gradle invocation on Termux. The manifest selects only those tests, not defaults.
- V0 exact HEAD/tree/preimage preflight and clean transactional apply; V1 targeted Python test selection; V2 provenance/security cross-contract; V3 full hosted CI24 at the actual post-overlay SHA for shared artifact trust changes; V4 manual native authenticated collection and independent Android SDK package inspection. FC-09 signing, device, consumer, release/production promotion are independent **OPEN** gates.
- A validated embedded native receipt is producer-reported evidence, not a trusted reproducible Android binary attestation. A GitHub-hosted APK successful build is not proof of source-to-binary reproducibility or production certification.

## R35-R1 — native Termux validation hardening

The original R35 overlay selected the correct targeted tests but failed on the
operator's Termux native Python validation path for two independent reasons:
`tools/fc08_host_capture.py` uses a *real sibling script* import that the
project dependency audit mistakenly classified as an undeclared PyPI package,
and `uv run python -m pytest` did not install the pinned test-only pytest package.

R35-R1 fixes both without skipping validation:

- The dependency audit recognizes a **regular sibling `.py`** imported by a
  script inside `tools/` or `scripts/`. It does **not** exempt symlinks,
  unknown imports or package-level dependencies. Negative tests cover these.
- The native Python pytest runner accepts an explicit
  `[python].test_requirements` path and executes `uv run --with-requirements`
  against that pinned repository-owned file. The Devtool repository declares
  `devtool-test-requirements.lock` (pytest 9.0.2); runtime dependencies do not
  gain pytest, and no new Gradle or local Android execution is introduced.
- The replacement R35-R1 overlay keeps the original strict R34-R1
  commit/tree preflight, all original artifact and provenance checks, and
  native targeted pytest selection, expanding coverage to Python source audit
  and runner regressions.

The previous failed transaction does not authorize an overlay with a new
unreviewed base; verify it rolled back and `HEAD` still matches the published
R34-R1 baseline before applying the replacement. R35-R1's local Linux source
and targeted test results are not native Termux acceptance: only the operator's
actual installer and subsequent GitHub run can supply those receipts.

## R35-R2: bootstrap-safe targeted validation

Native self-overlay validation is planned by the *currently installed* Devtool,
not by source code in the uncommitted transaction worktree. R35-R1 changed
`PythonRunner._pytest_spec`, but that code was not yet installed when the
R35-R1 validation ran; the old planner still emitted plain `uv run python -m pytest`.

The R35-R2 overlay therefore uses the **existing `validation.tests` artifact
executor** to run exactly 11 selected pytest test modules with
`uv run --with-requirements devtool-test-requirements.lock python -m pytest`.
The exact test-file selectors are embedded in the required `validation.tests` command.
The legacy auto-Python-test phase is disabled (`validation.test=false`), while the
source/dependency restore phase remains enabled. The explicit artifact test is
required and fails the transaction on nonzero exit; no `--no-validate`, no
user-global pytest installation and no Gradle on Termux.

R35-R1 failed transactions must be inspected/rolled back normally before
applying R35-R2 against original R34-R1 clean HEAD.
