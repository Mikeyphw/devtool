# RM-12 — JVM/Java/Kotlin Maven & Gradle hosted provider

Source-bound against **post-RM-11R3** Git tree `643e1aacb67e2d1b49e52415747fcfccf1fcd5b9`.

## Merge-window decision

RM-12 (Maven/Gradle reactors, JDK, JUnit, JAR) and RM-13 (CMake/Meson, CTest, native compiler/ABI) were reviewed as one window. **Keep separate transactions**: toolchain provenance, source parsing and binary formats do not share meaningful unimplemented semantics. Existing native DAG and SHA-256 handoff are reused by both.

## Original roadmap promise-by-promise acceptance

| Promise | Implementation and behavioral evidence | State |
|---|---|---|
| Tracked Maven multi-module reactor | Read-only safe POM parser, namespace-insensitive literal coordinates, recursive member discovery, exact project edges and cycle/duplicate/path refusal; root `-pl <module> -am` command builds member dependencies in the same native job. `test_project_dependency_graph_and_java_matrices`, `test_maven_cycle_unsafe_xml_android_and_source_drift`. | **Implemented, reviewed forms** |
| Tracked Gradle multi-project build | Static `settings.gradle(.kts)` includes; tracked `build.gradle(.kts)` and `gradle/libs.versions.toml`; source dependency and Kotlin JVM/Java plugin recognition; source graph order; dynamic settings/composite builds and symlinks refused. `test_project_dependency_graph_and_java_matrices`, `test_gradle_static_only_android_routing_and_symlink`, `test_kotlin_jvm_gradle_and_maven_android_plugin_direction`. | **Implemented, reviewed forms** |
| No plugin execution during discovery | Only Git-tracked, bounded UTF-8/XML/TOML bytes are read. No Maven/Gradle subprocess during project discovery or planning. Explicit refusers for external entities, symlinks, executable config and dynamic Gradle settings. | **Implemented** |
| JDK toolchain declaration, mismatch refusal | Exact JDK 17/21/25, explicit source/metadata compatibility, exact installed `javac -version` before *native* execution, exact Maven/Gradle version pin and runner refusal before build. `test_toolchain_mismatch_and_bad_native_argv`, `test_native_tool_version_drift_refuses_before_job`, `test_gradle_jdk_source_mismatch_and_manifest_classpath_refusal`. | **Implemented, bounded** |
| Java/OS matrices and execution | Independent JDK version × Ubuntu/macOS cells, pinned `actions/setup-java` commit with Temurin, pinned `gradle/actions/setup-gradle` commit for Gradle, no Windows POSIX bridge claim, native job owns argv and outputs. `test_project_dependency_graph_and_java_matrices`; valid generated YAML. | **Implemented in reviewed hosts; actual hosted runs external** |
| Tooling provenance and caching | Exact tool version checked during native run; source/module/lock digests in plan; opt-in Maven/Gradle dependency store cache fixed outside source and secret paths, keyed by host/JDK/source manifest digests; no floating action tags. Same matrix test and inherited RM-07 tests. | **Implemented** |
| Reviewed check/test/JAR/package jobs | Offline, direct literal Maven/Gradle commands, native job resolver and DAG identity; reactor dependencies not silently scheduled, branch/source pinned, test failures block output staging. `test_native_junit_real_runner_handoff_and_failure` and `test_native_package_jar_desktop_and_handoff`. | **Implemented in reviewed formats** |
| JVM JUnit adapter | Maven Surefire and Gradle report discovery; XML entity, duplicate test, failure, zero and count refusals; explicit deterministic `dist/jvm-tests.xml` node output. Actual SHA handoff test. | **Implemented** |
| JAR/manifest/classpath/signature check | Byte-level ZIP entry safety, CRC, class magic, manifest/relative classpath, signature pairing; signed JAR requires `jarsigner -verify -strict` but never claims production trust. Real `javac` + `jar` fixture, corrupt class and archive refusals. | **Implemented** |
| Desktop distribution ZIP structure | Native owned deterministic `app/*.jar` plus `bin/start` launcher with reviewed manifest Main-Class; structural inspection, SHA-256 handoff, unsafe member/refusal. `test_real_jar_manifest_class_signing_refusal_and_desktop`, `test_native_package_jar_desktop_and_handoff`. | **Implemented** |
| Android-only projects | Android Gradle plugins / Maven Android SDK build declarations refuse generic JVM admission with explicit Android-provider direction, rather than being silently exported. `test_gradle_static_only_android_routing_and_symlink`, `test_maven_cycle_unsafe_xml_android_and_source_drift`, `test_kotlin_jvm_gradle_and_maven_android_plugin_direction`. | **Implemented as fail-closed routing** |
| No second scheduler, signer or release authority | Existing `provider_plan → compile_graph → run_node → stage_outputs` performs all native execution and SHA handoff. Evidence remains local/untrusted. Existing remote Windows/signing/release capability not expanded. | **Preserved** |

### Strict bounds and qualification distinction

Maven and Gradle are programmable build systems; RM-12 never executes their scripts *in discovery*. Execution runs only reviewed target-owned, offline commands with exact tool-version and source checks. Only tracked, supported POM reactor, static Gradle settings and JVM plugin forms are admitted. Unreviewed plugins/configuration, network dependency recovery, arbitrary distribution formats, cross-platform native launchers, unsigned signer claims and hidden JDK auto-provisioning fail closed. These are **policy scope restrictions**, not silently deferred features.

**Engineering vs external evidence:** Actual GitHub runner acceptance with installed SDK/Maven/Gradle, Kotlin compiler artifact outputs, macOS host execution, signer validation and release qualification are not demonstrated by local tests and must be independently qualified. Their evidence status is separate from the implemented bounded contracts. No locally generated SHA receipt is external producer authentication.

### Regression and transaction checklist

- [x] RM-12 reactor, matrix, report, package and negative behavior tests
- [x] Inherited provider/DAG regression suite
- [x] Native source/config parser and generated CLI contracts
- [x] Exact outer ZIP positive Devtool transaction — 21 tests, 6 passed stages, one commit, clean worktree, 0 transaction warnings/errors
- [x] Wrong-tree and dirty-tree preflight refusal — both rejected before mutation using freshly extracted exact outer ZIP
- [x] Post-apply forced validator failure restores exact HEAD/tree/branch/status — failed mandatory pytest validation followed by Devtool rollback
- [ ] Real Android/Termux installation acceptance (user device)
- [ ] Independent GitHub-hosted Maven and Gradle execution evidence (external)

## Upcoming overlays and retained debt

| Overlay | Scope | Status |
|---|---|---|
| RM-13 | C/C++, CMake/Meson, compiler matrix, sanitizers, CTest, ABI | Next |
| RM-14 | Shell/Termux CI and script validation | Pending |
| RM-15 | Android Gradle custom variants/JVM policy — P0 | Pending |
| RM-16 | Actions UX, typed wrappers, idempotent dispatch, Termux control center | Pending |
| RM-17 | Config migration, overlay upgrades, atomic repo transactions | Pending |
| RM-18 | Multi-repository staged adoption/recovery/rollback | Pending |
| RM-19 | Dependency-aware extraction and consumer cleanup | Pending |
| RM-20 | Whole-repo onboarding and affected target inventory | Pending |

Earlier RM-06 affected-target selection, RM-07 Windows/Python cache, RM-08 native Python managers and RM-09 Windows/.NET coverage/publish gaps remain **open in the cumulative improvements ledger** and are not qualified or silently closed by RM-12.
