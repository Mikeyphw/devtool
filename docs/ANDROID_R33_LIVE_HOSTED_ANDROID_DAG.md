# ANDROID-R33: Devtool-only live hosted Android native DAG

Scope: **Devtool source only**. No xbsctl, dustui, MMRL, Telegram or Relatório repository changes.
No Gradle runs in Termux or in the source-only tests. The *new* manual GitHub
Actions workflow intentionally runs real Android Gradle 8.11.1 / AGP 8.9.2 on
Ubuntu 24.04. These versions and SDK 35/JDK 17 agree with Android's published
AGP 8.9 compatibility requirements.

## Source and execution contract

- The fixture template is tracked under `tests/fixtures/r33_hosted_android/`.
- GitHub Actions checks out the exact triggering Devtool commit and verifies
  every template file still equals its corresponding Git blob.
- Hosted runners download only the official `gradle-8.11.1-wrapper.jar` binary,
  enforcing SHA-256 `2db75c40782f5e8ba1fc278a5574bab070adccb2d21ca5a6e5ed840888448046`.
- `distributionSha256Sum` pins the Gradle 8.11.1 binary ZIP:
  `f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6`.
- The fixture plus verified JAR is committed into a *deterministic ephemeral*
  Git repository. Its commit is distinct from the GitHub Devtool commit and
  is separately bound to it in each JSON evidence record.
- `compile_graph()` must admit exactly two native Android Gradle jobs:
  `node-01 :app:lintDebug` then `node-02 :app:assembleDebug` with `needs: node-01`.
- GitHub Actions runs `lint` then `build` with an actual `needs: [lint]`
  and transfers lint evidence using Actions artifact downloads. The build
  refuses any mismatched source, graph, run ID or attempt.
- Both real Gradle commands execute via `devtool.actions_graph_export run-node`,
  NOT via ad-hoc shell Gradle commands or a Termux bridge. Each node checks
  the frozen plan and its source before calling Devtool's native job runner.
- The build node verifies the exact APK bytes against Devtool's staging receipt,
  checks package ID with SDK `aapt`, and verifies the **debug** signature with
  `apksigner`. Successful evidence is uploaded for inspection.

The native RM-06 interface currently requires `GITHUB_SHA` to match the job's
Git checkout. The R33 harness uses a temporary `GITHUB_SHA=fixture_commit` in
**only its subprocess** running RM-06, and separately preserves the actual
`devtool_sha` from the immutable GitHub checkout. This is explicitly a fixture
lab, **not** a claim that the fixture source commit equals the GitHub source
commit. No core RM-06 source-equality check is weakened.

The two-job Actions harness is source-reviewed and checks the compiled Devtool
native graph. It is **not** a claim that the entire GitHub workflow was directly
created by `provider_export`: that final integration remains separate.

## Current qualification boundary

Source-only/unit tests and transactional overlay acceptance can pass without
real Android execution. Until the new workflow actually runs on GitHub and
its APK result is inspected, the following remain **NOT VERIFIED**:

- Actual Gradle/AGP build and hosted Ubuntu execution
- APK production, package identity and debug-signature evidence
- Real artifact reception and Devtool RM-06 handoff evidence

Even if hosted R33 passes, it does not confer production APK source-to-binary
provenance, trusted package-signing authority, real-device certification,
release qualification or FC-09 closure.

## Operator workflow

```zsh
cd "$HOME/Code/devtool"
git push origin main
gh workflow run devtool-android-r32-source-qualification.yml --ref main
gh workflow run devtool-full-repository-pytest.yml --ref main
gh workflow run devtool-android-r33-live-hosted-dag.yml --ref main
gh run list --repo Mikeyphw/devtool --limit 8
```

CI runtime is on GitHub, not on Termux. Analyze the `lint` and `build` job logs,
JSON evidence, APK receipt, artifact digest, and signer certificate after the
workflow has completed. Never infer real success from local source tests.
