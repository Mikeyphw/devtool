# Android Operations: execution context, ADB providers, and devices

AOPS02 introduces the shared Android execution/device layer used by Devtool's
Android builder integration, instrumentation, artifact verification, doctor,
and release qualification code. Android-facing code should consume this layer
instead of independently locating ADB or parsing `adb devices` output.

## Execution context

`devtool android capabilities` resolves a side-effect-light
`AndroidExecutionContext`. It records the runtime, target, variant, Android SDK
root/source, device transport, and tool resolutions for Java, Gradle, Android
SDK build-tools, AAPT2, apksigner, zipalign, NDK, CMake, and ADB.

Each tool resolution records its provider, path, version, compatibility,
selection/rejection reason, runtime, provenance, and provider-specific metadata.
Use JSON when another tool needs the contract:

```bash
devtool --target app android capabilities --format json
```

The Android builder remains responsible for provisioning/build-time repair. The
execution context is the canonical *resolution and evidence* model; staged
`build-apk` shares the low-level ADB provider resolver through
`termux_toolchains.py` so native/chroot execution does not invent a second ADB
selection policy.

## ADB providers

`[android.adb].provider` supports:

- `auto` — select a provider that passes runtime probes.
- `sdk` — Android SDK `platform-tools/adb`.
- `termux` — ordinary ADB from Termux `android-tools`/PATH.
- `termux-adb` — the Termux USB transport-compatible `termux-adb` binary.
- `path` — an explicit `[android.adb].path`.
- `none` — disable ADB.

`auto` uses capability probes (`version` and `devices -l`), not just the first
matching filename. On native Termux it probes an explicitly resolved project SDK `platform-tools/adb` first, then falls back to `termux-adb` and ordinary Termux ADB when that SDK binary is unavailable or incompatible. On non-Termux hosts it considers SDK ADB
then PATH ADB. An explicit configured path wins when present.

Devtool never installs or adds a third-party package repository merely because
`termux-adb` was requested. Installation/provisioning of external repositories
must remain an explicit user action.

Example:

```toml
[targets.app.android.adb]
provider = "auto"
device_policy = "exactly-one"
# path = "/data/data/com.termux/files/usr/bin/termux-adb"
# serial = "..."
# preferred = "Pixel 8"
```

One-run overrides:

```bash
devtool --target app android devices --provider sdk
devtool --target app android devices doctor --provider termux-adb
devtool --target app android capabilities --provider path --adb-path /opt/android/adb
```

## Device inventory

`AndroidDeviceManager` owns `adb devices -l` parsing and normalizes:

- serial and transport ID;
- `device`, `offline`, `unauthorized`, recovery/sideload/bootloader/unknown state;
- product/model/device metadata;
- emulator versus physical device;
- USB/TCP/emulator connection type;
- optional API level, ABI, manufacturer, battery, screen, and boot state.

List devices:

```bash
devtool --target app android devices
devtool --target app android devices --enrich --format json
```

## Deterministic device selection

Supported selection policies are:

- `exactly-one`
- `serial`
- `preferred`
- `first-physical`
- `first-emulator`
- `all`

Examples:

```bash
devtool --target app android devices --policy exactly-one
devtool --target app android devices --policy first-physical
devtool --target app android devices --policy first-emulator
devtool --target app android devices --policy serial --serial SERIAL
devtool --target app android devices --policy preferred --preferred "Pixel 8"
```

`ANDROID_SERIAL` remains supported. It is treated as an input to device
selection rather than a hidden alternative implementation.

## Termux USB diagnostics

`devtool android devices doctor` distinguishes transport setup from ordinary
ADB device state. Depending on what can be observed it reports specific codes
such as:

- `termux-adb-not-installed`
- `termux-usb-unavailable`
- `termux-api-missing`
- `termux-usb-failed`
- `usb-permission-not-granted`
- `adb-provider-unusable`
- `adb-daemon-unavailable`
- `device-unauthorized`
- `device-offline`
- `usb-visible-adb-missing`
- `no-device`
- `device-ready`

The doctor is intentionally honest about what it can infer. USB enumeration is
read-only; Devtool does not automatically grant USB permission or install a
Termux:API companion package/repository during a diagnostic command.

Use:

```bash
devtool --target app android doctor
devtool --target app android devices doctor --provider termux-adb --format json
```

## Consumer rule

New Android features must use the shared layer:

```text
AndroidExecutionContext
        |
AndroidOperations
        |
AndroidDeviceManager
```

Do not add another high-level `shutil.which("adb")`, another `adb devices`
parser, or another device-selection policy in a release/test/doctor command.
AOPS03 builds signing, instrumentation orchestration, and lifecycle device operations on these services.

## AOPS03 signing and release-task policy

`devtool android signing create|list|inspect|verify|compare|import|export-certificate` uses `AndroidSigningManager`. Devtool-managed signing is task-scoped: only release-producing tasks require release credentials. Generated Gradle init scripts contain no secret values. Password values are resolved only at the final native process-spawn boundary and are excluded from planned command specs and resumable-validation state. Backends that would have to serialize those values into shell argv fail closed.

```toml
[targets.app.android.signing.release]
mode = "devtool"
policy = "release-required"
keystore = "~/.config/myapp/signing/release.p12"
alias = "release"
store_password_env = "MYAPP_RELEASE_STORE_PASSWORD"
key_password_env = "MYAPP_RELEASE_KEY_PASSWORD"
expected_cert_sha256 = "..."
```

## AOPS03 instrumentation policy

Instrumentation and connected-test admission share `disabled`, `when-device-present`, and `required`. Required mode treats missing transport, offline devices, and unauthorized devices as release-blocking conditions rather than silently disabling connected tests. Instrumentation assembly uses the staged Android builder; device selection/install/runner execution uses `AndroidDeviceOperations`.

```toml
[targets.app.android.instrumentation]
policy = "required"
keep_installed = false
capture_logcat_on_failure = true
capture_screenshot_on_failure = true
```

Selectors cover class, method, annotation, excluded annotation, size, and runner KEY=VALUE arguments.

## AOPS03 lifecycle operations and smoke gates

The Android Operations CLI provides install/replace/uninstall, launch/stop/restart/clear-data, permissions, shell, push/pull, logcat, and screenshot operations. `devtool android smoke` adds install, upgrade, cold-start, restart, restore-restart, process-death, and signed-artifact gates. All device-facing operations consume the AOPS02 device manager and provider selection instead of resolving ADB independently.
