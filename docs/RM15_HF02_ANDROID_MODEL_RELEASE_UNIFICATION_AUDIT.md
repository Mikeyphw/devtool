# RM15-HF02 — Android Gradle model and release interface unification

Date: 2026-10-23. Base: **clean post-RM15-HF01 Devtool tree** `d703844f1b1fcba0fb0d8d1c08ba94581dc302ae` (after RM-14 and unsigned-first hotfix; RM-13 is **not** required).

## Architecture and use

The original ACTIONS-05 and ACTIONS-10 **trust checks remain distinct**, but operators may use one `devtool actions android release-*` family. The release family is only a routing/orchestration facade—no second signer, no alternate artifact importer, no auto-release qualification.

- `devtool actions android discover --discovery-mode static`: read-only source inspection, the default; handles literal Kotlin/Groovy flavor dimensions and build types.
- `devtool actions android discover --discovery-mode gradle`: **explicit trusted Gradle evaluation**; executes checked-in `gradlew` and ALL configured convention plugins. It requires a clean reviewed Git revision, bounded output, project-root path checks, limited inherited environment, no signing tokens, no network-triggering tool invoked by Devtool, and source unchanged afterwards. **This is not a security sandbox. Gradle plugins have ordinary user privileges and may independently access files and network, even when Gradle is passed `--offline`. Never run this against untrusted source.**
- `devtool actions android plan --variant freeEuStaging --discovery-mode static --ref main`: proves a variant is in the checked model and passes explicit `build_type` for custom build types. Runtime mode must be opted into explicitly.
- `devtool actions android release-plan --ref main --consumer bkx-embedded`: invokes the registered consumer's reviewed ACTIONS-10 unsigned workflow and returns a registered unsigned/LIFE plan.
- `devtool actions android release-run --ref main --consumer bkx-embedded --yes`: dispatches only the registered producer. No GitHub keystore is required.
- `devtool actions android release-import --consumer bkx-embedded --run-id N --artifact-id N --expected-sha SHA --download`: invokes ACTIONS-10 immutable candidate import/verification, not generic ZIP collect.
- `devtool actions android release-sign --consumer bkx-embedded --registry-id ID --yes`: signs locally through the **existing LIFE identity**, verifying against the imported candidate's registered consumer. The operation runs offline from GitHub APIs.
- `release-plan` without `--consumer` may plan a generic unsigned variant workflow, but **cannot be upgraded to ACTIONS-10 import** until a registered exact producer/consumer contract exists. It never claims a signed or qualified release.

Custom build types such as `staging` use the same unsigned-first default for non-debug variants. They require an exact reviewed model/profile and unambiguous run/attempt/artifact identity. Existing `debug` and `release`, and BKX's `embeddedRelease` and `liteRelease`, remain unchanged.

## Original request promise checklist

| Engineering promise | Verification / implementation | Status |
| --- | --- | --- |
| Static discovery of multiple flavor dimensions | Reads literal `flavorDimensions` and per-flavor `dimension`, builds bounded Cartesian product in declared dimension order; rejects missing/unknown dimensions, collisions and excessive matrices | Delivered, bounded static DSL |
| Custom build types beyond debug/release | Reads named `buildTypes`, builds variant matrix, reviews exact `build_type` identity and uses task/manifest binding | Delivered, bounded static DSL |
| Arbitrary convention plugins | Explicit `--discovery-mode gradle` evaluates real Gradle project code with a checked-in wrapper on reviewed clean source; verifies bounded result; never implicit | Delivered as opt-in trusted execution; no sandbox claim |
| Safe planning with dynamic Android model | Source/digest checks; runtime model validates names, variants, project containment; source changes after execution rejected | Delivered |
| Hosted custom build-type execution | Workflow has optional `build_type` dispatch input, validates exact suffix, selects build task/artifact folder, publishes signed/unsigned manifest | Delivered locally; live GitHub run pending |
| No hostage to GitHub signing secrets | Non-debug default `local` unsigned build; `github` signing only explicitly selected on protected refs | Preserved |
| ACTIONS-05 / ACTIONS-10 unified operator interface | `release-plan`, `release-run`, `release-import`, `release-sign` with exact registered consumer branch and generic plan branch | Delivered |
| No laundering generic ZIP to LIFE | Generic candidate ineligible for registered import; exact consumer/producer required; registered route delegates to existing verifier and signer | Delivered |
| Registered custom build type | Registry permits non-debug reviewed build type; retains consumer signing certificate and identity authorization | Delivered |
| Certificate/signing/16 KiB/release authority | Existing ACTIONS-10 verifier and LIFE signer reused unchanged. No new keystore, no CI signing secrets required | Preserved |
| Source-bound deployment | Exact post-HF01 tree, touched-file hashes, native Devtool mandatory validator, rollback-on-failure | Required installer checks |
| Devtool HUD and existing Android cases | Original unsigned, profile and release-mode tests must remain green | Required installer checks |

## Explicit boundaries and next acceptance

1. **Live Gradle plugin execution is not claimed** by a mocked wrapper test. A real consumer Gradle build with convention plugins is an external qualification step. Runtime discovery itself is intentionally privileged and cannot be made safe for untrusted build logic simply by setting `--offline`.
2. **ACTIONS-10 import is for registered unsigned Android APK producers only**. Generic AAB and unsigned ZIP formats remain in their existing individually reviewed paths until an exact AAB consumer contract exists. No automatic cross-format promotion.
3. No real BKX Embedded/Lite hosted builds or locally signed APKs were produced in this installation test. Exact artifact and consumer IDs, signing certificate, rclone packaged-file rules and Skybind device acceptance must still be independently checked.
4. RM-13 remains unresolved on Termux. RM-19 extraction is **cancelled by user request**. Earlier RM-06 through RM-09 engineering gaps remain tracked; do not mark them closed.

## Roadmap after this hotfix

RM-16 Actions UX/Termux control center; RM-17 configuration migration/atomic upgrades; RM-18 multi-repository coordination (without extraction); RM-20 whole-repository in-place onboarding. RM-19 cancelled. RM-13 repair unresolved.
