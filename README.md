# repo-devtool

`repo-devtool` is a standalone Unix-oriented helper CLI for working on repositories.

It supports:

- `dotnet` repos
- Python repos
- colored Rich summaries
- restore/build/test parsing
- clipboard prompt with default **yes**
- desktop notifications
- overlay/full snapshot apply
- batch aliases
- config template generation
- dynamic AI prompt generation
- safer archive extraction for overlay and snapshot apply

## Install

```bash
tar -xf repo-devtool.tar
cd repo-devtool
./install.sh
```

## Basic usage

```bash
devtool validate --repo /path/to/repo
devtool wine-validate --repo /path/to/repo
devtool apply-overlay --validate /path/to/artifact.zip --repo /path/to/repo
devtool init-config /path/to/repo
devtool ai-prompt --repo-name my-repo
```

## Config

Generate a starter config in the repo root:

```bash
devtool init-config /path/to/repo
```

That writes `.devtool.toml`.

View the generated template without writing a file:

```bash
devtool config defaults --repo /path/to/repo
```

Use `devtool config show` to print the current repo config and `devtool config resolve` to inspect the fully resolved values.

### Environment overrides

Every value from `.devtool.toml` can be overridden through environment variables.

`[project]` overrides use the `DEVTOOL_PROJECT_` prefix:

```bash
DEVTOOL_PROJECT_KIND=python
DEVTOOL_PROJECT_BUILD_TARGET=pyproject.toml
DEVTOOL_PROJECT_RUN_TARGET=src/app.py
DEVTOOL_PROJECT_PYTHON=python3.12
DEVTOOL_PROJECT_RUN_MODULE=my_app
DEVTOOL_PROJECT_PACKAGE_COMMAND="python3 -m build"
DEVTOOL_PROJECT_TEST_COMMAND="python3 -m pytest -q"
```

`[env]` overrides or injected values use the `DEVTOOL_ENV_` prefix:

```bash
DEVTOOL_ENV_SIMSTOOLKIT_WINE_PATH=wine
DEVTOOL_ENV_SIMSTOOLKIT_WINEPATH_PATH=winepath
```

## Clipboard behavior

By default, at the end of a run, `devtool` asks whether to copy raw command output to the clipboard.

- Enter = yes
- `y` / `Y` = yes
- `n` / `N` = no

Use `--copy` to always copy raw output without prompting.
Use `--copy-summary` to copy the rendered summary, or `--copy-json` to copy JSON.
Use `--no-clipboard` to disable clipboard prompts and copy behavior.

### Output payloads

`--json` is a shortcut for the machine-envelope stdout payload.

Use `--stdout-payload` when you want the command to print something other than the normal human summary:

- `auto` keeps the usual human summary on stderr and prints the selected payload only when needed
- `summary` prints the compact summary
- `json` prints the machine envelope used by machine and Termux workflows
- `raw` prints the raw command output

Do not combine `--json` with `--stdout-payload`; use `--stdout-payload json` when you need explicit control.

`apply-overlay` and `apply-snapshot` accept `--validate` to run validation after apply, `--validate-only` to skip apply and validate the current repo state, `--commit "<message>"` to commit after success, and `--commit-even-on-failure` to commit after apply even if validation fails.

`validate` and `wine-validate` accept `--skip-restore`, `--skip-build`, and `--skip-test` so you can run only the stages you need. The same skip flags also work with `apply-overlay --validate` and `apply-snapshot --validate`.

## Notifications

Use `--notify` to send a desktop notification when done.

Use `--notify-brief` to shorten the notification body.

## AI prompt generation

Generate a fully resolved AI workflow prompt for the current repo:

```bash
devtool ai-prompt --repo /path/to/repo --repo-name my-repo
```

Write markdown to a file instead of printing to the terminal:

```bash
devtool ai-prompt --repo /path/to/repo --repo-name my-repo --output prompt.md
```

Useful flags:

- `--project-type auto|dotnet|python|unknown`
- `--build-target <path>`
- `--run-target <path>`
- `--delivery-mode overlay|snapshot`
- `--artifact-type zip|tar`
- `--overlay-count <n>`
- `--commit-message "..."`
- `--wine`
- `--format markdown|text`
- `--output <file>`

The generated prompt is project-agnostic and includes the TOML to environment override rules:

- `DEVTOOL_PROJECT_KIND`
- `DEVTOOL_PROJECT_BUILD_TARGET`
- `DEVTOOL_PROJECT_RUN_TARGET`
- `DEVTOOL_PROJECT_PYTHON`
- `DEVTOOL_PROJECT_RUN_MODULE`
- `DEVTOOL_PROJECT_PACKAGE_COMMAND`
- `DEVTOOL_PROJECT_TEST_COMMAND`
- `DEVTOOL_PROJECT_RESTORE_COMMAND`
- `DEVTOOL_PROJECT_RUN_COMMAND`
- `DEVTOOL_ENV_<NAME>` for values under `[env]`

## Archive Safety

Overlay and snapshot archives are extracted with path-safety checks.

- Archive entries that try to escape the destination directory are rejected.
- Tar archives with symlinks, hardlinks, or special device entries are rejected.
- Snapshot apply still uses a temporary extraction directory before syncing into the repo.

### Overlay Delete Manifest

If an overlay removes files or directories, include a root-level `.devtool-delete` file in the overlay archive.

- Put one repo-relative path per line in `.devtool-delete`, ignore blank lines and `#` comments, and avoid globbing.
- Treat `.devtool-delete` as overlay-only metadata; snapshot delivery should continue to use full repo state.
