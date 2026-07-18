# Build optimization across desktop, Termux, and chroot

Devtool applies one resource policy to every first-class project runner. The
policy detects the execution environment, logical CPUs, and available memory,
then chooses a bounded worker count. Desktop Linux can use more parallelism;
native Termux and Android-hosted chroots use lower caps to avoid swapping,
Android process reclamation, and thermal slowdown.

`DEVTOOL_MAX_WORKERS` overrides automatic selection for one run:

```bash
DEVTOOL_MAX_WORKERS=3 devtool -r "$HOME/Code/project" validate
```

The equivalent persistent setting is:

```toml
[build]
max_workers = 3
# cache_root = "/custom/cache/root"
```

Runner-specific `max_workers` values override `[build].max_workers`. User
supplied environment variables always override Devtool defaults.

## First-class optimized runners

| Kind | Detection | Validation optimization |
| --- | --- | --- |
| `dotnet` | `.sln` / `.csproj` | Same-target restore/build/test becomes one `dotnet test`; build servers stay warm. |
| `android` | Android Gradle plugin or manifest | Desktop validation can coalesce tasks; Android-safe validation splits heavy phases and lints only artifact-affected modules. |
| `gradle` | Gradle settings/build/wrapper | JVM build/test/lint share one invocation when tasks can be combined. |
| `python` | `pyproject.toml`, lock or test metadata | Existing environments are reused; manager caches remain persistent. |
| `node` | `package.json` | Incremental, prefer-offline dependency install; package scripts reuse the same package-manager cache. |
| `rust` | `Cargo.toml` | `cargo test --all-targets` covers compile and tests in one process; incremental Cargo state is retained. |
| `go` | `go.mod` / `go.work` | `go test` covers package compilation and tests; `go vet` reuses Go caches. |
| `maven` | `pom.xml` / `mvnw` | One parallel `mvn verify` reactor replaces separate dependency, build, and test launches. |
| `cmake` | `CMakeLists.txt` | Persistent out-of-tree build directory; warm builds skip explicit configure and use bounded parallelism. |
| `meson` | `meson.build` | Persistent Ninja state; warm builds skip setup and reuse the build directory. |
| `make` | `Makefile` / `GNUmakefile` | Detected targets run with bounded `-j`; no artificial restore phase. |
| `command` | Explicit configuration | Custom commands retain ordering but receive the shared cache/worker environment. |

## Environment behavior

### Desktop Linux

The default worker cap is eight, further limited by CPU count and available
memory. Existing compiler servers, Gradle daemons, Cargo targets, Go caches,
Node caches, and CMake/Meson build directories remain reusable.

### Native Termux

The default worker cap is three and drops to two on low-memory devices.
Tool-specific caches are placed below Termux's normal cache directory. The
policy avoids unconditional cleanup after successful builds.

### Android-hosted chroot

The default worker cap is four and is memory bounded. When Devtool wraps a
planned command with `chrootctl`, all runner resource and cache assignments are
exported inside the chroot. Chroot cache paths use the guest home rather than a
Termux host path.

Set the guest identity when it differs from the default:

```bash
export DEVTOOL_TERMUX_CHROOT_USER=mike
# or
export DEVTOOL_TERMUX_CHROOT_HOME=/home/mike
```

### Android-safe validation

When validation runs in native Termux or an Android-hosted chroot, Devtool now
uses a protective profile by default. Build, unit-test, and lint phases run
sequentially with one Gradle worker, a two-CPU affinity cap when `taskset` is
available, no project parallelism, a bounded Gradle heap, no persistent daemon,
reduced CPU/I/O priority, and an emergency
free-memory guard. Normal standalone Android builds keep their existing faster
settings.

The defaults can be tuned per target:

```toml
[validation]
android_safe = true
max_workers = 1
cpu_limit = 2
split_gradle = true
no_daemon = true
low_priority = true
gradle_heap_mb = 1536
gradle_metaspace_mb = 384
gradle_lint_metaspace_mb = 512
memory_guard_mb = 640
parallel = false
affected_module_lint = true
```

For artifact-backed validation, Devtool reads the overlay inventory and maps
changed files to their nearest Gradle modules. It then invokes tasks such as
`:app:lintDebug` and `:persistence:lintDebug` together, allowing Gradle to
deduplicate shared prerequisites while avoiding lint analysis for untouched
modules. Documentation-only overlays skip Android lint. Changes to
`settings.gradle*`, root `build.gradle*`, version catalogs, `gradle.properties`,
`buildSrc`, or `build-logic` conservatively fall back to root `lintDebug`. Direct
`devtool lint` remains a full-project check. Set `affected_module_lint = false`
to retain full-project lint during artifact validation.

Lint always receives at least 512 MiB of bounded metaspace unless a larger
`gradle_lint_metaspace_mb` value is configured. This floor is separate from the
general validation metaspace cap because Android lint loads substantially more
detector and model classes than compile or unit-test phases.

The configured guard is an early-warning and cache-reclaim threshold, not an
automatic abort line. Kotlin and KSP may remain healthy below it while zram and
filesystem cache absorb a temporary burst. Devtool therefore stops sustained
pressure only when low `MemAvailable` is corroborated by exhausted swap,
kernel memory PSI stalls, or a clearly worsening memory trend. A separate lower
hard floor still stops a genuine collapse quickly.

When Devtool can obtain root directly, through Termux `su`, or through
passwordless `sudo`, the Android builder makes one conservative recovery
attempt with `sync; echo 1 > /proc/sys/vm/drop_caches`. This drops clean page
cache only; it does not kill applications, clear app data, change swap, or
repeatedly reclaim caches. The parent Devtool process detects that the Android
builder already owns the guard and does not run a second competing watchdog.

Setting `memory_guard_mb = 0` disables only the emergency guard. Set
`android_safe = false` to restore the standard validation profile. Environment
overrides are available as `DEVTOOL_ANDROID_SAFE_VALIDATION`,
`DEVTOOL_VALIDATION_MAX_WORKERS`, `DEVTOOL_VALIDATION_CPU_LIMIT`,
`DEVTOOL_GRADLE_VALIDATION_HEAP_MB`, and
`DEVTOOL_VALIDATION_MEMORY_GUARD_MB`.

## Tool-specific controls

```toml
[dotnet]
coalesce_validation = true
explicit_restore = false
stop_after_run = false

[node]
manager = "auto" # npm | pnpm | yarn | bun
clean_install = false

[rust]
coalesce_validation = true
lint = false

[go]
coalesce_validation = true
vet = true

[maven]
coalesce_validation = true

[cmake]
build_directory = ".devtool/build/cmake"
ccache = true
# generator = "Ninja"

[meson]
build_directory = ".devtool/build/meson"

[make]
# build_target = "all"
```

## Progress totals

Gradle continues to emit exact structured task-graph totals. Generic build
output now recognizes common `completed/total` forms such as Ninja's
`[37/124]` and Maven reactor `2/7` lines. Those values use the same shared HUD
payload consumed by the terminal, Android notification/app/history, compact
desktop HUD, and desktop pipeline view.
