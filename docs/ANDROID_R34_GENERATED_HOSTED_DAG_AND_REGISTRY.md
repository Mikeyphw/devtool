# ANDROID-R34 — Devtool graph-derived hosted Android lab + native artifact registry

Scope: **Devtool repository source only**. It does not touch xbsctl, dustui,
MMRL or any other consumer. **No Gradle in Termux**. R33-R1 is preserved as a
passing fallback/benchmark.

## Implementation

- `devtool.actions_android_lab_export` calls the **real RM-06 `compile_graph`**
  on a disposable, source-reviewed fixture. A non-executable dummy Gradle
  wrapper JAR permits purely source-only compilation. A hash-pinned, official
  wrapper is generated **on the hosted runner only** and a real Git repository
  with real wrapper is rebuilt by the previously proven R33 host harness.
- The source-only graph must contain exactly two `android-gradle` nodes:
  `node-01 :app:lintDebug` and `node-02 :app:assembleDebug`, with one native
  `node-02 -> node-01` dependency, approved Java/SDK pins and one APK output.
  Any release, signing, device, source or DAG drift fails closed.
- `python -m devtool.actions_android_lab_export export` writes the generated
  `.github/workflows/devtool-android-r34-generated-hosted-dag.yml` only if it
  does not already exist. `verify` reconstructs the workflow byte-for-byte;
  GitHub runners perform verification before provisioning Android tooling.
  Regenerate only through the exporter and review the resulting commit.
- Both workflow nodes run real native RM-06 `run-node` via the already-proven
  `tools/r33_hosted_android_evidence.py execute` harness. The second job
  downloads the exact upstream source/run/attempt-bound lint receipt.
- Once R33 verifies the APK package and debug signature independently, R34
  additionally checks `node_result`, the RM-06 staged-output receipt, the
  source/run/attempt bootstrap, the graph digest, APK digest and package/signature
  again using pinned SDK tools. It registers APK bytes using **Devtool's real
  `artifact_registry.register_paths`**, independently calls `verify_record`,
  rereads `load_registry`, exports the local registry snapshot and uploads a
  standalone APK Actions artifact for later exact-run import.
- The source and run/attempt, fixture and graph identities remain separate.
  A local Devtool registry record is **not** an authenticated GitHub-hosted
  Actions artifact. The latter requires a separate exact-run `android collect`
  using `devtool.actions_android.android_collect` and GitHub's archive validation.

## Qualification boundaries

- R34 local tests: graph/export/runtime/static/registry contract tests only.
- An actual green R34 hosted workflow is required to qualify **fixture-hosted**
  native DAG execution and fixture registry evidence.
- The temporary hosted registry snapshot and GitHub uploaded archive do not
  confer a persistent canonical registry record on the operator's Termux
  checkout. Use Devtool's **existing** ACTIONS-05 Android collection command
  with pinned source SHA, run attempt, package and signer to import and register
  in the local repo. For custom workflows, these pins are mandatory.
- Neither CI green status nor local registration authorizes production signing,
  customer apps, independently verified GitHub artifact provenance, device
  acceptance or FC-09 release qualification.

## On GitHub after applying R34

```zsh
gh workflow run devtool-android-r34-generated-hosted-dag.yml --repo Mikeyphw/devtool --ref main
gh run list --repo Mikeyphw/devtool --workflow devtool-android-r34-generated-hosted-dag.yml --limit 3
```

Once the R34 run succeeds, inspect both native job logs and the uploaded
`devtool-r34-node-01-evidence-attempt1`, `devtool-r34-node-02-evidence-attempt1`
and `devtool-r34-debug-apk-attempt1` artifacts. Collecting the APK into a
persistent registry is a separate explicit action, not automatic promotion.
