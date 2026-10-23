# RM15-HF01 — Unsigned-first Android builds and canonical variant profile override

Source-bound to the **post-RM14** Devtool Git tree `fdafde4bf2932a297ede8667f2527e6282573be7`. RM13/R1 did not qualify on device and are not prerequisites.

## Engineering contract, with behavioral evidence

| Promise | Implementation | Mandatory proof |
|---|---|---|
| No hosted secrets required to **build** release candidates | `.github/workflows/devtool-android-variants.yml` dispatch input `signing_mode: local` is default; release Gradle execution explicitly passes `-Pdevtool.unsignedReleaseCandidate=true`; producer must honor it; unsigned staging has no GitHub secret requirement | `test_workflow_bash_syntax_and_secret_free_release_task`, `test_real_shell_local_unsigned_candidate_requires_no_secrets`, `test_unprotected_ref_allowed_for_unsigned_but_not_hosted_signing` |
| Signed release workflow remains available without becoming mandatory | `--signing-mode github` is explicit; protected ref and existing GitHub keystore secrets are required only on this route; 16-KiB zipalign/apksigner path unchanged | `test_explicit_github_signing_requires_existing_secrets`, `test_explicit_hosted_signing_still_uses_16k_and_reports_nonqualified` |
| Source/model-bound release choices | ACTIONS-05 `plan`/`run` default to local, select unsigned-capable custom workflow for release and send `signing_mode`, refuse explicit incompatible signed-only workflows | `test_release_planning_uses_local_unsigned_no_secrets_by_default` |
| Custom flavors selectable from CLI | Canonical parser accepts reviewed flavor names; new `--signing-mode {local,github}` option on both commands, generated CLI surfaces are regenerated | `test_cli_defaults_local_with_explicit_hosted_option` and `tests/test_core_x04_generated_surfaces.py` |
| ACTIONS-05 can consume existing Android variant profiles | Canonical `.devtool.toml` repository document + `android/variants.py` resolver provide an exact, reviewed variant profile when static inspection has no entry. Requires tracked configuration, matching module/build_type/flavor/task spelling | `test_explicit_reviewed_android_variant_profile_overrides_static_limit`, `test_untracked_reviewed_profile_cannot_override_gradle` |
| No silent signed candidate in local path | Hosted local mode refuses APK that passes `apksigner verify` and AAB with known JAR signature blocks; ACTIONS-10 independently validates APK ZIP/v2+, Android package, debuggable flag, native segment alignment and hash before LIFE signing | `test_local_unsigned_workflow_refuses_pre_signed_gradle_output`; existing `tests/test_actions_10_unsigned_candidate.py` |
| Signed/unsigned provenance must be distinct | Generated manifest records source SHA, run attempt, variant, underlying build type, signing_mode, signing_state and `qualified_release: false` with exact output digest | `test_real_shell_local_unsigned_candidate_requires_no_secrets`, `test_explicit_hosted_signing_still_uses_16k_and_reports_nonqualified` |
| LIFE local signing and previous HUD behavior remain intact | `actions_unsigned.py` unchanged; imported registered unsigned APK still uses existing LIFE key/cert, contract SHA-256, alignment and package checks; no key generation; no GitHub secret dependency | `test_native_life_signing_still_does_not_access_github_secrets`, `tests/test_rm15_android_consumers.py`, `tests/test_actions_10_unsigned_candidate.py` |
| Atomic source-bound installation | File byte locks + exact post-RM14 Git tree + native target preflight + required tests + `rollback_on_failure` | Transaction acceptance report in bundle |

## Important division of responsibilities

The general-purpose variant workflow can publish an **unsigned, unqualified** APK/AAB without secrets. It does **not** pretend that this generic artifact automatically satisfies ACTIONS-10's exact two-member (`manifest.json` + registered APK) import contract. For automated **GitHub → local LIFE signing**, a registered consumer such as BKX must use its **own audited unsigned workflow** with that exact ACTIONS-10 manifest and artifact name; invoke `actions android unsigned-run --consumer <id>`, followed by `unsigned-import` and `unsigned-sign`. Do not use ordinary ACTIONS-05 signed-release `collect` to import an unsigned APK; it correctly requires signed-release evidence. This is a deliberately fail-closed trust boundary, not a request to add signing secrets to GitHub.

No new signing engine is introduced; existing ACTIONS-06 dispatch, GitHub download, SHA-256, artifact registry and LIFE remain authoritative. A successful unsigned workflow or local signature never implies BKX/Skybind device or release qualification.

## Restrictions that are not missing engineering promises

Static Gradle inspection is intentionally bounded. The reviewed profile override handles undiscoverable **single-dimension** `<flavor><Release|Debug>` variant names with explicit Gradle module, flavor and build type; it does not execute arbitrary Gradle convention plugins, infer novel build types, or claim every multi-flavor combination. A mismatched, untracked, ambiguous or syntactically unsafe profile fails closed.

## Roadmap status (after this hotfix)

RM13/R1: failed on device/unresolved. RM16: Actions UX next. RM17: single-repository migration and atomic upgrades pending. RM18: multi-repository coordination without extraction pending. **RM19: cancelled at user request, no repository splitting/extraction.** RM20: in-place onboarding pending. Older RM06–RM09 engineering debt and real BKX/Skybind qualification remain independently tracked.

**Producer contract:** the generic unsigned workflow asks Gradle to honor `-Pdevtool.unsignedReleaseCandidate=true`; no universal Gradle CLI flag can strip arbitrary application signing configurations. A project that does not implement that property must be changed to create truly unsigned release output. The hosted selection also rejects a signed APK and does not bypass ACTIONS-10 independent verification.
