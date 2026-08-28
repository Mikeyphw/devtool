# Build / Package Promise Audit

Final acceptance audit: 2026-08-28.

This matrix traces the complete build/package overhaul promise set to the implementation and regression evidence in this repository. A promise is marked **PASS** only when the behavior is connected to the normal execution path, not merely present as an unused helper.

| # | Promise | Status | Implementation evidence | Regression evidence |
|---|---|---|---|---|
| 1 | Release validation must actually collect and verify the Android package required by the `release` profile. | **PASS** | `devtool/validation_profiles.py`, `AndroidGradleRunner` package phase, `devtool/artifact_pipeline.py` | `test_builtin_release_profile_reaches_android_package_artifact_contract`, `test_android_resumable_validation_verifies_artifacts_in_package_phase` |
| 2 | Artifact collection/verification must be runner-neutral, including Python registry participation. | **PASS** | `devtool/runners.py` invokes `collect_and_process_artifacts()` from metadata/config instead of hard-coded runner names; `devtool/artifact_pipeline.py` owns runner adapters. | `test_declared_artifacts_make_generic_build_output_runner_independent`, Python package tests |
| 3 | `package` must mean distributable output -> verification -> registration; Python must build then deep-check before final registration. | **PASS** | `PythonRunner.build_specs(..., "package")` defers copy/registration on the build spec and registers through `package-check`. Other first-class package specs set collect/verify/required metadata. | `test_python_package_build_defers_copy_and_registration_until_package_check` |
| 4 | Android and plain JVM Gradle must implement `devtool package`. | **PASS** | `AndroidGradleRunner`, `JvmGradleRunner`; JVM Gradle uses the shared structured Gradle event init script and a distributable-only fallback. | `test_android_package_and_release_validation_verify_artifacts`, `test_jvm_gradle_package_uses_structured_events_and_collects_jar_fallback` |
| 5 | Artifact history must be immutable production events rather than mutable path cache entries. | **PASS** | `devtool/artifact_registry.py` schema v2 appends a new event for every registration; current verification is stored separately in `verifications`. Provenance fields are promoted onto each record. | `test_registry_registration_is_immutable_even_for_same_path`, `test_verification_is_append_only_and_does_not_mutate_production_record` |
| 6 | Registry writes must be concurrency-safe, atomic, durable, merge-aware, and preserve corruption. | **PASS** | Cross-platform lock file, unique temp files, `fsync`, atomic replace, corruption backup, append-only merge in `save_registry()`. | `test_registry_corruption_is_preserved_instead_of_silently_lost`, `test_save_registry_cannot_rewrite_or_drop_existing_events` |
| 7 | Managed artifact name collisions must be explicit: safe `unique` default plus `replace` and `fail`. | **PASS** | `_collision_destination()` / `atomic_copy_artifact()` and common CLI option. | `test_atomic_copy_unique_and_fail_collision_modes`, CLI contract test |
| 8 | Gradle artifacts must have task-level provenance and distinguish produced/reused/discovered output. | **PASS** | `devtool/resources/gradle/devtool-events.init.gradle` emits `artifactOutputs`, task outcome and `didWork`; `_gradle_event_candidates()` maps these to artifact state and producer task. | JVM Gradle structured-event/fallback test plus existing Gradle event tests |
| 9 | Failed builds must never register old/cached output as a successful artifact for that run. | **PASS** | Registration is gated on `returncode == 0` and zero artifact errors; failure records are diagnostics only. | `test_failed_command_does_not_register_preexisting_artifact` |
| 10 | `build` and `package` must share output-dir/name, collision, and verification controls. | **PASS** | Common CLI parser/config override path in `devtool/cli.py`; completion/docs updated. | `test_build_and_package_share_artifact_output_verification_cli` |
| 11 | Python registry entries must retain semantic wheel/sdist verification metadata, not just file hashes. | **PASS** | Package report records are propagated through `artifact_pipeline` into immutable registry metadata. | `test_python_registry_keeps_semantic_package_metadata` |
| 12 | First-class ecosystems must use native package defaults: .NET, Node/Bun, Cargo, Go, Maven, CPack, Meson. | **PASS** | `project_runners.py`: `dotnet pack`, package-manager `pack`, `cargo package`, explicit Go output dir, Maven `package`, CPack output dir, `meson dist`; cold CMake/Meson configure first. | `test_dotnet_package_uses_native_pack`, `test_dotnet_pack_honors_release_variant`, `test_bun_package_uses_bun_pm_pack`, `test_native_package_defaults_cover_rust_go_maven_and_node`, cold CMake/Meson tests |
| 13 | Artifact copies must be atomic and hash checked, with bytes flushed before final rename. | **PASS** | `atomic_copy_artifact()` copies to a unique temp file, `fsync`s it, compares SHA-256, atomically replaces destination, and `fsync`s the directory. | collision/copy regression plus full suite |
| 14 | Auxiliary release archives must be reproducible; root module packaging must not mutate source. | **PASS** | `build-root-bridge-module.sh` stages into a private tree, normalizes modes/timestamps, verifies ZIP, `fsync`s and atomically replaces output. `SOURCE_DATE_EPOCH` defaults to Git commit time. | `test_root_bridge_packaging_is_reproducible_and_does_not_chmod_sources` |
| 15 | `build-android-app.sh` must stop manually copying Gradle APK output and consume Devtool's managed artifact result. | **PASS** | Wrapper invokes `devtool build -o/-n`, assigns a run ID, and resolves only the APK registered by that exact run. | `test_android_wrapper_resolves_artifact_from_current_run_only` |
| 16 | Final release candidate must have one authoritative manifest/checksum inventory covering source, Python, root module, optional desktop and Android assets, with verification/provenance. | **PASS** | `tools/make-release-candidate.sh` builds assets before `tools/build-release.py`; manifest records per-asset verification, producer, Git HEAD and source epoch; Android release can require `apksigner`. `SHA256SUMS` and `SHA256SUMS.all` render the manifest inventory. | strengthened `test_ov16_build_release_manifest_is_hash_checked_and_deterministic` |
| 17 | Reproducible timestamps must derive from Git commit time unless `SOURCE_DATE_EPOCH` is explicitly supplied. | **PASS** | `tools/build-release.py`, `build-root-bridge-module.sh`, `pack_repo.sh`, release-candidate script. | release/root-module tests and existing release-seal tests |
| 18 | Successful artifact summaries and JSON must expose useful identity/provenance, including type, size, hash, variant, verification and registry ID. | **PASS** | `format_artifact_summary()` and `build_json_summary()` expose the same artifact items and registry IDs. | `test_artifact_summary_and_json_include_variant_verification_and_registry_id` |

## Audit fixes found after the first delivery

The first delivered archive passed its focused tests but the acceptance trace found gaps that those tests did not cover. This final audit corrected all of them:

- `artifact verify` no longer mutates an original production record; it appends an immutable verification event.
- generic `save_registry()` can no longer overwrite/drop existing events from a stale caller.
- JVM Gradle is no longer routed through the Android artifact verifier; its structured Gradle output/fallback path is now live.
- Gradle structured output recognizes plain `.tar` and other distributable archive/package suffixes, and plain tar output receives structural verification.
- artifact registry entries expose producer/command/target/variant/state/producer-task/verification and canonical/source/exported paths directly as provenance fields.
- human artifact summaries include variant and registry ID; JSON includes a stable top-level artifact aggregate.
- atomic artifact copies now explicitly `fsync` copied bytes before hash comparison/rename.
- root bridge packaging now uses an isolated staging tree and durable atomic output.
- the Android wrapper resolves only an artifact from its own `DEVTOOL_RUN_ID`, preventing stale successful APK selection.
- release manifest entries now include per-asset verification and provenance rather than hash-only inventory data.
- Maven multi-module targets and JVM Gradle archives have explicit collector coverage.
- `.NET pack` honors configuration variants while retaining normal restore behavior on a clean checkout.

## Final seal

The final archive is accepted only after Python bytecode compilation, shell syntax checks, the focused build/package/release tests, and the complete pytest suite all pass from the final source tree.

**Final audited seal:** 1,946/1,946 collected pytest tests passed, plus 59 subtests passed. Python bytecode compilation and shell syntax validation also passed.
