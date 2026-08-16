# Devtool OV-09 + OV-10 implementation report

## Scope

Combined remediation slice:

- OV-09: Android SDK/NDK/QEMU/build configuration — MR-023, MR-024, MR-025, MR-026.
- OV-10: Python package verification and Android instrumentation reliability — MR-027, MR-028.

This overlay is built on the post OV-08 snapshot whose applied-history head is `d66905b`.

## Implemented changes

### OV-09: Android SDK/NDK/QEMU/build configuration

- QEMU x86_64 sysroot cache directories now include a stable hash of the sysroot URL plus the expected SHA-256.
- Cached QEMU sysroots are reused only when an embedded `.devtool-qemu-sysroot.json` identity marker matches the selected URL and checksum.
- Newly downloaded/extracted QEMU sysroots write the identity marker before the staging directory is promoted into the live cache path.
- This prevents a stale or wrong-rootfs cache with the same archive basename from satisfying a later NDK wrapper request.

### OV-10: Python package verification and Android instrumentation

- Python package import-smoke verification no longer interpolates configured module names into Python source.
- Import-smoke checks now strictly validate dotted import names, including rejection of empty dotted segments, and call `importlib.import_module(sys.argv[1])` in isolated mode.
- Console-script verification now rejects absolute paths, `..`, forward slashes, and backslash path separators before checking the clean environment's script directory.
- Android instrumentation now records configured or discovered test packages and uninstalls them in a `finally` block when `--keep-installed` is not set, including configured-runner setup failures.
- Android instrumentation now writes a structured failure report when setup fails before the runner executes, including the configured runner/test-package identity when available.

## Contract tests added

- `tests/test_android_ndk_host_fallback.py`
  - QEMU sysroot cache identity is scoped by URL and SHA-256.
  - QEMU sysroot cache reuse requires a matching identity marker.
- `tests/test_python_support.py`
  - Package import smoke rejects invalid module names and empty dotted segments.
  - Package CLI verification rejects path-escape and backslash-separated console script names.
- `tests/test_android_instrumentation.py`
  - Failed instrumentation uninstalls the test package and writes a report.
  - Configured-runner setup failures still clean up the test package and write structured reports.
  - Instrumentation setup errors write structured reports.

## Validation run

Executed related OV-09/OV-10 and adjacent regression groups:

```text
python3 -m pytest -q \
  tests/test_android_ndk_host_fallback.py \
  tests/test_android_instrumentation.py \
  tests/test_python_support.py \
  tests/test_android_runner.py \
  tests/test_termux_native_android_providers.py \
  tests/test_ov04_detection_python_selection_contracts.py \
  tests/test_ov07_ov08_contracts.py \
  tests/test_project_runners.py
```

Result:

```text
248 passed in 19.39s
```

Also executed:

```text
python3 -m compileall -q devtool tests
```

Result: pass.
