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
```

## Automatic project type

`kind = "auto"` inspects the repository passed with `-r`, even when the active `.devtool.toml` lives in a parent workspace. One root-local marker wins over unrelated nested projects. Ambiguous root markers, or multiple nested project kinds with no root owner, are rejected until you choose `[project].kind`, `--project-type`, or a typed `--target`.

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
kind = "python"

[targets.app]
root = "app"

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

Those sections describe a repository. Keep them in `.devtool.toml`.

`devtool config doctor --global` warns when repo-scoped sections or command-scoped validation filters appear in the global file.

## Python projects in Termux chroot policy

`[termux_chroot].project_kinds` may include `"python"` when Python validation should run inside the configured Android/Termux chroot. When Python is routed this way, Devtool plans the venv, restore, editable install, tests, and source audit for the chroot backend before commands are wrapped. This avoids host/chroot venv mismatches.

For Python projects that declare `build-system.requires`, Devtool installs those build requirements into the project venv and uses `pip install --no-build-isolation -e .` for editable installs. This keeps build dependencies in the same chroot-local environment that later runs validation.



## Python projects in Android/Termux chroot

When `[termux_chroot].project_kinds` includes `python`, Devtool plans Python validation for the selected chroot before building restore/test specs. The venv bootstrap phase is always emitted for chroot plans because host-side filesystem checks cannot prove that the guest venv exists. This keeps `venv`, `restore:test-tools`, `restore:build-system`, `restore:project`, tests, and source audit in one execution environment.

For Python 3.14 Termux hosts, Devtool preserves the executable name (`python3.14`) when translating host Python paths into chroot bootstrap commands, so Linuxbrew/Ubuntu Python versions can match the active host interpreter where available.
