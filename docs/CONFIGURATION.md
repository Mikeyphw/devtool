# Devtool configuration

Devtool has two configuration layers:

1. A sparse user preference file at `~/.config/devtool/config.toml`.
2. The repository/project config at `.devtool.toml` or the file passed with `--config`.

Merge order is:

```text
built-in defaults
user global config
repo .devtool.toml
CLI flags
```

The global config is a preference layer, not a shadow project file. It should describe how you like Devtool to behave everywhere. It should not describe how a specific repo builds, tests, validates, or packages.

## Global config commands

```bash
devtool config path --global
devtool config show --global
devtool config defaults --global --minimal
devtool config init --global --minimal
devtool config edit --global
devtool config doctor --global
```

`config edit` picks an editor in this order:

```text
--editor
DEVTOOL_EDITOR
VISUAL
EDITOR
[terminal].preferred
micro
nano
vim
vi
```

The default prefers `micro` so editing works nicely in Termux and normal terminals.

## Good global config

Use a sparse file. Omitted keys keep Devtool's built-in defaults.

```toml
[cli.output]
copy = false
copy_via = "auto"
theme = "auto"
stdout_payload = "auto"

[cli.hud]
auto_hud = "auto"
mode = "desktop"

[ui]
profile = "developer"
theme = "auto"
density = "comfortable"
icons = "auto"
motion = "auto"
verbosity = "normal"
show_environment = "when-relevant"
show_commands = "on-error"
show_diagnostics = "expanded-on-error"

[ui.tui]
density = "compact"

[ui.hud]
density = "dense"
max_steps = 6

[ui.desktop]
sidebar = "expanded"

[ui.android]
notification_detail = "normal"

[cli.apply]
validate = true
manifest_check = true
rollback_on_failure = true
apply_notification = true
artifact_script = true
keep_artifact = false
delete_artifact_on_failure = false
ignore_checksum = false

[cli.environment]
execution_environment = "auto"
no_auto_chroot = false

[environment]
default_chroot = "androidos"

[termux_chroot]
project_kinds = ["android", "gradle"]
default_chroot = "androidos"

[terminal]
preferred = "micro"

[dotnet]
stop_after_run = true

[android]
aapt2_provider = "auto"
aapt2_fallback_path = "/sdcard/Download/aapt2-arm64-v8a.bin"

[android.adb]
provider = "auto" # auto | sdk | termux | termux-adb | path | none
device_policy = "exactly-one"
# path = "/data/data/com.termux/files/usr/bin/termux-adb"
# serial = "..."
# preferred = "Pixel 8"
```


## Shared UI preferences and profiles

`[ui]` is valid in both the user-global config and a repository config. Repository values deep-merge over the global UI preference layer. The effective presentation for each frontend resolves as built-in defaults -> selected profile -> surface defaults -> merged `[ui]` -> `[ui.<surface>]` -> `DEVTOOL_UI_*` -> explicit CLI override.

Built-in profiles are `minimal`, `standard`, `developer`, and `debug`. `profile = "auto"` resolves to `standard`. Custom profiles live under `[ui.profiles.<name>]` and may use `extends = "developer"`; invalid values and inheritance cycles fail during config loading.

Common one-run overrides are `--ui-profile`, `--ui-theme`, `--ui-density`, `--ui-icons`, `--ui-motion`, and `--ui-verbosity`. Use `devtool ui-contract --surface desktop --json` to inspect the global contract, and `devtool config resolved --json` to inspect the repository-merged presentation for every surface. See [`UI_PREFERENCES_AND_TOKENS.md`](UI_PREFERENCES_AND_TOKENS.md).

## Automatic runner detection and platform identity

`runner = "auto"` is the canonical MP01 form. It inspects the repository passed with `-r`, even when the active `.devtool.toml` lives in a parent workspace. `kind = "auto"` remains a compatibility alias. One root-local marker wins over unrelated nested projects. Ambiguous root markers, or multiple nested runner kinds with no root owner, are rejected until you choose `[project].runner`, the legacy `[project].kind`, `--project-type`, or a typed `--target`.

`platform` is independent from `runner`: a TV app and phone app may both use the Android runner, while two desktop targets may use different runners. See [`POLYGLOT_TARGETS_AND_WORKFLOWS.md`](POLYGLOT_TARGETS_AND_WORKFLOWS.md).

## Android/Termux chroot project policy

Historically Devtool's Termux auto-chroot behavior was Android/Gradle-first. The global preference file can now choose which project kinds may be routed through the configured chroot when running from native Termux:

```toml
[termux_chroot]
project_kinds = ["android", "gradle", "python"]
default_chroot = "androidos"
```

Use `all` when the chroot should own every supported project kind:

```toml
[termux_chroot]
project_kinds = ["all"]
```

Explicit CLI choices still win. `--host`, `--execution-environment native-termux`, and `--no-auto-chroot` opt out. `--chroot NAME` explicitly opts in.

`DEVTOOL_TERMUX_CHROOT` is an environment override for the global/default
chroot name. Repository/target-specific chroot declarations remain
authoritative over that environment preference, while the environment value
must not be masked by a global `default_chroot` setting.

## Keep these repo-local

Do not put these in global config:

```toml
[project]
runner = "python"
platform = "server"

[targets.app]
runner = "python"
platform = "server"
root = "app"

[jobs.generate-client]
runner = "node"
command = ["npm", "run", "generate"]

[targets.app.workflows]
validate = ["job:generate-client", "runner:python#validate"]

[tasks]
build = ["..."]

[artifacts]
output_directory = "dist"
output_name = "app"
collision = "unique" # unique | replace | fail
verify = true
# Custom runners/layouts may declare concrete files or directories:
# paths = ["dist/custom-package.zip"]
# directories = ["dist/packages"]
# patterns = ["*.zip", "*.tar.gz"]
# recursive = true

[test_profiles.medium]
select = ["..."]
```

Those sections describe a repository. Keep them in `.devtool.toml`. Reusable `[jobs.*]` declarations are repo-scoped as well. `command = ["tool", "arg"]` is structured argv; use `shell_command = "..."` only when shell syntax is intentional.

### Executable workspace workflow DAGs (MP03–MP06)

A workflow declared as a list of strings remains sequential for compatibility:

```toml
[targets.app.workflows]
validate = [
  "job:generate-client",
  "runner:python#validate",
]
```

MP03 also accepts inline step tables when a branch or join is needed. `id` is the stable dependency name and `depends_on` references other step IDs. Omitting `depends_on` preserves sequential semantics; declaring `depends_on = []` explicitly creates a root node.

```toml
[targets.app.workflows]
validate = [
  { id = "generate", ref = "job:generate-client", depends_on = [] },
  { id = "python", ref = "runner:python#validate", depends_on = ["generate"] },
  { id = "web", ref = "runner:node#test", depends_on = ["generate"] },
  { id = "join", ref = "job:package-metadata", depends_on = ["python", "web"] },
]
```

Every referenced dependency must name a step with an explicit `id`; duplicate IDs, unknown dependencies, self-dependencies, and dependency cycles are configuration errors. MP04 dispatches each executable node through its declared runner and preserves that runner's native planning/progress/diagnostic behavior. A job can also choose its own `execution_environment`, `chroot_name`, `device_serial`, and runner options. MP05 expands `target:<name>#<action>` into the referenced target's workflow or primary-runner action inside the same DAG, deduplicates shared target/action prerequisites, and rejects cross-target cycles during config loading.

MP06 makes `[workspace]` the canonical home for a repository's default target and persistent target groups:

```toml
[workspace]
default_target = "api"

[workspace.groups]
mobile = ["android", "tv"]
frontend = ["web", "desktop"]
release = ["api", "@frontend", "@mobile"]
everything = ["*"]
```

Legacy `[project].default_target` remains accepted. A canonical and legacy value may coexist only when they are equal. Group names must not collide with target names. Groups are validated eagerly, including unknown members and nested-group cycles. Use `devtool --group release validate`, repeated `--group`, or `--all-targets`; `--target`, target matrices, and workspace-group selection are mutually exclusive scopes.

### Affected ownership and graph preflight (MP07–MP08)

Target roots are automatic Git-change ownership boundaries. Add `inputs` for extra source/config paths that affect a target and `outputs` for generated/write paths:

```toml
[targets.api]
runner = "python"
root = "services/api"
inputs = ["../../packages/protocol/**", "../../schemas/**"]
outputs = ["build/**", "dist/**"]
```

Reusable-job `inputs` also make every referencing target an owner of those paths. `devtool validate --affected` computes direct ownership plus reverse `target:` dependency closure; unowned paths conservatively select the candidate workspace. `--affected` may be combined with `--group`/`--all-targets` as a candidate filter and supports `--affected-base`/`--affected-head` for Git ranges.

`devtool preflight` and the workflow section of `devtool doctor` expand selected workflows and aggregate their runners, concrete command executables, effective execution environments, roots, and output safety. Job `execution_environment = "auto"` inherits the target execution destination. See `AFFECTED_TARGETS.md` and `WORKFLOW_PREFLIGHT.md`.

`devtool config doctor --global` warns when repo-scoped sections or command-scoped validation filters appear in the global file.

## Python projects in Termux chroot policy

`[termux_chroot].project_kinds` may include `"python"` when Python validation should run inside the configured Android/Termux chroot. When Python is routed this way, Devtool plans the venv, restore, editable install, tests, and source audit for the chroot backend before commands are wrapped. This avoids host/chroot venv mismatches.

For Python projects that declare `build-system.requires`, Devtool installs those build requirements into the project venv and uses `pip install --no-build-isolation -e .` for editable installs. This keeps build dependencies in the same chroot-local environment that later runs validation.



## Python projects in Android/Termux chroot

When `[termux_chroot].project_kinds` includes `python`, Devtool plans Python validation for the selected chroot before building restore/test specs. The venv bootstrap phase is always emitted for chroot plans because host-side filesystem checks cannot prove that the guest venv exists. This keeps `venv`, `restore:test-tools`, `restore:build-system`, `restore:project`, tests, and source audit in one execution environment.

For Python 3.14 Termux hosts, Devtool preserves the executable name (`python3.14`) when translating host Python paths into chroot bootstrap commands, so Linuxbrew/Ubuntu Python versions can match the active host interpreter where available.


## Safe workflow scheduler (MP09)

Workflow execution is sequential unless the workspace policy or CLI requests safe parallelism. `inputs` are read scopes; `outputs` are write scopes. Nodes with unknown write sets remain serialized and cross-process reservations remain repository-exclusive.

```toml
[workspace.scheduler]
parallel = false
max_parallel_nodes = 4
max_gradle_nodes = 1
max_dotnet_nodes = 1
```

Use `devtool plan ACTION --explain` to inspect exactly why nodes may or may not overlap. See `WORKFLOW_SCHEDULING_AND_PLANNING.md`.

## Inspecting and migrating configuration

Use `devtool config resolve` for the effective configuration and `devtool config resolve --provenance` or `devtool config explain` to understand where target, runner, platform, execution environment, workspace groups and UI settings came from.

Legacy repositories using `[project].kind`, `[targets.*].type/kind`, `[project].default_target`, or legacy `[ui]` confirmation booleans continue to work. FS01 extends the safe migration assistant across all of those config shims:

```sh
devtool compatibility --active-only
devtool config migrate --check
devtool config migrate --check --json
devtool config migrate --write
devtool config migrate --global --check
devtool config migrate --global --write
```

`--write` rewrites safe runner aliases, adds an explicit migration-time `platform` when missing, moves the default target to `[workspace]`, canonicalizes UI confirmation policy, and writes a `.bak` file before modifying configuration. Migration reads raw TOML first, so contradictory legacy/canonical values can be reported even when normal config loading would fail. Existing canonical values are never silently overwritten when reconciliation is ambiguous.

Use `--runner` for one-run runner overrides. `--project-type` remains a compatibility alias. Likewise, `DEVTOOL_PROJECT_RUNNER` is canonical while `DEVTOOL_PROJECT_KIND` remains accepted. See [`BACKWARD_COMPATIBILITY.md`](BACKWARD_COMPATIBILITY.md).

## Completion metadata

`devtool completion-state --kind targets|groups|ui-profiles|test-profiles|variants|jobs|workflows|pytest-tests|all` exposes resolved values for shell/editor integration. This command uses Devtool's real config model, so target groups and inherited profiles match command execution semantics.

## Android Operations signing and instrumentation (AOPS03)

Per-target Android configuration can declare ADB/device policy, instrumentation admission, and release signing:

```toml
[targets.app.android.adb]
provider = "auto"
device_policy = "exactly-one"

[targets.app.android.instrumentation]
policy = "required"
keep_installed = false
capture_logcat_on_failure = true
capture_screenshot_on_failure = true

[targets.app.android.signing.release]
mode = "devtool"
policy = "release-required"
keystore = "~/.config/app/signing/release.p12"
alias = "release"
store_password_env = "APP_RELEASE_STORE_PASSWORD"
key_password_env = "APP_RELEASE_KEY_PASSWORD"
expected_cert_sha256 = "..."
```

Persist only secret source names (or FD/provider configuration), never password values. Project `[env]` is deliberately rejected as storage for signing passwords because validation plans and execution wrappers may serialize project configuration. Devtool-managed signing is task-scoped, so debug/lint/unit tasks do not require release credentials.

