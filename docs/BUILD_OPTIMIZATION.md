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
| `android` | Android Gradle plugin or manifest | Build, unit tests, and lint share one Gradle invocation; daemon and build cache are reused. |
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
