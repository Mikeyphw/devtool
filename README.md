# devtool

`devtool` is a standalone Unix-oriented helper CLI for working on repositories.

It supports:

- `dotnet` repos
- Python repos
- colored Rich summaries
- restore/build/test parsing
- clipboard prompt with default **yes**
- desktop notifications
- Tasker-compatible live progress JSON under `/sdcard/Tasker/devtool`
- overlay/full snapshot apply
- batch aliases
- config template generation
- dynamic AI prompt generation
- safer archive extraction for overlay and snapshot apply

## Install

```bash
tar -xf devtool.tar
cd devtool
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

`apply-overlay` and `apply-snapshot` accept `--validate` to run validation after apply, `--validate-only` to skip apply and validate the current repo state, `--commit "<message>"` to commit after success.

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

## 0.2.8 Tasker live progress

This build adds a Tasker-friendly live progress channel. Use:

```bash
devtool --tasker validate
```

`--tasker` is a shortcut for:

- `--tasker-progress`
- `--tasker-broadcast`
- `--notify-progress`

By default, devtool writes JSON under:

```text
/sdcard/Tasker/devtool
```

Files:

- `state.json` — current run state, overwritten atomically as progress changes
- `latest_event.json` — latest event payload, overwritten atomically
- `events/*.json` — append-only event history for each run
- `runs/<run_id>.json` — latest state for the run id

Useful flags:

- `--tasker-dir /path` to change the JSON directory
- `--tasker-progress` to write JSON without enabling the shortcut
- `--tasker-broadcast` to send Android broadcasts after event files are written
- `--tasker-action net.mph.devtool.EVENT` to change the broadcast action
- `--notify-progress` to update a stable live Termux notification; this can be used by itself or through `--tasker`
- `--notify-id 7771` to change the notification id

Tasker can react either by watching `latest_event.json` / `state.json`, or by listening for the broadcast action. The broadcast includes extras named `devtool_event`, `devtool_run_id`, `devtool_event_file`, and `devtool_state_file`.

Example event shape:

```json
{
  "schema_version": 1,
  "producer": "devtool",
  "run_id": "...",
  "event_type": "progress",
  "status": "running",
  "command": "validate",
  "repo_name": "sims3tools",
  "phase": {"name": "build", "index": 2, "total": 3},
  "progress": {"percent": 67.0, "completed": 2, "total": 3, "detail": "S3pe.Core"},
  "notification": {"title": "devtool RUN", "body": "sims3tools • validate • build • 67%"}
}
```

Normal human output remains on stdout. Tasker data is side-channel JSON, so wrappers can react without parsing terminal output.

## 0.2.7 dotnet progress bars

Non-verbose `dotnet restore`, `dotnet build`, `dotnet test`, `dotnet publish`, and `dotnet pack` now show a parsed Rich progress display instead of raw command output. The progress view tracks solution/project counts when it can infer them from `.sln`, `.csproj`, `.fsproj`, `.vbproj`, or directory targets, and shows the current phase/project/test summary while still capturing full logs for diagnostics.

Use `--verbose` when you want the original raw dotnet stream.

## 0.2.6 confirmation and quiet-run fixes

This build tightens apply and validation UX:

- non-verbose restore/build/test no longer streams routine command output; it shows the Rich spinner, captures logs, and still parses diagnostics for the final summary.
- if an overlay/snapshot manifest is present, devtool prints the JSON before any destructive confirmation prompt.
- manifest confirmation defaults to yes (`Y/n`).
- `--yes` / `-y` confirms all destructive prompts, including artifact-manifest confirmation, dirty repo continuation, snapshot overwrite confirmation, and `--interactive` apply confirmation.
- `--verbose` is the explicit way to see raw restore/build/test output live.

## 0.2.5 artifact metadata safety

Artifact metadata entries are now consumed by devtool and skipped during extraction. They should never land in the target repository when artifacts are applied through `devtool apply-overlay` or `devtool apply-snapshot`.

Skipped metadata names:

- `.devtool-artifact.json`
- `artifact.devtool.json`
- `devtool-artifact.json`
- `.devtool-delete`

`.devtool-delete` is also now active for overlays: devtool reads it, safely deletes the listed repo-relative paths, and does not copy the marker file into the repo.

### Dirty repo handling recommendation

For generated overlays/snapshots, prefer this policy:

1. Default: refuse or prompt when `git status --porcelain` is non-empty.
2. For non-interactive automation: fail fast unless the caller passes `--yes`/`--force` intentionally.
3. Safer automation option: create a named pre-apply stash/backup commit before applying, then record the applied artifact in `.devtool/artifacts.json`.
4. Avoid auto-popping a stash after apply; leave it for manual review because overlay changes and local edits can conflict silently.

## 0.2.4 workflow upgrades

This build adds the higher-friction workflow pieces around the existing runner:

- `--machine [compact|pretty]` and `--machine-file` for a clean machine envelope, while normal human output stays on stdout.
- live command execution via `Popen`; non-verbose mode keeps the spinner while capturing restore/build/test output silently for logs, summaries, and parsed diagnostics. Use `--verbose` for the raw live stream.
- persistent Termux dotnet proot `/etc/os-release` cache under `$PREFIX/var/devtool/proot/etc`.
- `--chroot NAME` / `--host` execution selection; apply operations stay host-side and validation/run phases can be routed through `chrootctl exec NAME -- ...`.
- artifact manifest validation, `apply-overlay --preview`, `--interactive`, and `.devtool/artifacts.json` history tracking.
- `devtool detect`, `devtool handoff`, `devtool history`, and `devtool rollback --last`.
- validation matrix support with `validate --matrix linux-arm64,linux-x64` and `--matrix-mode target` for named target fan-out.
- `validate --watch` polling watch mode.
- safer clipboard routing through `DEVTOOL_CLIPBOARD_SOCKET`, `DEVTOOL_CLIPBOARD_FIFO`, Termux, X11, Wayland, or `--copy-via`.
- failure classification in JSON summaries (`restore_failure`, `compile_failure`, `test_failure`, `nuget_failure`, `missing_sdk`, `wine_issue`, `arm_mismatch`, etc.).
- `doctor --fix` creates safe state/cache directories and the Termux proot cache when applicable.


### Dotnet validation progress

- `validate` runs restore, build, and test as separate Rich progress phases.
- Non-verbose dotnet output is parsed into a one-line progress bar plus a detail line below it, which keeps narrow Termux terminals readable.
- Restore/build/test details are normalized to repo-relative paths so long absolute Termux paths do not flood the terminal.
- Progress totals expand dynamically when MSBuild reports more project outputs than the initial solution/project estimate.
- During `validate`, implicit dotnet test commands get `--no-build` when the build phase already ran. Standalone `devtool test`, skipped-build validation, and explicit `test_command` values are left unchanged.

### Artifact metadata and diffs

`apply-overlay` and `apply-snapshot` render artifact manifests as parsed Rich sections instead of raw JSON. Use `--diff` to preview a repo-relative unified diff of artifact file changes without applying; combine `--interactive --diff` to review the diff before confirming apply.

## Transactional artifact apply

`apply-overlay` and `apply-snapshot` now create an rsync transaction backup before mutating files.

- Transaction data is stored under `${DEVTOOL_XDG_DIR:-${XDG_DATA_HOME:-~/.local/share}/devtool}/transactions/<repo-key>/`.
- Nothing is written to `.devtool/transactions` inside the repository.
- Only touched paths are backed up and restored. For overlays this includes changed archive members and paths listed in `.devtool-delete`.
- If extraction, delete handling, restore, build, test, or interruption fails after the transaction starts, devtool rolls back the touched files.
- If devtool is force-closed and leaves an active transaction, the next devtool command for that repo detects and rolls it back before doing anything else.
- Successful applies and successful rollbacks remove the backup directory.
- The original overlay/snapshot artifact and sidecar manifest are deleted only after the whole apply/validate/commit flow succeeds with no rollback. If rollback happens, the original artifact is preserved for debugging or retry.
- Rollback is emitted like any other command result: stdout, Rich summary, notifications, clipboard output, Tasker event JSON, and machine JSON are all still respected.

`--commit-even-on-failure` has been removed. Failed applies and failed validation are never committed.
