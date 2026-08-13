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
- `ssh`

`ssh` is a whole-process transport: the destination devtool process still selects
its native/chroot execution backend normally. `android-device` and `external-device`
are represented in the shared model now,
but command execution through those backends is intentionally rejected until
the Android instrumentation phase implements them.

## CLI

```bash
devtool --execution-environment native-termux -r "$HOME/Code/demo" validate
devtool --execution-environment chroot -r "$HOME/Code/demo" validate
devtool --chroot androidos -r "$HOME/Code/demo" validate
devtool --host -r "$HOME/Code/demo" validate
devtool --ssh buildbox --ssh-port 2222 --ssh-user builder \
  --ssh-cwd /srv/projects/demo validate
```

`--host` means the environment where the current devtool process is running. On
Termux that is `native-termux`; on ordinary Linux it is `desktop-linux`.
`--chroot NAME` remains a compatibility shortcut for selecting `chroot` and
setting its name. Chroot delegation is accepted only on native Termux. On
desktop Linux it fails preflight even when `chrootctl` is installed; when
Devtool is already inside the chroot, recursive delegation is suppressed.


## SSH + tmux transport

SSH execution delegates the **entire devtool invocation** to the destination and
runs it in `tmux`. The local process is only the SSH/tmux launcher. The remote
devtool process owns repository discovery, builds, tests, validation, and overlay
application. Detaching with `Ctrl-b` then `d` therefore leaves the remote work
running.

SSH is **not** treated like a chroot. Devtool does not derive remote paths from
Termux paths, does not assume a remote home directory, and does not apply the
Termux/chroot path map. Arguments such as `--repo`, overlay paths, SDK roots, or
config paths are forwarded exactly as supplied and are interpreted by the remote
host.

The remote host must provide `tmux` and the configured remote `devtool` command.
The local machine must provide `ssh`. Port 22 is the default, but any TCP port
from 1 to 65535 can be selected.

```bash
# Run in an explicit remote working directory and attach immediately.
devtool --ssh buildbox --ssh-port 2222 --ssh-user builder \
  --ssh-cwd /srv/projects/xdm \
  --ssh-session xdm-validation validate

# Start/reuse the tmux session without attaching.
devtool --ssh buildbox --ssh-detached \
  --ssh-cwd /srv/projects/xdm validate

# No --ssh-cwd: use the remote SSH login's normal working directory.
devtool --ssh buildbox validate

# A remote path can also be passed directly to devtool. Quote ~ locally so the
# local shell does not expand it before SSH sees it.
devtool --ssh buildbox --repo '~/src/xdm' validate
```

Every attached run prints an `ssh ... tmux attach-session ...` reattach command
before connecting. If the session already exists, another launch attaches to
that session rather than starting a duplicate devtool run.

Transport defaults can be stored in the local `.devtool.toml`:

```toml
[execution]
environment = "ssh"

[ssh]
host = "buildbox"
port = 2222
user = "builder"
identity = "~/.ssh/id_ed25519"
cwd = "/srv/projects/xdm" # optional; remote path, never inferred locally
session = "xdm-validation"
tool = "devtool"
detached = false
```

CLI values override environment variables (`DEVTOOL_SSH_HOST`,
`DEVTOOL_SSH_PORT`, `DEVTOOL_SSH_USER`, `DEVTOOL_SSH_IDENTITY`,
`DEVTOOL_SSH_CWD`, `DEVTOOL_SSH_SESSION`, `DEVTOOL_SSH_TOOL`, and
`DEVTOOL_SSH_DETACHED`), which override file settings.

The remote process receives `DEVTOOL_INSIDE_SSH=1` to suppress recursive SSH
delegation. After the SSH hop, the remote devtool process selects whatever
execution backend is appropriate **for that remote machine**.

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
