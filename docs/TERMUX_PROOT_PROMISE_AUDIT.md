# Termux-native + PRoot campaign promise audit

This is the literal final acceptance audit for the Termux-native/PRoot campaign. `PASS` means the behavior exists in the implementation and has regression coverage. The final seal intentionally validates devtool/Python code paths without launching the Android HUD/application Gradle target.

| # | Promise | Status | Implementation / regression evidence |
|---:|---|:---:|---|
| 1 | SSH work was merged before this campaign. | PASS | Mainline contains `ssh_runner.py`; SSH regression suites run in the final seal. |
| 2 | Both SSH command mode and remote-devtool mode remain supported. | PASS | `tests/test_ssh_tmux_runner.py`, `tests/test_execution_environments.py`. |
| 3 | SSH paths do not inherit chroot/Termux path assumptions. | PASS | SSH cwd/staging contracts and path-isolation tests in `tests/test_ssh_tmux_runner.py`. |
| 4 | SSH uploads inputs and retrieves produced artifacts with SSH/SCP. | PASS | SSH artifact-transfer regressions in `tests/test_ssh_tmux_runner.py`. |
| 5 | Remote `sh -lc` payload quoting remains fixed. | PASS | SSH remote-shell quoting regression coverage. |
| 6 | Commit451 can no longer construct the dead 35.0.0 release URL. | PASS | `test_commit451_35_0_0_uses_published_35_0_1_release`, release-resolution tests. |
| 7 | Existing chroot behavior remains intact. | PASS | `tests/test_termux_chroot_runner.py` plus execution-environment regressions. |
| 8 | Native Termux is identified as Android/Bionic, not generic glibc Linux. | PASS | `test_detect_runtime_identity_requires_native_termux_executable`, API/runtime tests. |
| 9 | Termux packages are recognized as tool/provider metadata. | PASS | package probes in `termux_toolchains.py` and `tests/test_termux_toolchain_providers.py`. |
| 10 | Termux OpenJDK is a first-class provider. | PASS | Java provider discovery/version/package tests. |
| 11 | A complete project Gradle Wrapper remains preferred. | PASS | wrapper-preference and incomplete-wrapper regressions. |
| 12 | Termux Gradle is a first-class provider/fallback. | PASS | Termux Gradle probe, explicit provider, wrapperless selection tests. |
| 13 | `ndk-sysroot` is recognized but never impersonates a full NDK. | PASS | native capability tests and `test_full_android_native_build_rejects_termux_sysroot`. |
| 14 | Compiler-only native work can use Termux clang/lld/sysroot. | PASS | plain-CMake/native-provider tests. |
| 15 | Android NDK-integrated projects still require a real full NDK. | PASS | `ndkVersion`, `Android.mk`, external-native/full-NDK tests. |
| 16 | Termux CMake, Ninja and ccache are supported when compatible. | PASS | CMake/Ninja/ccache provider and runner regressions. |
| 17 | Termux Android Build Tools are capability/runtime checked. | PASS | AAPT2 capability probes and Android tool provider tests. |
| 18 | SDK Build Tools remain available/preferred where compatibility requires them. | PASS | SDK-provider arbitration and incompatible-native-tool tests. |
| 19 | External release providers resolve availability before constructing/downloading assets. | PASS | Commit451 metadata/404-before-download tests. |
| 20 | Bionic ARM64 and glibc ARM64 are distinguished. | PASS | native ELF preflight and Commit451 Termux/glibc tests. |
| 21 | Android shared-storage repositories follow allow/warn/deny policy. | PASS | shared/removable-storage classification and deny-policy tests. |
| 22 | Termux caches remain in private/backend-isolated storage. | PASS | `host-termux` cache and shared-XDG-cache rejection tests. |
| 23 | Verified artifacts can be exported to shared storage after verification. | PASS | verified-artifact export and verification-error suppression tests. |
| 24 | Termux resource defaults are phone-aware and bounded. | PASS | resource profile/memory/concurrency regressions. |
| 25 | Explicit resource settings override automatic defaults. | PASS | `test_resource_defaults_do_not_override_explicit_values`. |
| 26 | Wake locks are released on normal, failure, interrupt and timeout paths. | PASS | wake-lock lifecycle/timeout regression tests. |
| 27 | Long Gradle/dependency silence yields diagnostics without killing the build. | PASS | Gradle quiet/stall diagnostic regressions. |
| 28 | PRoot-Distro is a first-class execution environment. | PASS | parser/config/preflight/wrapper tests in `tests/test_termux_runtime_proot.py`. |
| 29 | PRoot guests do not need devtool installed. | PASS | Python helper staging and guest-tool probe tests. |
| 30 | PRoot host↔guest path mappings are explicit and validated. | PASS | bind/cwd/unmapped-path/conflicting-destination tests. |
| 31 | PRoot artifacts/helpers use binds/staging, not SCP. | PASS | repo-artifact and helper/cache bind tests. |
| 32 | Tool discovery is isolated per execution environment. | PASS | host-Termux tool rejection/resolution inside PRoot tests. |
| 33 | Detachment is supported by wrapping whole local orchestration in tmux, not PRoot detach. | PASS | PRoot explicitly rejects detached modes; doctor reports tmux availability. |
| 34 | Host, chroot, SSH and PRoot regression suites pass together. | PASS | final non-Android Python suite and cross-backend final validator. |
| 35 | CLI, config templates, help/completion, manpage, doctor and implementation agree. | PASS | final integration docs/config/completion/manpage contract tests. |
| 36 | Every campaign feature has regression coverage. | PASS | phase-specific test modules plus `test_termux_final_integration.py` and this audit contract. |
| 37 | A literal promise-vs-implementation audit is shipped. | PASS | This document, checked by `test_termux_campaign_promise_audit.py`. |
| 38 | Gaps found by the final audit are fixed before completion. | PASS | Final seal and post-seal native validation fix live-progress partial totals, explicit-XDG log isolation (including Android Tasker/phone-mirror defaults), dynamic JDK package advice, detailed Android/NDK/backend doctor rows, hermetic Android-marker scrubbing, normal native-Termux validation HOME/TMPDIR isolation outside the repository, automatic `pexpect` restoration for imported PTY tests, short private temporary roots for AF_UNIX regression tests with fixtures rooted in the configured temporary directory, uv-native sdist rebuilds during post-build package verification, installed-wrapper CLI verification that does not launch chroot/GUI entry points, interruption-aware structured report parsing, and atomic rollback of Git HEAD/index state after failed staging, commit, or post-commit verification. |

## Final-seal scope

The final validator runs the complete non-Android-app Python/devtool regression suite and targeted cross-backend suites. It does **not** launch `android/devtool-live-hud` Gradle build/lint/test tasks. Android runner/provider behavior is covered at the Python command-planning, provider-selection, artifact, and compatibility layers.

Normal native-Termux `devtool validate` is also a supported acceptance path: its isolated Python test HOME/TMPDIR must not sit beneath the repository, must not inherit the repository config/Git boundary into temporary fixture projects, and must use Termux-private writable temporary storage for local IPC fixtures.


### Post-seal build-stage correction (overlay 08)

- **Normal validation reaches build after the complete Python suite:** confirmed from the native-Termux validation report: 1,327 tests passed before the build-stage failure.
- **uv build frontend is self-consistent:** uv-managed projects now plan `uv build --out-dir <dist>` instead of `uv run python -m build`, so validation does not rely on an undeclared `build` module in the project environment.
- **Build backend contract remains PEP 517 based:** `uv build` consumes the project's `[build-system]` metadata and creates sdist/wheel artifacts.
- **Offline semantics preserved:** Devtool's Python `offline` option is forwarded to `uv build --offline`.
- **Non-uv behavior unchanged:** other Python managers continue to use the pre-existing `python -m build` invocation.


### Post-seal package-check correction (overlay 09)

- **Primary build remains authoritative:** the successful uv build still produces the wheel and sdist consumed by the package gate.
- **sdist rebuild uses the uv frontend:** uv-managed projects verify the source distribution with `uv build --wheel <sdist> --out-dir <dir>` instead of `python -m pip wheel`, avoiding a second pip build-isolation resolver and its independent network/backend bootstrap path.
- **CLI verification is packaging-safe:** clean no-dependency wheel installs verify that every declared/configured console-script wrapper exists and is executable; package verification no longer launches chroot, GUI, or other application entry points merely to inspect packaging integrity.
- **Interruptions remain control flow:** an interrupted package-check skips structured package-result parsing instead of inventing a `PYTHON_PACKAGE_RESULT` validation error.
- **Package depth is preserved:** clean wheel installation, configured import smoke, sdist-to-wheel rebuild, rebuilt-wheel installation, archive/RECORD validation, metadata parity, and optional twine/reproducibility checks remain intact.
