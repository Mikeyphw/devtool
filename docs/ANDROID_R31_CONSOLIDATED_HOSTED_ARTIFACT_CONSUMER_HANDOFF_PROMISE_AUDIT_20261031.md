# ANDROID-R31 — consolidated source-bound hosted Android artifact intake

## Reviewed baseline

- GitHub `Mikeyphw/devtool` base SHA `343331d6a1a5b863588b6b025347039149604953`; base Git tree `21396494965e4ad720ed57799eaa0ba041c16f1a` (CORE-R30-R1).
- Same-source R30 hosted evidence: CORE-R30 [41207720205](https://github.com/Mikeyphw/devtool/actions/runs/41207720205) PASS 58 tests; G1 [41207723435](https://github.com/Mikeyphw/devtool/actions/runs/41207723435) PASS 7075 tests across 614 modules/24 shards; FC08 host [41207726786](https://github.com/Mikeyphw/devtool/actions/runs/41207726786) PASS five jobs. **Historical baseline, not R31 acceptance.**
- Related roadmap windows: NEXT-09 source-discovery/custom variants (already partially implemented in ACTIONS-05/Wave B); NEXT-10 operator handoff and resume diagnostics (partially implemented in Wave D/RM-16). Combine only shared receipt/dispatch/intake contracts. Do NOT merge unrelated new language providers (NEXT-07/08) into this overlay.

## Exact implementation and constraints

1. **Dispatch accounting:** `android_plan` propagates `dispatched` from the actual RM-16 lower-level result, exposing `resumed_existing` independently. A reused journal emits `dispatched=false`, **not** a phantom real POST. Remote mutable-ref race and unknown outcomes remain fail-closed and unchanged.
2. **Signed-package handoff:** New read-only `devtool.actions_android_handoff.handoff` accepts only explicitly named `relatorio` / `telegram` consumers, exact repository and local clean-HEAD SHA, GitHub run and attempt, workflow, package ID, signer certificate, format and variant. It requires an ACTIONS-05 `android_collect` **materialized** result, previously verified archive + upstream Android manifest/signing evidence, an exact private canonical package path, rehashed bytes, and an unchanged artifact-registry event with matching metadata. It does **not** parse or sign APKs independently, and does not fetch or invoke GitHub.
3. **Operator CLI:** `tools/android_r31_consumer_handoff.py` accepts explicit pins and an existing JSON ACTIONS-05 collection receipt and writes an atomic private non-release handoff. Never echoes raw untrusted receipt, token, path, or exception text. Rejected artifacts are not modified.
4. **Hosted acceptance:** Manual non-mutating workflow `devtool-android-r31-source-qualification.yml` executes exact-source contract tests with read-only token and retains JUnit and explicit unqualified source receipt. Source audit checks clean checkout and reviewed tracked bytes, no remote POSTs.
5. **Safety:** No automatic signing, distribution, installation, third-party repo mutation, Gradle execution, consumer deployment, release promotion, false auto-retry, or relaxation of `actions_artifacts`/`actions_android` verification. Does not claim additional Android app-specific data without the actual consumer repository and successful GitHub build evidence.

## Gate/promise audit (evidence must be same-source)

| Promise | Source evidence | Required external closure |
|---|---|---|
| Existing-dispatch safe resume counted as zero new POST | Synthetic `android_plan` returning `resume_existing=true`; exact regression | R31 real Actions source-bound run / receipt audit OPEN |
| Explicit consumer repo/source/run/attempt/signer/manifest pins | Tampering tests for each identity and signer field | Real relatorio / Telegram source and GitHub build OPEN |
| Canonical registered APK bytes rechecked read-only | Real registry fixture, byte tamper, symlink and corrupt registry tests | Real signed APK and independent Android tool output OPEN |
| No false release/device/consumer qualification | Return schema and workflow assertion | Android signer/device installation/consumer rollback OPEN |
| Hermes-safe import | R31 tests use `devtool.*` directly, never `from tools import ...` | Real Termux acceptance OPEN |
| CLI errors redact secrets | Bounded code categories, never print original exceptions | User Termux CLI execution OPEN |
| Exact base, atomic preflight & transaction | ZIP manifest, original hashes and local install test | User Termux apply OPEN |
| Formal FC-09 security promise and release | Explicitly false | OPEN: independent promises, provenance, device and authorized signer |

## Recommended consumer-side sequence

1. Adopt reviewed workflow in the **actual** consumer GitHub repository; select project/module/variant using ACTIONS-05 static discovery and plan. Explicitly pin current local SHA to remote ref; do not dispatch before review.
2. Dispatch from that consumer repo (never duplicate an uncertain request). After success, retrieve via ACTIONS-04, inspect APK signer+manifest and `android_collect` with `materialize=True`; record exact run/attempt, expected package and signer fingerprint.
3. Run R31 handoff with explicit consumer and matching repository, HEAD, run/attempt, signer fingerprint; retain receipt. It remains a *non-release, non-installation* evidence transfer.
4. Validate actual on-device installation, consumer behavior and rollback independently before promoting any release/FC09 flag.
