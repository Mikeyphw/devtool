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

Use `--require-qt` or `--require-android` on a release machine. `--require-qt`
is retained as the compatibility name for the desktop GTK4/libadwaita gate in
this release; it turns missing GTK4 bindings or package verification failures
into release failures instead of documented skips. Add `--device-tests` when an
emulator or physical device is connected. The machine-readable report is written
to `dist/release-qualification.json`.

## GTK4 desktop gate

The desktop package now stages the GTK4/libadwaita Control Center and compact
HUD as the canonical desktop executables:

```bash
devtool control-center --backend gtk4 --once
devtool-control-center --once
devtool-control-center-hud --once
```

The Debian package builder no longer runs a native desktop build. It stages the Python
`devtool.desktop` modules, GTK4 launchers, desktop metadata, metainfo, and icons,
then verifies that the resulting package has GTK4/libadwaita dependencies and no
removed desktop runtime baggage:

```bash
desktop/gtk-control-center/package-deb.sh --verify
```

Ubuntu/Debian runtime dependencies:

```bash
sudo apt install python3 python3-gi python3-rich \
  gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0
```

The legacy Qt/QML source has been removed; release gates guard the GTK4-only desktop path,
but release packaging and default launchers now point at GTK4.

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
- GTK4 once-smoke and Debian packaging without removed desktop runtime dependencies;
- Android lint, JVM tests, ephemeral-key signed release, and signature checks;
- Android emulator instrumentation;
- deterministic source release generation after all preceding jobs pass.

Failure jobs retain logs, UI snapshots, lint output, test reports, and native
build diagnostics as CI artifacts.

## Validation reporting release gate

Phases 32–33 add an explicit validation release seal:

```bash
python3 tools/validate-validation-release.py
```

The tool validates `docs/validation-release-scenarios.json`, runs every mapped failure scenario, checks validation report persistence and Android history/report contracts, then executes the portable release qualification. On the release machine, require the native Android gate:

```bash
python3 tools/validate-validation-release.py --require-android --device-tests
```

The portable source gate cannot substitute for Android lint, unit tests, signed APK verification, or connected instrumentation. Missing Android SDK components are a release failure whenever `--require-android` is set.

- The complete Python regression inventory runs in four deterministic balanced pytest groups to isolate hangs while preserving complete coverage.
