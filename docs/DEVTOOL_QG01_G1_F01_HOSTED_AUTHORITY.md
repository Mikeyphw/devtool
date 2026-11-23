# QG01-G1-F01 — Hosted Android G1 authority and fail-closed diagnostics

**State:** source candidate; native check/apply, remote commit, required CI, fresh CI24 and hosted G1 acceptance are NOT yet qualified for this correction.

## Reproduced production failure

- Baseline: `Mikeyphw/devtool` PR #47 HEAD `4ca77b2e2986cf71050142256c15779ff6b5928c`, tree `0de9b7f894951278c7a41ee049fb33fac8861d62`.
- Hosted G1 run: GitHub Actions `45176122529`, attempt 1, `workflow_dispatch`, status `failure`; `candidate-consumer` passed, `android-g1-hosted` failed.
- Host provisioned Java 17, Android SDK / adb, Gradle 8.11.1 and installed Devtool. The nested Android G1 gate returned `rc=1`, which `g3r_hosted_android` converted to `HOSTED_ANDROID_G1_FAILED rc=1`. No hosted G1 proof archive was uploaded.
- The source-level deterministic admission defect: `devtool.android_g1_gate._tool()` only authorizes native Termux or an expressly **test-only** override; the production hosted Linux runner satisfies neither.
- The exact child stderr was **not available** from GitHub's job logs because the parent captured it to an unuploaded runner-local file. Do not falsely claim to have observed that text.

## Corrective contract

- Admit the existing real Gradle producer **only** for the source-bound GitHub hosted G1 workflow's Linux job: explicit GitHub event/job/repository/attempt/run identifiers, actual checked-out HEAD and clean workspace. Never admit an ordinary Linux shell, a push-triggered G3R run, an unverified checkout, or a second workflow attempt.
- This is an execution-authority guard, not a remotely authenticated receipt. The existing native `g3r-remote-resume` independently checks GitHub run/workflow/source/attempt and the authenticated hosted artifact's bytes and receipt.
- Never use or set `DEVTOOL_ANDROID_G1_ALLOW_TEST_HOST` in production. Never execute Gradle locally in the operator, checks, or Termux. The real Gradle gate runs **exclusively in the GitHub Actions Linux job**.
- Preserve fail-closed behavior for all deeper G1/device/runtime checks. Add a distinct non-qualifying, bounded, sanitized failure artifact (`g3r-android-g1-failure`) without exporting child stdout, stderr, environment or secrets. Its absence/presence cannot qualify hosted G1; only the strict existing successful `g3r-android-g1-hosted` receipt can.
- Regression tests cover accepted host scope, denied context/source/WIP/attempt, no ordinary-linux Gradle, sensitive-output non-disclosure and failure-not-acceptance semantics.

## Acceptance protocol

Read-only native preflight → native Devtool apply/validate/commit/session → workflow-aware App push and existing PR #47 update → six required CI jobs on new SHA → fresh 24-shard CI24 attempt with all 25 archive proofs → **one** new GitHub-hosted G1 dispatch tied to the new SHA → G1 job, artifact receipt and archive authentication → native G3R 13 independent lanes → FS03 strict final seal. Each step must stop on ambiguity; do not reuse the failed run for the new commit. No merge, release, signing or device installation is authorized.

The original v0.6.5 operator, PR branch and previous session `9ff659bac5b14788910fa575089e27cb` remain preserved. The new overlay is additive and must not replay QG01-CI24-F01 apply or old CI24/G1 dispatch.
