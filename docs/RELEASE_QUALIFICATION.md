# Release qualification

Slice 5 turns the portable source-contract checks into a native release gate.
The canonical versions live in `release/versions.json`; every package and
frontend must match that file before a release can proceed.

## Local gate

Run the portable gates on any Python 3.11+ host:

```bash
python3 -m pip install -e . pytest
python3 tools/release-qualify.py --core-only
```

Run every gate that is available on the current host:

```bash
python3 tools/release-qualify.py
```

Use `--require-qt` or `--require-android` on a release machine. Those switches
turn an unavailable native toolchain into a failure instead of a documented
skip. Add `--device-tests` when an emulator or physical device is connected.
The machine-readable report is written to `dist/release-qualification.json`.

## Qt gate

The Qt project exposes CTest smoke tests for both executables. They verify the
compiled version and launch the complete QML engines with an isolated runtime,
offscreen platform, and software renderer:

```bash
cmake -S desktop/devtool-control-center -B build/qt -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DDEVTOOL_ENABLE_TESTS=ON
cmake --build build/qt --parallel
ctest --test-dir build/qt --output-on-failure
```

The Debian package builder normalizes staged timestamps using
`SOURCE_DATE_EPOCH`, produces a checksum, and can validate package metadata and
required payloads:

```bash
BUILD_DIR="$PWD/build/qt" \
desktop/devtool-control-center/package-deb.sh --skip-build --verify
```

## Android gate

Release APKs are never allowed to fall back to the debug key or remain unsigned.
Set all four values before invoking a release task:

```bash
export DEVTOOL_RELEASE_STORE_FILE=/secure/devtool-release.jks
export DEVTOOL_RELEASE_STORE_PASSWORD='...'
export DEVTOOL_RELEASE_KEY_ALIAS=devtool
export DEVTOOL_RELEASE_KEY_PASSWORD='...'
cd android/devtool-live-hud
./gradlew lintRelease testReleaseUnitTest verifyReleaseApk
```

`verifyReleaseApk` depends on `assembleRelease` and runs build-tools
`apksigner verify --verbose --print-certs`. The repository also includes local
JVM tests for command construction/import, path normalization, and run identity,
plus connected instrumentation smoke tests that install and launch
`MainActivity`.

## Deterministic source bundle

```bash
SOURCE_DATE_EPOCH=1784030400 \
python3 tools/build-release.py --verify-reproducible
```

The builder fixes ZIP timestamps, ordering, permissions, compression, and root
prefix. It writes `release-artifacts.json` and `SHA256SUMS` next to the archive.

## CI matrix

`.github/workflows/release-qualification.yml` runs:

- Python 3.11, 3.12, and 3.13 portable gates;
- Qt 6 compile/link, CTest offscreen launch, and Debian packaging;
- Android lint, JVM tests, ephemeral-key signed release, and signature checks;
- Android emulator instrumentation;
- deterministic source release generation after all preceding jobs pass.

Failure jobs retain logs, UI snapshots, lint output, test reports, and native
build diagnostics as CI artifacts.
