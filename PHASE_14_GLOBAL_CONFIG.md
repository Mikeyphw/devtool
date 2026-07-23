# Phase 14: Sparse global config and Termux chroot policy

Phase 14 adds a user preference layer without turning the global file into a second project config. The new global file lives at `~/.config/devtool/config.toml` or `DEVTOOL_GLOBAL_CONFIG`. It is merged as defaults before `.devtool.toml`, and explicit CLI flags still win.

## User global config

The global config supports a safe sparse subset:

- `[cli.output]` for copy/theme/stdout preferences.
- `[cli.hud]` for default HUD mode and auto-HUD behavior.
- `[cli.apply]` for apply validation, rollback, notification, checksum, and artifact-script defaults.
- `[cli.environment]` for execution backend preferences.
- `[environment] default_chroot` for preferred chroot identity.
- `[termux_chroot]` for Android/Termux auto-chroot project-kind policy.
- `[android]` and `[dotnet]` for small cross-repo tool preferences.

Repo-scoped sections such as `[project]`, `[targets]`, `[tasks]`, `[artifacts]`, and test profiles remain repository-local and are reported by `devtool config doctor --global`.

## CLI commands

New config commands:

```bash
devtool config path --global
devtool config show --global
devtool config defaults --global --minimal
devtool config init --global --minimal
devtool config edit --global
devtool config doctor --global
```

`config edit` resolves editors in this order: `--editor`, `DEVTOOL_EDITOR`, `VISUAL`, `EDITOR`, `micro`, `nano`, `vim`, `vi`.

## Android/Termux chroot policy

`[termux_chroot] project_kinds` controls which project types auto mode may route through the configured chroot on native Termux. The built-in default stays Android/Gradle-first, but users can opt in other kinds:

```toml
[termux_chroot]
project_kinds = ["android", "gradle", "python"]
default_chroot = "androidos"
```

Use `project_kinds = ["all"]` to allow all project kinds. Explicit `--host`, `--execution-environment native-termux`, `--no-auto-chroot`, and `--chroot NAME` still take precedence.

## Validation

`tests/test_global_config.py` verifies sparse global merge behavior, repo override precedence, CLI preference defaults, global doctor warnings, editor resolution, parser coverage, and the expanded Termux chroot project-kind policy. The completion and manpage tests verify public documentation stays synchronized.
