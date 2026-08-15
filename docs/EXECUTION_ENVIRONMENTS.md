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

`ssh` supports two tmux-backed modes. `devtool` delegates the whole invocation
to a remote devtool. `commands` keeps devtool local and sends only planned
external command phases to the remote host, similar to the Termux/chroot
toolchain-runner architecture. `android-device` and `external-device` are
represented in the shared model now, but command execution through those
backends is intentionally rejected until the Android instrumentation phase
implements them.

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

SSH has two explicit modes. Neither mode assumes that the remote filesystem is
a chroot or that it has any Termux-style paths.

### Whole-run `devtool` mode

This is the default and is the fully detachable mode. The local process launches
the configured remote devtool inside `tmux`; repository discovery, overlay
application, validation, and builds then belong to the remote devtool process.
The remote host must have `devtool` and `tmux`.

```bash
devtool --ssh buildbox --ssh-mode devtool --ssh-port 2222 \
  --ssh-user builder --ssh-cwd /srv/projects/xdm \
  --ssh-session xdm-validation validate

# Detach immediately. The whole remote devtool run continues.
devtool --ssh buildbox --ssh-mode devtool --ssh-detached \
  --ssh-cwd /srv/projects/xdm validate
```

An existing local overlay/snapshot archive passed on the command line is copied
to a session-scoped remote staging directory over SCP and the remote invocation
receives that remote path. A path that does not exist locally is treated as an
already-remote path and is forwarded unchanged. This makes commands such as the
following work without requiring shared storage:

```bash
devtool --ssh buildbox --ssh-cwd /srv/projects/xdm \
  apply-overlay ~/Downloads/fix.zip --no-validate
```

### Local-devtool `commands` mode

This mode does **not** require devtool to be installed on the remote host. The
local devtool performs planning/UI/orchestration and wraps each external command
phase with SSH + tmux. The remote host needs only `tmux` and the project
toolchain required by the actual command.

Because an SSH host is an independent filesystem, command mode requires
`--ssh-cwd` (or `[ssh].cwd`) as the explicit remote counterpart of the local
repository root. Only that one user-supplied repository mapping is applied; no
remote HOME, username, Termux, or chroot path is inferred. Devtool-owned helper
files referenced by a phase (for example the staged Android builder/Gradle init
script) are copied to the remote staging directory over SCP. Android/Gradle
build artifacts written to the configured artifact output directory are pulled
back over SCP before the local artifact verifier runs.

```bash
devtool --ssh buildbox --ssh-mode commands \
  --ssh-cwd /srv/projects/xdm validate
```

`--ssh-detached` is intentionally reserved for whole-run `devtool` mode. In
`commands` mode the local devtool must wait for each phase so it can preserve
ordering, validation state, progress parsing, and final result handling. A phase
still runs inside a named remote tmux session; manually detaching leaves that
phase alive, but the local run stops with a distinct status instead of falsely
advancing to the next phase.

Command mode assumes the remote checkout already contains the source state that
should be built/tested. For overlay application against an independent remote
checkout, prefer whole-run `devtool` mode so the copied overlay is applied by the
remote devtool itself.

The local machine must provide `ssh` and `scp`. Port 22 is the default, but any
TCP port from 1 to 65535 can be selected. Automatic artifact transfer can be
disabled with `--ssh-no-artifact-copy`. `--ssh-stage-dir` controls the remote
staging root.

Every whole-run attached invocation prints an `ssh ... tmux attach-session ...`
reattach command before connecting.

Transport defaults can be stored in the local `.devtool.toml`:

```toml
[execution]
environment = "ssh"

[ssh]
host = "buildbox"
port = 2222
user = "builder"
identity = "~/.ssh/id_ed25519"
mode = "devtool" # or "commands"
cwd = "/srv/projects/xdm" # required by commands mode
session = "xdm-validation"
tool = "devtool" # devtool mode only
stage_dir = "~/.cache/devtool/ssh-stage"
copy_artifacts = true
detached = false
```

CLI values override environment variables (`DEVTOOL_SSH_HOST`,
`DEVTOOL_SSH_PORT`, `DEVTOOL_SSH_USER`, `DEVTOOL_SSH_IDENTITY`,
`DEVTOOL_SSH_CWD`, `DEVTOOL_SSH_SESSION`, `DEVTOOL_SSH_TOOL`,
`DEVTOOL_SSH_MODE`, `DEVTOOL_SSH_STAGE_DIR`,
`DEVTOOL_SSH_NO_ARTIFACT_COPY`, and `DEVTOOL_SSH_DETACHED`), which override
file settings.

In whole-run mode the remote devtool receives `DEVTOOL_INSIDE_SSH=1` to suppress
recursive SSH delegation.

## PRoot-Distro command execution

`proot-distro` is a peer execution backend to chroot and SSH commands mode. Devtool remains on the host and executes the already-planned external commands through `proot-distro login`; the guest therefore does not need devtool installed. PRoot-Distro supports explicit users, work directories, repeatable binds, and repeatable guest environment overrides. Devtool uses those primitives directly and keeps host/guest paths separate.

```bash
devtool --proot-distro ubuntu --proot-cwd /workspace/project validate

devtool --proot-distro ubuntu \
  --proot-bind "$HOME/Code/demo:/workspace/demo" \
  --proot-cwd /workspace/demo \
  validate
```

Automatic repository binding maps the actual host checkout to `/workspace/project` by default. Explicit guest bind destinations and configured PRoot work directories must be absolute. Absolute host paths inside ordinary arguments, `--flag=/path`/`-DNAME=/path` arguments, and safe command environment values are translated through the same explicit mapping. Helper files outside mapped roots are copied to a per-session host staging directory bound at `/run/devtool`; owned staging is cleaned after the command plan. Devtool's stdlib-only Python support helper is staged the same way when a Python validation helper is needed, so the guest does not require a devtool installation. Persistent guest package/build caches are isolated under `~/.cache/devtool/proot/<distro>` on the host and bound at `/var/cache/devtool` by default.

PRoot guest environment state is intentionally narrow. Devtool passes selected terminal/locale/session values and explicit `[proot].env` entries, but does not blindly carry host `HOME`, `PATH`, `JAVA_HOME`, `GRADLE_USER_HOME`, Android SDK/NDK homes, Python host environments, or chroot/SSH build state. Devtool-managed execution/backend variables are reserved and cannot be overridden through `[proot].env`, project environment, or command-spec environment. Guest cache variables are recomputed against the PRoot cache bind instead of reusing host cache paths. Tool-provider discovery is scoped to the execution environment: selecting PRoot prevents native-Termux CMake/clang paths from being embedded in the guest command plan, and an absolute command selected from the Termux package prefix is reduced to its command name so the guest resolves its own Linux-compatible provider rather than staging the host Bionic binary or launcher script.

`--isolated`, `--minimal`, and `--shared-tmp` are Termux-only PRoot-Distro options. PRoot-Distro itself can also run on a regular Linux host, so the backend is not restricted to Termux when the executable is available. Devtool deliberately does not use PRoot-Distro `--detach` for validation/build phases because detached sessions discard the normal streams needed for progress and phase control. Use tmux around the whole local devtool invocation when detachment is required.

### Termux/PRoot doctor and provider explanations

The final integration diagnostics are host-only so auto-chroot cannot move the native Termux inspection into another environment:

```bash
devtool --host termux doctor --explain
devtool --host termux doctor --distro ubuntu --explain
devtool --host termux packages
devtool --host termux explain
```

`termux doctor` reports native runtime/provider/cache/storage/resource state, each Termux Android-tool candidate, Android SDK/full-NDK readiness when applicable, local SSH/chrootctl/tmux/PRoot availability, and can probe a named installed PRoot guest. The complete campaign promise matrix is maintained in `TERMUX_PROOT_PROMISE_AUDIT.md`. Guest discovery runs the guest's own shell and reports guest tool paths; it never requires a guest devtool installation. `termux packages` emits recommendations only and never invokes `pkg install`, `pkg upgrade`, `apt install`, or `apt upgrade`; OpenJDK package advice is discovered read-only from installed/available package names with a conservative fallback. Generic `devtool doctor --explain` includes the same provider-selection explanations when running on native Termux.

## Native Termux runtime hardening

Native Termux repositories should live in private app storage such as `$HOME/Code`. Android shared storage (`/sdcard`, `/storage/emulated/...`) is detected before native execution and follows `[termux.storage].shared_storage_policy = "allow|warn|deny"`; `--allow-shared-storage-build` is the one-run override. Verified Android/Gradle output may still be exported afterward with `[termux.artifacts].export_dir` or `--artifact-export`. Devtool caches native-Termux build state under a `host-termux` namespace so Bionic-native state is not mixed with chroot, PRoot, SSH, or conventional glibc-host caches.

`[termux].resource_profile` selects conservative, balanced, or performance phone-aware defaults for Gradle workers/JVM memory, memory guards, and native parallelism; explicit resource configuration wins. These phone-local defaults apply to native Termux/chroot/PRoot work that consumes the device resources, but are not injected into SSH/other independent remote execution plans. Optional wake-lock integration always releases the lock in a `finally` path. Before native execution, devtool inspects ELF architecture and `PT_INTERP` without running the candidate and rejects clearly wrong-architecture or conventional glibc/musl executables on Android/Bionic. Runtime diagnostics still classify common glibc/ABI/DNS/TLS/timeout/404 failures, directly invoked scripts can adapt a missing `/bin` or `/usr/bin` shebang to an equivalent Termux PATH executable, and a non-fatal Gradle quiet-period diagnostic is emitted after a configurable silence window instead of assuming dependency resolution is hung. If `XDG_CACHE_HOME` points at Android shared storage, the automatic Termux cache falls back to private `$HOME/.cache`; only an explicit `DEVTOOL_TERMUX_CACHE_ROOT` overrides that safety default.

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
Default mappings are created only for shared paths that have the same verified meaning on both sides:

```text
/sdcard  -> /sdcard
/storage -> /storage
/mnt     -> /mnt
/tmp     -> /tmp
```

Devtool does not invent a guest home for the Termux home. Configure the verified mapping explicitly with `DEVTOOL_TERMUX_CHROOT_HOME` or the general path map:

```bash
export DEVTOOL_TERMUX_CHROOT_PATH_MAP="$HOME=/home/<chroot-user>,/sdcard=/sdcard"
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
