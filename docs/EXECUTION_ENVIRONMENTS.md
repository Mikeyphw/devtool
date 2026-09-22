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

For raw/unprofiled command-mode SSH, `--ssh-cwd` (or `[ssh].cwd`) remains the
explicit remote counterpart of the local repository root. Named workers in
`managed`/`ephemeral` workspace mode do not require a pre-existing checkout or
`ssh.cwd`: Devtool synchronizes the authoritative local checkout and resolves the
remote workspace automatically. Devtool-owned helper files referenced by a phase
(for example the staged Android builder/Gradle init script) are copied to the
remote staging directory over SCP. Generated reports and distributable outputs
from named workers are returned to the phone before local report parsing and
artifact verification.

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

Raw/unprofiled command mode assumes the remote checkout already contains the
source state that should be built/tested. Named managed workers instead mirror
the live phone checkout automatically. For overlay application against an
independent unmanaged remote checkout, prefer whole-run `devtool` mode so the
copied overlay is applied by the remote devtool itself.

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

Named build workers can be declared without replacing the existing `[ssh]`
configuration. SSHW-06 also treats workers as reusable user-level resources:
`devtool ssh add` writes them to `~/.config/devtool/config.toml` by default, so
the same tablet can be selected from every repository. Repository-local worker
profiles remain available with `--repo-local` and override matching global
profile fields. A raw legacy `--ssh HOST` does not inherit a global default
profile unless `--ssh-profile` is explicitly supplied.

```sh
# One-time setup from the phone. Termux sshd commonly listens on 8022.
devtool ssh add tablet --host 192.168.1.50 --port 8022 --user u0_a123
devtool ssh profiles

# Optional repository-only definition/override instead of the global profile.
devtool -r "$HOME/Code/xdm" ssh add tablet --repo-local \
  --host 192.168.1.50 --port 8022 --user u0_a123
```

The equivalent TOML is:

```toml
[ssh]
profile = "tablet" # optional default for `devtool ssh ...` management commands

[ssh.hosts.tablet]
host = "192.168.1.50"
port = 8022
user = "u0_a123"
mode = "commands"

[ssh.hosts.tablet.bootstrap]
policy = "missing" # missing | never

[ssh.hosts.tablet.worker]
tmux = true

[ssh.hosts.tablet.workspace]
mode = "managed" # existing | managed | ephemeral
root = "~/.cache/devtool/workspaces"

[ssh.hosts.tablet.sync]
mode = "incremental"
delete = true
default_excludes = true
exclude = [".idea/"] # optional rsync patterns
```

`--ssh-profile tablet` selects the profile. Explicit transport flags still win,
so `--ssh-profile tablet --ssh-port 2222` is a one-run override rather than a
second configuration mechanism. Worker discovery, bootstrap, tmux inspection,
workspace mapping, and repository synchronization are available directly:

```sh
devtool ssh doctor tablet
devtool ssh bootstrap tablet --dry-run
devtool ssh bootstrap tablet
devtool ssh sessions tablet
devtool -r "$HOME/Code/xdm" ssh workspace tablet
devtool -r "$HOME/Code/xdm" ssh sync tablet --dry-run
devtool -r "$HOME/Code/xdm" ssh sync tablet
devtool -r "$HOME/Code/xdm" ssh retrieve tablet --run-id <run-id>
```

`doctor` intentionally needs only SSH and a POSIX shell on the remote side. It
reports Termux/Linux, architecture, CPU, memory, free storage, package manager,
worker/runtime tools, and the configured workspace/sync policy. Managed workers
require `tmux`; bootstrap installs only missing `python`, `tmux`, `rsync`, and
`git` packages. On Termux it uses `pkg`; supported Linux package managers
require root or passwordless sudo.

### Final worker qualification

`devtool ssh qualify` is the final worker-side seal. On a managed worker it
checks/bootstraps the minimum runtime, synchronizes the authoritative phone
checkout, starts a detached tmux probe and returns from that SSH connection,
reconnects through a separate SSH request to observe completion, rsyncs a
run-scoped evidence marker back to `.devtool/ssh-qualification/`, then plans a
second source synchronization. The second plan must be clean; a non-zero delta
means the phone source changed during qualification or the sync filters are not
stable. Timing and transfer counters are reported so the first-vs-incremental
path is observable.

```sh
devtool -r "$HOME/Code/xdm" ssh qualify tablet --dry-run
devtool -r "$HOME/Code/xdm" ssh qualify tablet

# Then qualify the real project/toolchain through the ordinary managed-build path.
devtool -r "$HOME/Code/xdm" --target xdm_android --ssh-profile tablet \
  validate --task assembleDebug

# Re-run the same command to exercise persistent Gradle/SDK caches and the
# near-zero source delta expected from the incremental path.
```

Use `--no-sync` when only transport/tmux/evidence-return qualification is
wanted. `--dry-run` is non-mutating. The qualification marker is intentionally
outside the synchronized source set, so it cannot create a false delta on the
second pass. For the full roadmap seal, add `--project-validation`. Devtool then invokes the
repository's real managed `validate` path through the selected worker, requires
new SSH return evidence, verifies the remote build did not create a phone->tablet
source delta, and (by default) runs the same validation a second time so the warm
cache/incremental timing is recorded. Use `--target` and repeatable `--task` to
select the real project gate; `--single-validation` disables the second pass.

```sh
devtool -r "$HOME/Code/xdm" ssh qualify tablet \
  --project-validation \
  --target xdm_android \
  --task assembleDebug
```

The infrastructure-only form without `--project-validation` remains available
for fast worker/transport qualification.

### SSH workspace modes and synchronization

`existing` preserves the legacy model: `ssh.cwd` points at a checkout that the
user manages on the remote host. `devtool ssh sync` is deliberately
non-destructive by default in this mode (`delete = false` unless explicitly
configured), and Devtool does not add ownership metadata to that checkout.

`managed` makes the local repository authoritative. Devtool derives a stable
workspace identity from the local checkout, resolves the remote worker home,
and maps it below the configured workspace root. The first real sync creates a
Devtool ownership marker; subsequent syncs verify that marker plus the canonical
remote root before allowing rsync deletion. A pre-existing non-empty directory
without that marker is never claimed.

`ephemeral` uses the same guarded root but creates a per-operation instance
below `.ephemeral/`. The stable repository ID and the operation instance ID are
both recorded so later execution/resume layers can identify the source truth.

Synchronization uses rsync archive semantics, so modified/staged/untracked
files, `.git` state, symlinks, executable bits, and timestamps are mirrored from
the phone. It does **not** use `.gitignore` as a transfer policy: ignored files
can still be legitimate build inputs. Instead Devtool has explicit sync
exclusions. With `default_excludes = true`, `.gradle/`, `build/`, Devtool build
artifacts, SSH staging directories, `.devtool/ssh-return/` evidence bundles, and
`.devtool/ssh-qualification/` qualification evidence are excluded and therefore survive `--delete-delay` on the worker. Global
Gradle/SDK/JDK/NDK/npm caches live
outside the managed repository and are not synchronized at all.

`devtool ssh sync ... --dry-run` is strictly non-mutating. For an existing
workspace it asks remote rsync for the exact delta. For a not-yet-created
managed workspace it compares the local tree to an empty local directory using
the same rsync filters, so even planning does not create remote directories or
markers.

### Transparent managed-worker execution

For a named worker in `managed` or `ephemeral` workspace mode, normal command-mode
execution now owns the preparation lifecycle automatically. A command such as:

```sh
devtool -r "$HOME/Code/xdm" --target xdm_android --ssh-profile tablet validate --task assembleDebug
```

first checks/bootstraps the minimum worker runtime according to
`bootstrap.policy`, synchronizes the live local checkout exactly once, resolves
the verified canonical remote workspace, and then runs every external phase from
that same workspace in tablet-owned tmux sessions. Manual `devtool ssh sync` is
still useful for inspection/prewarming but is no longer required before normal
managed builds. Raw/unprofiled SSH keeps the legacy explicit `ssh.cwd` contract.

Android builds continue to use Devtool's staged Android builder on the remote
Termux host. That builder owns task-aware JDK, Android SDK/build-tools/NDK, Node,
and Gradle provisioning, so the remote worker reuses the same provider and
bootstrap policies as local Android execution without copying phone toolchain
paths. Plain JVM Gradle builds prefer the synchronized project `gradlew`; Devtool
provisions a missing Termux JDK (Java 17/21 when compatible with the declared
toolchain) and only installs system Gradle when no wrapper exists. A wrapperless
project fails closed if the worker Gradle version does not satisfy the declared
version.

Worker-owned caches stay remote. In particular `$HOME/.gradle`, Android SDK/NDK
state, and `$HOME/.cache/devtool` are not synchronized back to the phone and are
reused by later builds. `devtool ssh doctor` reports whether automatic sync,
tmux-owned phases, target-aware toolchains, and persistent remote caches are
active for the selected profile.

### Returned artifacts, reports, logs, and provenance

Named command-mode workers create one run/session-scoped phone-side return bundle per
remote phase under `.devtool/ssh-return/<run>/<session>/`. Before local report
parsing runs, rsync returns generated test/lint reports and common distributable
outputs such as APK/AAB/JAR/AAR files while leaving SDK, Gradle, and other remote
caches on the worker. The normal local artifact pipeline then verifies and copies
returned distributables into the configured `.devtool/build-artifacts` output,
so existing artifact registry/release logic continues to operate on phone-local
files. Remote command output is already streamed into Devtool's ordinary local
run log.

Every return bundle contains a manifest linking the worker profile/destination,
tmux session, verified remote workspace, and source synchronization ID. RunStore
records that provenance alongside the stable declared TestSpec identity, report
paths, build-artifact records, and local log path. Failure evidence therefore
remains attributable to the exact remote source sync without changing `TestResult.id`.

Evidence transfer is resumable and does not replace the build's exit code. A
partial rsync becomes an `SSH_RETURN_PARTIAL` warning and the manifest/partial
files are retained. Retry the transfer without re-running the build with:

```sh
devtool -r "$HOME/Code/xdm" ssh retrieve tablet --run-id <run-id>
# optionally narrow it further
devtool -r "$HOME/Code/xdm" ssh retrieve tablet --run-id <run-id> --session <tmux-session>
```

Recovery revalidates the named worker and workspace boundary before trusting a
persisted manifest, so a local manifest cannot request an arbitrary remote path.
When a recovered bundle belongs to a persisted test run, Devtool also reconciles
that run's SSH return provenance from partial/deferred to the recovered state
without changing the stable TestSpec/operation identity or any test counts.

### Resilient SSH worker runs and hardening

Named SSH workers persist every command phase below the configured `run_root` and
let the tablet's tmux server own the actual process. If the control connection
drops while tmux is still running, the local wrapper maps SSH transport failure
to the distinct recoverable status `125`; it does not kill the remote build or
release its workspace lease. Inspect and reconnect with:

```sh
devtool ssh runs tablet
devtool ssh resume tablet --run-id <run-id>
# JSON resume is intentionally non-interactive and reports current state only.
devtool ssh resume tablet --run-id <run-id> --format json
devtool ssh cancel tablet --run-id <run-id>
devtool ssh recover tablet
```

A managed/ephemeral workspace uses an atomic lease acquired before rsync. The
lease spans all phases in one local execution plan, blocks concurrent writers,
and is released on definitive completion/failure/cancellation. Transport loss
keeps it while the matching tmux phase may still be active. Stale leases are
reclaimed only when no matching tmux session exists and the configured
`lease_timeout_seconds` has elapsed; explicit stale-run recovery reconciles the
run state and its matching owned lease immediately. Manual `devtool ssh sync`
uses the same lock while transferring but releases it as soon as the prewarm
sync completes.

Before managed execution Devtool checks the worker's reported free storage
against `min_free_mb`. Named profiles default to `StrictHostKeyChecking=accept-new`
while raw/unprofiled SSH keeps the system OpenSSH policy; profiles can choose
`strict` and an optional dedicated `known_hosts` path. The same policy is used
for SSH, SCP, rsync transport, management, and recovery operations.

Environment names that look credential-bearing (tokens, passwords, API/private
keys, signing/keystore credentials, and similar) are never embedded in the SSH
argv. Their values are sent over SSH stdin into a mode-`0600` run-state file,
sourced by the tmux phase, omitted from metadata except for key names, and
removed on definitive completion, timeout/cancellation, resume reconciliation,
or stale recovery. A transport-loss case keeps the file only while the remote
phase may still need it. Local timeout/termination traps kill the owned tmux
phase and release the matching lease rather than leaving a concurrent writer.

Example policy:

```toml
[ssh.hosts.tablet.resilience]
host_key_policy = "accept-new" # accept-new | strict | system
known_hosts = ""                # optional local OpenSSH known_hosts file
run_root = "~/.cache/devtool/ssh-runs"
lease_timeout_seconds = 900
min_free_mb = 1024
```

CLI values override environment variables (`DEVTOOL_SSH_HOST`,
`DEVTOOL_SSH_PROFILE`, `DEVTOOL_SSH_PORT`, `DEVTOOL_SSH_USER`, `DEVTOOL_SSH_IDENTITY`,
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

## PRoot-Distro OCI worker lifecycle (OCI-02)

Named SSH workers can manage a rootless PRoot-Distro OCI runtime before OCI
repository execution is enabled. OCI-02 is lifecycle-only: normal repository
commands still use `ssh-native`, and `ssh-proot-oci` remains fail-closed as an
execution provider until the later roadmap overlay activates command wrapping.

```sh
devtool oci doctor tablet
devtool oci images tablet
devtool oci containers tablet
devtool oci bootstrap tablet
devtool oci install tablet ubuntu:24.04 --name devtool-android --architecture aarch64
devtool oci install tablet ./devtool-android.oci.tar --name devtool-android
devtool oci remove tablet devtool-android
devtool oci remove tablet ubuntu:24.04 --image
```

`oci doctor` discovers the PRoot-Distro/proot executables, versions, host
architecture, free storage, runtime/cache roots, installed containers, cached
OCI image references, cache size, and nested-PROot refusal state. `ssh doctor`
projects the same runtime discovery alongside the existing worker toolchain.
On native Termux, the runtime root follows the Termux prefix. On regular Linux,
discovery follows PRoot-Distro's XDG data/cache roots (`XDG_DATA_HOME` and
`XDG_CACHE_HOME`, with their standard home-directory fallbacks).

`oci bootstrap` is intentionally restricted to a native Termux worker and uses
`pkg install -y proot-distro`, which supplies the `proot` dependency. A regular
Linux SSH worker can still be discovered and managed after PRoot-Distro/proot
have been provisioned outside Devtool. Bootstrap, inventory, install, archive
staging, and remove refuse to invoke PRoot-Distro from an already nested PRoot
environment.

Registry references are installed directly by the remote PRoot-Distro. A local
archive must be expressed as an explicit path (`/`, `./`, `../`, or `~` prefix),
matching PRoot-Distro's distinction between registry references and local
archives. Devtool hashes a local archive before execution, streams it to a
temporary worker file, verifies the transferred SHA-256, and atomically publishes
it under the full digest in the worker cache before `proot-distro install` uses
it. A failed upload blocks its dependent install node instead of leaving it
planned as if it could still run.

OCI lifecycle operations participate in the canonical EXO run. Known steps are
published with stable node IDs and dependency edges before execution, real SSH
subprocesses are process-owned by those nodes, progress remains indeterminate
when Devtool has no measured transfer/install fraction, and failures emit typed
diagnostics. Dry-run nodes are planned then skipped without entering `running`
or publishing process progress. Provider/runtime provenance is attached to the
EXO process rather than maintained as a separate OCI lifecycle state machine.
Diagnostic/evidence text emitted by this lifecycle is redacted before
persistence.

OCI-02 deliberately strips the controller's `PD_DOCKER_AUTH` from every SSH
subprocess environment. Private-registry credential transport is deferred to the
later OCI resilience/security overlay; public registry images and worker-local
authentication remain usable now.


## Rootless OCI image lifecycle on SSH workers

Devtool's OCI roadmap keeps image lifecycle separate from repository command
execution. `ssh-proot-oci` is configuration-visible but remains fail-closed for
ordinary build/test/validate execution until the later execution-provider
activation overlay. OCI-03 adds the canonical **image contract and build gate**.

```sh
# Local, read-only definition inspection; no worker is contacted.
devtool oci definition --format json

# Worker lifecycle. Build uses PRoot-Distro's rootless Dockerfile/OCI builder.
devtool oci doctor PROFILE
devtool oci build PROFILE --dry-run
devtool oci build PROFILE --install-as devtool-android-api36

devtool oci validate-image PROFILE devtool-android-api36 --format json
```

The canonical initial image is `linux/arm64` / `aarch64`. Its contract pins the
Ubuntu Noble base identity/snapshot, JDK 17 and 21 (21 default), Python 3.12,
Node 22.20.0, Android API 36, Build Tools 36.0.0 and NDK 29.0.14206865. Google
ships Linux SDK/NDK host-native binaries primarily for x86_64, so image creation
performs the required ARM64 build-tools replacement and provisions transparent QEMU x86_64 wrappers for the official NDK host toolchain.
The original NDK ELF files are preserved behind those wrappers, and `argv[0]`
is retained for multi-call tools such as clang/clang++. Image validation performs
a real direct-driver Android ARM64 compile+link rather than treating emulator or
binary presence as capability proof.

Gradle is deliberately **not** baked into the image. Repositories keep authority
over their own Gradle Wrapper. The image writes only the user-level ARM64 aapt2
override needed by modern AGP on Linux ARM64.

Image definition staging is content-addressed by SHA-256. Build, install and
capability validation are separate dependency-linked EXO nodes under the
caller's canonical RunId. Remote OCI archives are emitted as EXO artifacts with
producer process/node identity and verified SHA-256 metadata. An installed image
also carries `/usr/local/lib/devtool/oci-build-metadata.json`; validation requires
its definition and build-context digests to match the Devtool resources that
requested the run.

Ordinary repository validation must never provision toolchains. The image's
validator is intentionally read-only: it may discover and execute pinned tools,
but it does not run `apt`, `sdkmanager`, `pip install`, global npm installs, or
other package mutation. Toolchain changes belong to explicit image-build
operations and produce a new image/archive identity.

### OCI-03 validation gate 1

The repository includes `tools/oci03-fresh-worker-smoke.sh` for the external
Termux-worker portion of OCI-03 qualification. It deliberately uses
`oci build --no-cache --install-as ...`, so one canonical build run owns the
content-addressed definition staging, PRoot-Distro image build, installation and
in-image capability validation. The smoke gate then checks the OCI manifest
digest separately from the exported archive SHA-256 and requires the in-image
build metadata to match the exact definition/context digests used by Devtool.

```sh
# Use the configured default SSH worker:
tools/oci03-fresh-worker-smoke.sh

# Or name an explicit fresh Termux worker and container:
tools/oci03-fresh-worker-smoke.sh PROFILE devtool-android-api36
```

Gate 1 also includes the OCI-01/OCI-02 provider/lifecycle regressions and native
SSH execution-contract tests. Passing static/unit tests without this live worker
smoke does not by itself qualify OCI-03's external validation gate.

### OCI-03 Gate 1 canonical EXO execution

`devtool oci gate1 [PROFILE] --container devtool-android-api36` is the authoritative
fresh-worker validation surface for OCI-03. It runs preflight, deterministic
context staging, no-cache image build, digest/archive verification, install,
capability validation, provenance verification, and evidence sealing under one
canonical EXO `RunId` and one sealed dependency graph.

Durable truth is written under the Devtool state directory at
`oci-gate1/<run_id>/execution.json`, with the compact redacted event journal,
checkpoint, terminal history snapshot, and `gate1-evidence.json`. The legacy
`tools/oci03-fresh-worker-smoke.sh` helper is only a compatibility launcher and
must not maintain an independent lifecycle state machine.
