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
setting its name.

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
Default mappings include:

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

Android/Gradle commands can run natively in Termux or through the configured chroot. Auto mode uses the existing host-UI/chroot-toolchain backend when it is available, preserving Termux-side progress and notification state. Set `prefer_native_termux = true` in the target's Android settings or pass `--execution-environment native-termux` to force native execution. The sparse user global config may extend this policy with `[termux_chroot] project_kinds`, allowing additional project kinds such as Python to use the same Android/Termux chroot backend while leaving repo `.devtool.toml` and explicit CLI flags in control.
