# All-project build optimization

## Coverage

Devtool now has optimized first-class runners for:

- .NET
- Android/Gradle
- JVM Gradle
- Python
- Node.js (`npm`, `pnpm`, Yarn, Bun)
- Rust/Cargo
- Go
- Maven
- CMake
- Meson/Ninja
- Make
- Explicit custom-command projects

## Cross-environment resource policy

- Worker counts are selected from CPU count, available memory, and execution environment.
- Desktop Linux can use up to eight workers by default.
- Native Termux is capped at three workers and drops to two on low-memory devices.
- Android-hosted chroots are capped at four workers and use guest-home cache paths.
- `DEVTOOL_MAX_WORKERS` and `[build].max_workers` provide explicit overrides.
- Chroot wrappers now export runner cache and resource variables inside `chrootctl`.

## Validation/process reductions

- .NET same-target restore/build/test remains one `dotnet test` process.
- Android/JVM Gradle validation remains coalesced into one Gradle launch.
- Cargo validation uses one `cargo test --all-targets` process for compile and tests.
- Go validation uses one `go test` process for compile and tests, then reuses cache for `go vet`.
- Maven validation uses one parallel `mvn verify` reactor.
- CMake and Meson preserve build directories and omit redundant warm setup passes.
- Node installs are incremental and prefer offline package-manager caches.
- Existing Python virtual environments are reused.
- Make targets use bounded parallel jobs without a fake restore phase.

## Progress/UI

- Exact Gradle task totals remain available everywhere.
- Generic progress recognizes common fraction output such as Ninja `[37/124]` and Maven `2/7`.
- Completed/total values continue through the shared payload used by terminal, Android notification/app/history, compact desktop HUD, and desktop pipeline view.

## Validation

- 531 Python tests passed.
- 36 subtests passed.
- All modified Python files compiled successfully.
- First-class runner planning was tested for cold and warm builds and for desktop, native Termux, and chroot resource behavior.
- Native toolchain builds were not executed for every ecosystem in this container; command planning, environment propagation, progress parsing, and existing repository regressions were exercised by automated tests.
