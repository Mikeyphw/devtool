# RM-15 + D1–D5: Android multi-consumer release implementation and promise audit

**Source basis:** clean post-RM-12 Devtool Git tree `4c5ad4697dcbb6e7ad43c41089bd3fd6e391b404`. RM-13 and RM-13R1 were not accepted on the user's Termux device and are **not** prerequisites or treated as complete.

**Engineering disposition:** Implemented under registered, bounded Android consumer contracts. The precise BKX application ID, existing LIFE certificate SHA-256, signing identity, workflow and artifact IDs must be configured locally from real BKX records; there are **no invented credentials or forced re-keying**. Live GitHub APK builds, physical device compatibility and Skybind acceptance need independent evidence; they are *not* engineering deliverables silently claimed here.

| Promise | Behavior implemented | Positive and adversarial evidence |
|---|---|---|
| RM15-1: multiline Kotlin/Groovy `include(...)` and static module relocation | `actions_android_model.discover_modules` parses bounded static multiline `include`, Groovy include, and known `projectDir = file(...)` forms; refuses dynamic input and external paths | `test_multiline_gradle_relocation_convention_flavors`, `test_nested_android_plan_rejects_library_and_unregistered_variant` |
| RM15-1: catalogs and convention plugins | Reads tracked `gradle/libs.versions.toml` plugin aliases and reviewed `build-logic/src/main/kotlin/*.gradle.kts` for application plugin classification; never executes Gradle at discovery | Same tests, existing `tests/test_actions_05_android_builds.py`; dynamic cases fail closed |
| RM15-2: reviewed custom variant model | Case-sensitive, discovered `<flavor><BuildType>` variants including `embeddedRelease`, `liteRelease`, and `officialDebug`, with actual build type distinct from flavor; unknown variants and library-only modules refused | `test_multiline_gradle_relocation_convention_flavors`, `test_nested_android_plan_rejects_library_and_unregistered_variant` |
| RM15-2: custom GitHub workflow | Separate reviewed `.github/workflows/devtool-android-variants.yml`, preserving original HUD debug/release workflow. Emits workflow source/run-attempt bound APK/AAB hash manifest | `test_workflow_custom_release_is_protected_and_16k`, `test_custom_variant_requires_run_bound_member_manifest` |
| RM15-3: custom APK/AAB collection identity | Collection requires named member manifest, exact source SHA, attempt, variant, member bytes; release signing state independently verified against package and cert; no automatic qualification | `test_custom_variant_requires_run_bound_member_manifest` and original Android collection suites |
| RM15-4: Gradle JVM migration | Read-only preview by default; explicit apply only with source hash, atomic file replacement; effective protected target memory limit never lowered and unreviewed JVM flags refused | `test_protected_gradle_jvm_migration_previews_and_is_atomic` |
| D1: generic unsigned producer / importer | Exact registered repository, workflow, run SHA, run attempt, artifact ID/name and candidate bytes; old HUD contract preserved. Multiple consumers uniquely selected | `test_unsigned_variant_manifest_source_artifact_and_attempt`, `test_two_variants_import_independently_with_real_receipts`, existing HUD ACTIONS-10 suite |
| D2: 16 KiB ZIP and native segments | `zipalign -P 16 -f 4`, sign, `zipalign -c -P 16 4`, Android signing verifier and ELF PT_LOAD page-alignment inspector (rejects 4 KiB alignment) | `test_signed_variant_uses_registered_identity_16k_and_preserves_rclone`, `test_page_aligned_native_elf_is_verified_not_assumed` |
| D3: existing authorized LIFE identity | Consumer's signing target, identity ID and exact certificate are mandatory; `local_verify` compares actual APK package+certificate, not HUD project path. No new keystore, no CI secrets | `test_signed_variant_uses_registered_identity_16k_and_preserves_rclone` (correct identity and wrong-identity refusal); original HUD tests |
| D4: custom flavor and release build type | Variant preserved end-to-end and underlying build type remains explicit; unsigned release candidates never reported as signed/qualified | `test_nested_android_plan_rejects_library_and_unregistered_variant`, `test_custom_variant_requires_run_bound_member_manifest`, `test_two_variants_import_independently_with_real_receipts` |
| D5: variant provenance, rclone inclusion and exclusion | Contract hash bound at import and sign; remote source/run/attempt/artifact/variant and both APK SHA-256 values + LIFE certificate attached to receipt. Registered required/forbidden file rules checked before and after signing. | `test_two_variants_import_independently_with_real_receipts` (registry rewrite fails), `test_signed_variant_uses_registered_identity_16k_and_preserves_rclone`, `test_registry_variants_and_package_invariants` |
| No parallel build/release engine | Existing ACTIONS-06 dispatch, native DAG, artifact registry, LIFE signing and HUD workflows preserved. No local Gradle compilation during import/sign | Existing ACTIONS-10 tests + implementation/code path inspection |

## Register two independent BKX consumer contracts

The registry is local to Devtool at `config/android-consumers.json`, with `schema_version: 1` and `consumers: [...]`. Every item requires these **exact** fields: `id`, `repository`, `workflow`, `artifact`, `apk`, `application_id`, `variant`, `build_type`, `target`, `project_dir`, `module`, `signing_identity_id`, `signer_sha256`, `required_files`, `forbidden_files`.

Create separate entries for `embeddedRelease` and `liteRelease`; **both must use the existing BKX LIFE signing identity and SHA-256 fingerprint**. Use actual BKX application ID, correct GitHub workflow and artifact names. The `required_files` list for Embedded must point to the actual APK member containing rclone; the corresponding Lite `forbidden_files` list must point to the same actual member. The illustrative test path `lib/arm64-v8a/librclone.so` is a fixture only, **not** proof of the real BKX packaging layout. Because BKX may embed rclone in assets rather than as an ELF `.so`, the rule is data-driven and exact, not globally hard-coded. If an embedded native `.so` exists, ELF 16 KiB alignment is independently enforced.

### Minimal operational sequence

1. Add the local reviewed consumer contract with real BKX identifiers, using existing LIFE identity/cert already configured; keep it in project policy (no secrets).
2. Dispatch/reuse the existing GitHub Actions workflow (or ACTIONS-06 pipeline), producing independently named unsigned Android candidate archives with `manifest.json` and APK.
3. Import each run/artifact with exact remote source SHA, `--consumer <registered-id>`, and optional download; importer verifies remote run/workflow/artifact and APK/package identity.
4. Preview `unsigned-local-sign` using each imported `registry_id`; approve explicit local sign. Signature uses only preexisting LIFE key and verifies certificate, APK identity, final ZIP and ELF alignment.
5. Inspect receipts and test both Embedded/Lite with BKX + Skybind independently. Hosted build/signing does not imply device or release qualification.

**Security boundaries:** Missing or ambiguous registry entries are rejected; 16 KiB alignment does not assert APK runtime compatibility; explicit signer IDs are not secrets and are never generated; SHA-256 validates byte identity but is not a substitute for independently authenticated GitHub/Skybind authority.

## Distinct external evidence still needed

- Real remote BKX build results and exact artifact manifest identities for both variants.
- Real Termux signing with the existing BKX LIFE identity, `zipalign`, `apksigner`, certificate verification and application install.
- Actual Embedded contains rclone, Lite excludes rclone, and Skybind acceptance for each.
- Real Studyflow/Relatório/MMRL repository configurations (fixture-driven source parser tests do not claim execution of those repositories).
- Android device qualification and any release authorization: always a separate gate, never inferred from local tests.

## Future roadmap

RM-14 Shell/Termux, RM-16 Actions UX/typed wrappers, RM-17 config migration, RM-18 multirepository adoption, RM-19 repository extraction, RM-20 onboarding/affected-target inventory. RM-13 remains unresolved on-device, and RM-06–RM-09 earlier improvement debt is preserved in the cumulative ledger.
