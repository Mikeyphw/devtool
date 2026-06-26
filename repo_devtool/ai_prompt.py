from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import textwrap

from .models import ProjectConfig

DELETE_MANIFEST_NAME = ".devtool-delete"

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
    target_name: str = ""
    solution_target: str = ""
    restore_target: str = ""
    test_target: str = ""


def resolve_ai_prompt_options(config: ProjectConfig, args_repo: str | None, *,
                              repo_name: str,
                              project_type: str,
                              build_target: str,
                              run_target: str,
                              target_name: str,
                              solution_target: str,
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
    resolved_target_name = target_name.strip() or config.selected_target or config.default_target
    resolved_solution_target = solution_target.strip() or config.build_target or ""
    resolved_restore_target = config.restore_target or resolved_solution_target or resolved_build
    resolved_test_target = config.test_target or resolved_solution_target or resolved_build
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
        target_name=resolved_target_name,
        solution_target=resolved_solution_target,
        restore_target=resolved_restore_target,
        test_target=resolved_test_target,
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
    target_flags = f' --target "{options.target_name}"' if options.target_name else ""
    validation_base = "wine-validate" if options.prefer_wine_validation else "validate"
    validation_command = f'devtool -r "{options.repo_path}"{target_flags} {validation_base} --copy'
    apply_validate_command = f'devtool -r "{options.repo_path}"{target_flags} apply-overlay --validate "<downloaded artifact file>"'
    notification_mode = "enabled (brief summary)" if options.notifications_enabled and options.notify_brief else (
        "enabled" if options.notifications_enabled else "disabled"
    )
    combined_delivery = "yes" if options.overlay_count >= 3 and options.delivery_mode == "overlay" else "no"

    return textwrap.dedent(f"""    # AI workflow prompt for {options.repo_name}

    Target repo: `{options.repo_path}`
    Repo guardrail: This prompt is for the target repo above. Do not infer the devtool source tree is the repo under change unless the supplied path explicitly points there.
    Project type: `{options.project_type}`
    Build target: `{options.build_target}`
    Run target: `{options.run_target}`
    Target name: `{options.target_name or "-"}`
    Solution target: `{options.solution_target or "-"}`
    Restore target: `{options.restore_target or "-"}`
    Test target: `{options.test_target or "-"}`
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
      `devtool -r "{options.repo_path}" init-config`
    - After that, use `devtool` for validation, repo operations, and Git handoff.
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
    - In a monorepo, always preserve the exact `--target`, `--solution`, or `--project` selector in every validate/apply command.
    - For overlay delivery in a monorepo, prefer:
      - `{apply_validate_command}`
    - Use `--verbose` only when needed for debugging or when I ask.
    - Default behavior should keep Rich summaries and the clipboard prompt from `devtool`.
    - Use `--copy` when you want to bypass the prompt in scripted command blocks.

    ## Artifact apply preferences
    - Assume downloaded artifacts are in `~/Downloads`.
    - Overlay patch apply should unzip directly into the target repo with overwrite enabled and remove the downloaded archive afterward.
    - Full snapshot apply should extract into a temp dir, sync into the repo, remove the downloaded archive, then remove the temp dir.
    - Prefer deletion to be handled by the patch, not by asking me to delete files manually.
    - If an overlay removes files or directories, include a root-level `{DELETE_MANIFEST_NAME}` file in the overlay archive.
    - Put one repo-relative path per line in `{DELETE_MANIFEST_NAME}`, ignore blank lines and `#` comments, and avoid globbing.
    - Treat `{DELETE_MANIFEST_NAME}` as overlay-only metadata; snapshot delivery should continue to use full repo state.

    ## Overlay cadence rule
    - Track successful overlay deliveries.
    - After 3 successful overlay deliveries, the next overlay delivery must also include a full snapshot in a separate archive.
    - That full snapshot must include all changes up to and including the overlay being delivered in that turn.
    - After delivering that combined overlay and full snapshot turn, reset the overlay counter.

    ## Sandbox cleanup rule
    - When delivering a new overlay or full snapshot, delete all older overlays, patches, and superseded archives from the sandbox so only the latest relevant artifacts remain available.
    - Never keep stale overlay chains around once a newer full snapshot supersedes them.

    ## Use of devtool
    - Use `devtool -r "{options.repo_path}" validate` for normal validation.
    - Use `devtool -r "{options.repo_path}" wine-validate` when Wine parity is relevant.
    - Use `devtool -r "{options.repo_path}" run` when I ask how to run the app.
    - Use `devtool -r "{options.repo_path}" package` for packaging when relevant.
    - Use `devtool -r "{options.repo_path}" commit "{options.commit_message}"` for the Git handoff instead of raw `git add` / `git commit`.
    - Use `devtool -r "{options.repo_path}" tag <name>` when a release tag is needed.
    - Use `devtool -r "{options.repo_path}" release-notes --from-ref <ref> --to-ref HEAD` when changelog or release notes are needed.
    - Use `devtool -r "{options.repo_path}" config resolve` when config debugging is needed.
    - Use `devtool -r "{options.repo_path}" ai-prompt --repo-name "{options.repo_name}"` when I want this prompt regenerated.

    ## Overlay patch apply pattern

    ```bash
    cd "{options.repo_path}"

    if [ ! -f ".devtool.toml" ]; then
      devtool -r "{options.repo_path}" init-config
    fi

    {apply_validate_command}

    devtool -r "{options.repo_path}" commit "{options.commit_message}"
    ```

    ## Full snapshot apply pattern

    ```bash
    cd ~/Downloads
    rm -rf "/tmp/devtool_snapshot_apply"
    mkdir -p "/tmp/devtool_snapshot_apply"
    tar -xf "<downloaded artifact file>" -C "/tmp/devtool_snapshot_apply"

    rsync -a --delete \
      --exclude '.git' \
      --exclude '.gitignore' \
      "/tmp/devtool_snapshot_apply"/ "{options.repo_path}"/

    rm -f "<downloaded artifact file>"
    rm -rf "/tmp/devtool_snapshot_apply"

    cd "{options.repo_path}"

    if [ ! -f ".devtool.toml" ]; then
      devtool -r "{options.repo_path}" init-config
    fi

    {validation_command}

    devtool -r "{options.repo_path}" commit "{options.commit_message}"
    ```

    ## Response style
    - Keep responses concrete and implementation-focused.
    - Avoid conceptual-only answers.
    - Use the current repo state and my pasted logs as the source of truth.
    - When giving me a patch, always tell me exactly what changed and what to run.
    """)


def render_ai_prompt_text(options: AiPromptOptions) -> str:
    target_flags = f' --target "{options.target_name}"' if options.target_name else ""
    validation_base = "wine-validate" if options.prefer_wine_validation else "validate"
    validation_command = f'devtool -r "{options.repo_path}"{target_flags} {validation_base} --copy'
    apply_validate_command = f'devtool -r "{options.repo_path}"{target_flags} apply-overlay --validate "<downloaded artifact file>"'
    return textwrap.dedent(f"""    AI workflow prompt for {options.repo_name}

    Target repo: {options.repo_path}
    Repo guardrail: This prompt is for the target repo above. Do not infer the devtool source tree is the repo under change unless the supplied path explicitly points there.
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

    - Treat the target repo above as the repo under change, not the devtool source tree.
    - Treat the latest uploaded full snapshot or repo snapshot as the only source of truth.
    - If .devtool.toml does not exist, create it first with:
      devtool -r "{options.repo_path}" init-config
    - Use devtool for validation, repo operations, and Git handoff.
    - Validation is always required.
    - Prefer validation command: {validation_command}
Prefer monorepo overlay command: {apply_validate_command}
    - The normal Git path is `devtool -r "{options.repo_path}" commit "{options.commit_message}"`.
    - Use `devtool -r "{options.repo_path}" tag <name>` and `devtool -r "{options.repo_path}" release-notes --from-ref <ref> --to-ref HEAD` when needed.
    - Use `devtool -r "{options.repo_path}" ai-prompt --repo-name "{options.repo_name}"` when I want this prompt regenerated.
    - Use `devtool -r "{options.repo_path}" config resolve` when config debugging is needed.
    - Default to overlay patch mode unless I explicitly ask for a full snapshot.
    - After 3 successful overlays, the next overlay must also include a full snapshot in a separate archive.
    - Delete older overlays, patches, and superseded archives from the sandbox when a newer delivery replaces them.

    ## Overlay delete manifest
    - If an overlay removes files or directories, include a root-level `{DELETE_MANIFEST_NAME}` file in the overlay archive.
    - Put one repo-relative path per line in `{DELETE_MANIFEST_NAME}`, ignore blank lines and `#` comments, and avoid globbing.
    - Treat `{DELETE_MANIFEST_NAME}` as overlay-only metadata; snapshot delivery should continue to use full repo state.
    """)


def render_ai_prompt(options: AiPromptOptions, fmt: str) -> str:
    if fmt == "text":
        return render_ai_prompt_text(options)
    return render_ai_prompt_markdown(options)
