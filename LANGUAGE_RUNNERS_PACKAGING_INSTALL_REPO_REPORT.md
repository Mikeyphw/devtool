# Overlay 14 of 15 — Language runners, selector exactness, packaging, install/root/repo integration

## Scope

This overlay continues the final Devtool remediation roadmap after Overlay 13. It closes the language-runner selector bugs from S15 and the remaining packaging, install, root bridge, and repository hygiene findings from S16. Overlay 15 remains the final validation gate.

## Implemented changes

### Language runners and selection exactness

- Normalized string booleans through `_bool_option` across .NET, Python, Node, Rust, Go, Maven, and CMake so TOML/string values such as `"false"`, `"off"`, and `"null"` no longer behave as truthy toggles.
- Added `_int_option` for Python retry/service numeric settings so malformed values fail with actionable `SystemExit` messages instead of raw `ValueError` tracebacks.
- Added `_shell_split_option` wrappers around user-supplied shell-style commands and runner arguments so malformed quotes are reported as configuration errors.
- Tightened catalog ID filtering so explicit test IDs are exact matches only.
- Hardened .NET test filter translation: explicit IDs use exact `FullyQualifiedName=...`, tags/categories stay structured, and unsafe filter-control characters are rejected unless the user supplies a native filter.
- Hardened Node selection: generic selectors are no longer blindly appended to package-manager scripts. Users must use `test_selection.tasks` for scripts or `test_selection.filter` for native runner arguments.
- Hardened Go and CTest selection: generic selectors are escaped and anchored as exact regex unions.
- Hardened Maven selection: IDs/classes/methods/paths are translated only when they can be represented as safe exact Surefire selectors, while tags map to groups/excludedGroups.
- Hardened Rust and Meson native filters with shell-split validation.
- Hardened Make selection: only Make targets from `test_selection.tasks` are accepted; generic test selectors now fail instead of being silently ignored.
- CMake and Meson now treat configure state as stale when required configure markers are missing or project configure files are newer than the cache/coredata marker.

### Packaging, install, root bridge, and repository hygiene

- `install.sh` now rejects impossible root-module and clipboard combinations before printing the plan or mutating the system.
- Installer fingerprints include wrapper JARs and file modes, so wrapper or permission drift changes the computed fingerprint.
- `--skip-core` installs now print a clear `CLI: skipped (--skip-core)` result instead of implying a CLI path was installed.
- `uninstall.sh` now supports `--prefix`, `--skip-pip`, and `--dry-run`, and cleans DotCLI fallback completion plus desktop/hud leftovers.
- `pack_repo.sh` now normalizes tar archive permissions and writes manifest/checksum files as `0644`.
- `tools/build-release.py` now includes file mode in tree hashes and writes release manifests/checksums with readable `0644` permissions.
- `tools/make-release-candidate.sh` no longer blindly assumes `python3 -m build`; it detects the build frontend first or honors `DEVTOOL_PYTHON_BUILD_CMD`.
- Root bridge module ZIP output is normalized to `0644`.
- Desktop `.deb` packaging strips Python bytecode/`__pycache__`, normalizes staged modes, and rejects package payload files unreadable by group/others.
- Android Live HUD build validates the app tree before host package, SDK, license, cache, or APK mutations; dry-runs now report that no APK was built/copied instead of saying build success.
- Root bridge execution now preserves the child Devtool exit code, marks the operation environment for privileged process discovery, records the child PID, and cancellation now terminates operation process groups as well as marked PIDs.
- Final seal repository backup hygiene now scans the workspace with the same candidate rules used by `devtool cleanup`, so exported snapshots or non-Git handoff folders still report transient backup/reject files.

- Final seal golden build-report fixture is updated to match the already-present UI/action metadata, preventing the final gate from failing on a stale golden contract.

## Tests and checks run

- `python3 -m py_compile devtool/project_runners.py devtool/test_selection.py devtool/final_seal.py tests/test_rem14_language_packaging_repo.py`
- `bash -n install.sh`
- `bash -n uninstall.sh`
- `bash -n pack_repo.sh`
- `bash -n build-root-bridge-module.sh`
- `bash -n tools/make-release-candidate.sh`
- `bash -n desktop/gtk-control-center/package-deb.sh`
- `bash -n android/devtool-live-hud/build.sh`
- `sh -n root-module/devtool-bridge/bin/devtool-root-bridge`
- `python3 -m pytest -q tests/test_test_selection.py tests/test_ts00_pytest_selection_parity.py tests/test_rem14_language_packaging_repo.py`
- `python3 -m pytest -q tests/test_project_runners.py tests/test_python_support.py tests/test_termux_native_android_providers.py tests/test_test_selection.py tests/test_ts00_pytest_selection_parity.py tests/test_rem14_language_packaging_repo.py`
- `python3 -m pytest -q tests/test_arch_final_seal.py tests/test_arch_ov08_tui_cleanup.py tests/test_project_runners.py tests/test_python_support.py tests/test_termux_native_android_providers.py tests/test_test_selection.py tests/test_ts00_pytest_selection_parity.py tests/test_rem14_language_packaging_repo.py`

Final result: 186 focused tests passed in the local handoff snapshot. Full repository validation is intentionally reserved for Overlay 15.

## Apply command

```bash
cd "$HOME/Code/devtool"

gradle --stop || true

devtool --copy --target devtool -y apply overlay \
  /sdcard/Download/devtool_rem14_language_runners_packaging_install_repo_v1.tar.gz \
  --no-validate \
  --commit "fix(devtool): harden language runners packaging and repo integration" \
  --resource-profile standard \
  --max-workers 4 \
  --cpu-limit 4 \
  --gradle-heap-mb 2048 \
  --gradle-metaspace-mb 768 \
  --memory-guard-mb 0 \
  --parallel
```

## Next overlay

Overlay 15 of 15 should be the final validation seal. It should run the full explicit final-gate test/task set, confirm no roadmap promises remain open, and produce the transfer-ready post-roadmap handoff.
