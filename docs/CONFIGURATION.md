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

[test_profiles.medium]
select = ["..."]
```

Those sections describe a repository. Keep them in `.devtool.toml`.

`devtool config doctor --global` warns when repo-scoped sections or command-scoped validation filters appear in the global file.
