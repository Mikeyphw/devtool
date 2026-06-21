from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import textwrap

from .models import ProjectConfig


@dataclass(slots=True)
class AiPromptOptions:
    repo_name: str
    project_type: str
    build_target: str
    run_target: str
    delivery_mode: str
    artifact_type: str
    overlay_count: int
    commit_message: str
    notifications_enabled: bool
    notify_brief: bool
    clipboard_behavior: str
    prefer_wine_validation: bool
    repo_path: str
    output_format: str


def resolve_ai_prompt_options(config: ProjectConfig, args_repo: str | None, *,
                              repo_name: str,
                              project_type: str,
                              build_target: str,
                              run_target: str,
                              delivery_mode: str,
                              artifact_type: str,
                              overlay_count: int,
                              commit_message: str,
                              notify: bool,
                              notify_brief: bool,
                              copy: bool,
                              no_clipboard: bool,
                              prefer_wine_validation: bool) -> AiPromptOptions:
    resolved_type = project_type if project_type and project_type != "auto" else config.kind
    resolved_build = build_target.strip() or config.build_target or "<build target>"
    resolved_run = run_target.strip() or config.run_target or "<run target if needed>"
    repo_path = str((Path(args_repo).expanduser().resolve() if args_repo else config.repo_root))
    if copy:
        clipboard_behavior = "always copy raw command output without prompting"
    elif no_clipboard:
        clipboard_behavior = "never use the clipboard prompt"
    else:
        clipboard_behavior = "ask whether to copy raw command output, with Enter meaning yes"
    return AiPromptOptions(
        repo_name=repo_name,
        project_type=resolved_type or "unknown",
        build_target=resolved_build,
        run_target=resolved_run,
        delivery_mode=delivery_mode,
        artifact_type=artifact_type,
        overlay_count=max(0, overlay_count),
        commit_message=commit_message or "Clear commit message",
        notifications_enabled=notify,
        notify_brief=notify_brief,
        clipboard_behavior=clipboard_behavior,
        prefer_wine_validation=prefer_wine_validation,
        repo_path=repo_path,
        output_format= "markdown",
    )


def render_ai_prompt_markdown(options: AiPromptOptions) -> str:
    validation_command = 'devtool wine-validate -r "$REPO_CURRENT"' if options.prefer_wine_validation else 'devtool validate -r "$REPO_CURRENT"'
    notification_mode = "enabled (brief summary)" if options.notifications_enabled and options.notify_brief else (
        "enabled" if options.notifications_enabled else "disabled"
    )
    combined_delivery = "yes" if options.overlay_count >= 3 and options.delivery_mode == "overlay" else "no"

    return textwrap.dedent(f"""    # AI workflow prompt for {options.repo_name}

    Repo path: `{options.repo_path}`
    Project type: `{options.project_type}`
    Build target: `{options.build_target}`
    Run target: `{options.run_target}`
    Delivery mode: `{options.delivery_mode}`
    Artifact type: `{options.artifact_type}`
    Successful overlay count before this turn: `{options.overlay_count}`
    Notifications: `{notification_mode}`
    Clipboard behavior: `{options.clipboard_behavior}`
    Prefer Wine validation: `{"yes" if options.prefer_wine_validation else "no"}`
    Next overlay must also include a full snapshot: `{combined_delivery}`

    Use these as my standing preferences for the current repo and follow them unless I explicitly override them.

    ## Source of truth
    - Treat the latest uploaded full snapshot or repo snapshot as the only source of truth.
    - Use the latest patched or current repo state as the base for the next change.
    - Ignore older overlays or snapshots unless they are explicitly needed to reconstruct a fix.
    - Assume `devtool` is already installed and available on PATH.

    ## First-run config rule
    - Before using `devtool` commands for a repo, ensure the repo has a config file.
    - If the repo does not already have `.devtool.toml`, create it first with:
      `devtool init-config "{options.repo_path}"`
    - After that, use `devtool` for validation and repo operations.
    - Treat `.devtool.toml` as repo-local configuration and the first required setup step for a new repo.

    ## Working style
    - Work on the real repo, not conceptual examples.
    - Make real implementations, not scaffolds.
    - Work in small vertical slices.
    - Keep model, service, UI, and tests in sync when relevant.
    - Patch build and test failures directly from pasted command output.
    - Be explicit about limitations or blockers.
    - Do not invent architecture or features that are not present in the repo.

    ## Delivery requirements
    - Default to overlay patch mode unless I explicitly ask for a full snapshot.
    - Every patch handoff must include:
      - Artifact type
      - Exact touched files
      - Delete status
      - Brief concrete summary
      - Validation status
      - Exact apply, build, and test commands
    - State delete status explicitly as:
      - `Delete: none`
      - or `Delete handled in patch`

    ## Validation policy
    - Validation is always requested.
    - Use `devtool` for validation.
    - Prefer:
      - `{validation_command}`
    - Use `--verbose` only when needed for debugging or when I ask.
    - Default behavior should keep Rich summaries and the clipboard prompt from `devtool`.
    - Use `--copy` when you want to bypass the prompt in scripted command blocks.

    ## Artifact apply preferences
    - Assume downloaded artifacts are in `~/Downloads`.
    - Overlay patch apply should unzip directly into the repo with overwrite enabled and remove the downloaded archive afterward.
    - Full snapshot apply should extract into a temp dir, sync into the repo, remove the downloaded archive, then remove the temp dir.
    - Prefer deletion to be handled by the patch, not by asking me to delete files manually.

    ## Overlay cadence rule
    - Track successful overlay deliveries.
    - After 3 successful overlay deliveries, the next overlay delivery must also include a full snapshot in a separate archive.
    - That full snapshot must include all changes up to and including the overlay being delivered in that turn.
    - After delivering that combined overlay and full snapshot turn, reset the overlay counter.

    ## Sandbox cleanup rule
    - When delivering a new overlay or full snapshot, delete all older overlays, patches, and superseded archives from the sandbox so only the latest relevant artifacts remain available.
    - Never keep stale overlay chains around once a newer full snapshot supersedes them.

    ## Configuration override rules
    - All `.devtool.toml` values should be considered overridable by environment variables.
    - Project-level overrides use the `DEVTOOL_PROJECT_*` namespace.
    - Repo environment entries use the `DEVTOOL_ENV_*` namespace.
    - Environment variables take precedence over TOML config.

    ### Environment variable override names
    - `DEVTOOL_PROJECT_KIND`
    - `DEVTOOL_PROJECT_BUILD_TARGET`
    - `DEVTOOL_PROJECT_RUN_TARGET`
    - `DEVTOOL_PROJECT_PYTHON`
    - `DEVTOOL_PROJECT_RUN_MODULE`
    - `DEVTOOL_PROJECT_PACKAGE_COMMAND`
    - `DEVTOOL_PROJECT_TEST_COMMAND`
    - `DEVTOOL_PROJECT_RESTORE_COMMAND`
    - `DEVTOOL_PROJECT_RUN_COMMAND`
    - `DEVTOOL_ENV_<NAME>` for any entry under `[env]`

    ## Use of devtool
    - Use `devtool validate -r "$REPO_CURRENT"` for normal validation.
    - Use `devtool wine-validate -r "$REPO_CURRENT"` when Wine parity is relevant.
    - Use `devtool run -r "$REPO_CURRENT"` when I ask how to run the app.
    - Use `devtool package -r "$REPO_CURRENT"` for packaging when relevant.
    - Use `devtool config resolve -r "$REPO_CURRENT"` when config debugging is needed.
    - Use `devtool ai-prompt -r "$REPO_CURRENT" --repo-name "{options.repo_name}"` when I want this prompt regenerated.

    ## Overlay patch apply pattern

    ```bash
    REPO_CURRENT="{options.repo_path}"
    ARTIFACT_CURRENT="<downloaded artifact file>"
    COMMIT_MESSAGE_CURRENT="{options.commit_message}"

    cd ~/Downloads
    unzip -o "$ARTIFACT_CURRENT" -d "$REPO_CURRENT"
    rm -f "$ARTIFACT_CURRENT"

    cd "$REPO_CURRENT"

    if [ ! -f ".devtool.toml" ]; then
      devtool init-config "$REPO_CURRENT"
    fi

    {validation_command} --copy

    git add -A
    git commit -m "$COMMIT_MESSAGE_CURRENT"
    ```

    ## Full snapshot apply pattern

    ```bash
    REPO_CURRENT="{options.repo_path}"
    ARTIFACT_CURRENT="<downloaded artifact file>"
    COMMIT_MESSAGE_CURRENT="{options.commit_message}"
    TMP_SNAPSHOT_CURRENT="/tmp/devtool_snapshot_apply"

    cd ~/Downloads

    rm -rf "$TMP_SNAPSHOT_CURRENT"
    mkdir -p "$TMP_SNAPSHOT_CURRENT"

    tar -xf "$ARTIFACT_CURRENT" -C "$TMP_SNAPSHOT_CURRENT"

    rsync -a --delete \
      --exclude '.git' \
      --exclude '.gitignore' \
      "$TMP_SNAPSHOT_CURRENT"/ "$REPO_CURRENT"/

    rm -f "$ARTIFACT_CURRENT"
    rm -rf "$TMP_SNAPSHOT_CURRENT"

    cd "$REPO_CURRENT"

    if [ ! -f ".devtool.toml" ]; then
      devtool init-config "$REPO_CURRENT"
    fi

    {validation_command} --copy

    git add -A
    git commit -m "$COMMIT_MESSAGE_CURRENT"
    ```

    ## Response style
    - Keep responses concrete and implementation-focused.
    - Avoid conceptual-only answers.
    - Use the current repo state and my pasted logs as the source of truth.
    - When giving me a patch, always tell me exactly what changed and what to run.
    """)


def render_ai_prompt_text(options: AiPromptOptions) -> str:
    return textwrap.dedent(f"""    AI workflow prompt for {options.repo_name}

    Repo path: {options.repo_path}
    Project type: {options.project_type}
    Build target: {options.build_target}
    Run target: {options.run_target}
    Delivery mode: {options.delivery_mode}
    Artifact type: {options.artifact_type}
    Successful overlay count before this turn: {options.overlay_count}
    Notifications enabled: {"yes" if options.notifications_enabled else "no"}
    Notifications brief mode: {"yes" if options.notify_brief else "no"}
    Clipboard behavior: {options.clipboard_behavior}
    Prefer Wine validation: {"yes" if options.prefer_wine_validation else "no"}

    Use these as my standing preferences for the current repo and follow them unless I explicitly override them.

    - Treat the latest uploaded full snapshot or repo snapshot as the only source of truth.
    - If .devtool.toml does not exist, create it first with:
      devtool init-config "{options.repo_path}"
    - Use devtool for validation and repo operations.
    - All .devtool.toml values are overridable by environment variables.
    - Project overrides use DEVTOOL_PROJECT_*.
    - Runtime env overrides use DEVTOOL_ENV_*.
    - Validation is always required.
    - Default to overlay patch mode unless I explicitly ask for a full snapshot.
    - After 3 successful overlays, the next overlay must also include a full snapshot in a separate archive.
    - Delete older overlays, patches, and superseded archives from the sandbox when a newer delivery replaces them.
    """)


def render_ai_prompt(options: AiPromptOptions, fmt: str) -> str:
    if fmt == "text":
        return render_ai_prompt_text(options)
    return render_ai_prompt_markdown(options)
