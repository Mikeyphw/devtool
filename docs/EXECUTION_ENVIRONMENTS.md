# Execution environments

Project runners decide **what** command to run. Execution environments decide
**where** that command runs. The two concerns are independent.

Supported environment identifiers:

- `auto`
- `native-termux`
- `chroot`
- `desktop-linux`
- `android-device`
- `external-device`

`android-device` and `external-device` are represented in the shared model now,
but command execution through those backends is intentionally rejected until
the Android instrumentation phase implements them.

## CLI

```bash
devtool --execution-environment native-termux -r "$HOME/Code/demo" validate
devtool --execution-environment chroot -r "$HOME/Code/demo" validate
devtool --chroot androidos -r "$HOME/Code/demo" validate
devtool --host -r "$HOME/Code/demo" validate
```

`--host` means the environment where the current devtool process is running. On
Termux that is `native-termux`; on ordinary Linux it is `desktop-linux`.
`--chroot NAME` remains a compatibility shortcut for selecting `chroot` and
setting its name. Chroot delegation is accepted only on native Termux. On
desktop Linux it fails preflight even when `chrootctl` is installed; when
Devtool is already inside the chroot, recursive delegation is suppressed.

## Configuration

```toml
[execution]
environment = "auto"
chroot_name = "androidos"
device_serial = ""
```

A named target may override the shared settings:

```toml
[targets.demo]
solution = "Demo.sln"
execution_environment = "chroot"
chroot_name = "androidos"
```

Precedence is:

1. Explicit CLI environment
2. Selected target override
3. `[execution]`
4. Current environment when set to `auto`

## Chroot recursion guard

Every delegated chroot command receives:

```text
DEVTOOL_EXECUTION_CONTEXT=chroot
DEVTOOL_INSIDE_TERMUX_CHROOT=1
DEVTOOL_CHROOT_CONTEXT=<name>
```

A devtool process that sees this context never delegates into the same chroot
again. This applies both to whole-process Termux delegation and to host-UI /
chroot-toolchain execution.

## Shared path mapping

Termux/chroot path translation is implemented once in
`devtool.execution_environments` and reused by the legacy Termux frontend.
Default mappings are created only on native Termux and include:

```text
/data/data/com.termux/files/home -> /home/termux
/sdcard                         -> /sdcard
/storage                        -> /storage
/mnt                            -> /mnt
/tmp                            -> /tmp
```

Override mappings with:

```bash
export DEVTOOL_TERMUX_CHROOT_PATH_MAP="$HOME=/home/mike,/sdcard=/sdcard"
```

`devtool doctor` and `devtool status` report the current, requested, and
effective environment. Before execution, devtool checks repository visibility,
chrootctl availability, path mapping, and whether the selected backend has an
implementation.

## Android auto selection

Android/Gradle commands can run natively in Termux or through the configured chroot. This automatic policy is never evaluated on a PC. Auto mode uses the existing host-UI/chroot-toolchain backend when it is available, preserving Termux-side progress and notification state. Set `prefer_native_termux = true` in the target's Android settings or pass `--execution-environment native-termux` to force native execution. The sparse user global config may extend this policy with `[termux_chroot] project_kinds`, allowing additional project kinds such as Python to use the same Android/Termux chroot backend while leaving repo `.devtool.toml` and explicit CLI flags in control.

## Toolchain discovery and bootstrap policy

Chroot Android/Gradle runs default to discovery-only bootstrap behavior:

```toml
[bootstrap]
downloads = "never" # never | missing | auto | force
network = "deny"    # deny | allow
fail_on_missing_tools = true

[chroots.androidos.android]
sdk_root = "/opt/android-sdk"
gradle = "/opt/gradle/gradle-9.6.1/bin/gradle"
java_home = "/usr/lib/jvm/java-21-openjdk-amd64"
gradle_user_home = "/root/.gradle"
```

Unknown location is not treated as permission to provision. With downloads set
to `never`, Devtool reuses explicit paths, chroot-visible environment values,
project-local metadata, PATH tools, and already cached wrapper distributions.
If a required Android SDK, Gradle, JDK, AAPT2, SDK package, or ARM build-tool
repair is unavailable, the run fails with the checked locations and the exact
config or CLI override to use. Pass `--no-bootstrap-downloads`,
`--deny-network`, `--offline`, `--toolchain-profile NAME`,
`--android-sdk-root`, `--gradle-command`, `--java-home`, or
`--gradle-user-home` for one-run overrides. Non-chroot hosts keep the existing
automatic bootstrap behavior unless `[bootstrap]` or CLI flags change it.


## Path isolation

Before guest execution, Devtool translates shared paths and rejects any remaining native Termux path in command arguments, working directories, or environment values. Host-only toolchain variables are removed when they point into `/data/data/com.termux`. A versioned Termux Python executable is resolved through guest `python3` rather than assuming the same minor version exists in Ubuntu.
