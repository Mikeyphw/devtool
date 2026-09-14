from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Iterable

from ..clipboard import copy_text
from ..console_utils import create_console, is_narrow_console
from ..models import ProjectConfig
from .fzf import Choice, TerminalUI
from .rendering import shell_join
from .validation_catalog import ValidationCatalog, build_catalog, dependency_closure, refresh_catalog
from .validation_recipes import ValidationRecipe, delete_recipe, expand_recipe, load_recipes, save_recipe
from .validation_wizard import ValidationDraft


console = create_console()


def run_validation_menu(config: ProjectConfig, args: argparse.Namespace) -> int:
    ui = TerminalUI(console=console, force_fallback=bool(getattr(args, "no_fzf", False)))
    catalog = build_catalog(config, include_runtime_tasks=bool(getattr(args, "runtime_tasks", False)))
    base = ValidationDraft.from_namespace(args)
    base.repo = base.repo or str(config.repo_root)
    base.target = base.target or config.selected_target
    base.variant = base.variant or config.variant
    # The menu itself does not publish HUD progress. Do not turn that internal
    # controller choice into --no-hud on the generated validation command.
    if str(getattr(args, "command", "") or "") == "validation-menu" and not bool(getattr(args, "_devtool_hud_explicit", False)):
        base.auto_hud = None

    requested_recipe = str(getattr(args, "recipe", "") or "").strip()
    if requested_recipe:
        recipe = next((item for item in load_recipes(config.repo_root) if item.name == requested_recipe or item.label == requested_recipe), None)
        if recipe is None:
            console.print(f"[red]Unknown validation recipe: {requested_recipe}[/red]")
            return 2
        matrix_values = str(getattr(args, "matrix_values", "") or "").strip() or ",".join(catalog.targets)
        draft = _draft_from_recipe(
            base,
            recipe,
            ui,
            artifact=str(getattr(args, "artifact", "") or ""),
            matrix_values=matrix_values,
        )
        if draft is None:
            return 2
        if bool(getattr(args, "print_command", False)):
            print(shell_join(draft.full_argv()))
            return 0
        return _review(ui, config, draft)

    if bool(getattr(args, "wizard", False)):
        draft = copy.deepcopy(base)
        if not _guided_wizard(ui, draft, catalog):
            return 0
        if bool(getattr(args, "print_command", False)):
            print(shell_join(draft.full_argv()))
            return 0
        return _review(ui, config, draft)

    if bool(getattr(args, "print_command", False)):
        print(shell_join(base.full_argv()))
        return 0

    if not ui.interactive:
        console.print("[red]The validation menu requires an interactive terminal.[/red]")
        console.print("Use --recipe NAME --print-command for non-interactive command generation.")
        return 2

    include_runtime_tasks = bool(getattr(args, "runtime_tasks", False))
    while True:
        selected = ui.choose(
            _main_choices(catalog),
            prompt="validation",
            header=_main_header(catalog),
            expect=("ctrl-r",),
        )
        if selected is None or selected.key == "quit":
            return 0
        if ui.last_action == "ctrl-r" or selected.key == "refresh":
            try:
                _refresh_for_draft(catalog, base, include_runtime_tasks=include_runtime_tasks)
                config = catalog.config
            except (OSError, SystemExit, ValueError) as exc:
                ui.page(f"# Catalog refresh failed\n\n```text\n{exc}\n```")
            continue
        result = _dispatch_main(ui, config, base, catalog, selected.key)
        if result is not None and result != 0:
            return result
        try:
            _refresh_for_draft(catalog, base, include_runtime_tasks=include_runtime_tasks)
            config = catalog.config
        except (OSError, SystemExit, ValueError) as exc:
            ui.page(f"# Catalog refresh failed\n\n```text\n{exc}\n```")


def _main_header(catalog: ValidationCatalog) -> str:
    config = catalog.config
    state = catalog.state or "none"
    phase_summary = ""
    if catalog.has_session:
        completed = sum(1 for item in catalog.phases if str(item.get("status")) in {"passed", "skipped", "not-applicable"})
        phase_summary = f" · {completed}/{len(catalog.phases)} phases · rev {catalog.revision}"
    environment = config.execution_environment or "auto"
    if config.chroot_name and environment == "chroot":
        environment = f"chroot:{config.chroot_name}"
    if is_narrow_console(console) or console.width <= 72:
        repo = Path(config.repo_root).name or str(config.repo_root)
        target = config.selected_target or "default"
        return (
            f"{repo} · {target} · {config.kind} · {environment} · "
            f"session {state}{phase_summary}"
        )
    return (
        f"repo {config.repo_root} · target {config.selected_target or 'default'} · "
        f"{config.kind} · {environment} · session {state}{phase_summary}"
    )


def _main_choices(catalog: ValidationCatalog) -> list[Choice]:
    choices: list[Choice] = []
    state = catalog.state
    if not catalog.has_session:
        choices.append(Choice("start", "▶ Start a new validation session", "Create a durable resumable session and run its next phase.", _preview_start(catalog)))
    else:
        if state in {"running", "pausing", "stopping"}:
            choices.append(Choice("status", "● View live status", "Show current phase progress and durable control state.", _preview_session(catalog)))
            choices.append(Choice("pause", "Ⅱ Pause after the active phase", "Publish a revision-guarded pause request.", _preview_control(catalog, "pause")))
            choices.append(Choice("stop", "■ Stop the active phase", "Stop now and leave the phase retryable.", _preview_control(catalog, "stop")))
            if state == "pausing":
                choices.append(Choice("cancel-pause", "↻ Cancel pending pause", "Keep the current validation run moving after the active phase.", _preview_control(catalog, "pause --cancel")))
        else:
            choices.append(Choice("next", "▶ Continue with next phase", _next_phase_description(catalog), _preview_run_scope(catalog, "next")))
            choices.append(Choice("through", "◆ Run through a checkpoint", "Choose an ending phase; pending prerequisites are included.", _preview_run_scope(catalog, "through")))
            choices.append(Choice("selected", "◇ Run selected phases", "Dependency-aware multi-selection from the durable plan.", _preview_run_scope(catalog, "selected")))
            choices.append(Choice("complete", "≫ Run every remaining phase", f"Run all {len(catalog.pending_phases)} pending or retryable phases.", _preview_run_scope(catalog, "remaining")))
            if catalog.failed_phases or state in {"blocked", "recovery-required", "rollback-failed"}:
                choices.append(Choice("retry", "↻ Retry failed or interrupted work", "Retry the cached failed phase and all invalidated dependents.", _preview_run_scope(catalog, "retry")))
                recommended = _recommended_retry_profile(catalog)
                if recommended:
                    choices.append(Choice("safe-retry", "♻ Safe retry failed phase", f"Retry immediately with recommended profile {recommended}.", _preview_safe_retry(catalog, recommended)))
            if state in {"recovery-required", "rollback-failed"}:
                choices.append(Choice("recover", "⚕ Recover interrupted attempt", "Reconcile the interrupted phase or resume failed rollback recovery.", _preview_control(catalog, "recover")))
            if state == "recovery-required":
                choices.append(Choice("accept-plan-change", "✓ Accept reviewed plan change", "Record an audit reason and accept incompatible validation-plan drift.", _preview_control(catalog, "accept-plan-change")))
            if state in {"validated", "validated-uncommitted"}:
                choices.append(Choice("finalize", "✓ Finalize validation session", "Seal the completed session and attached transaction.", _preview_control(catalog, "finalize")))

    choices.extend([
        Choice("one-shot", "✓ Run one-shot validation", "Restore, build, and test without a durable phase session.", _preview_method("validate")),
        Choice("all-failures", "☷ Collect all validation failures", "Continue independent checks rather than stopping at the first failure.", _preview_method("validate-all")),
        Choice("watch", "⟳ Watch and revalidate", "Run validation again when repository files change.", _preview_method("watch")),
        Choice("matrix", "⊞ Run validation matrix", "Validate multiple runtime IDs, targets, or variants.", _preview_method("matrix")),
        Choice("artifact", "▣ Validate an overlay or bundle", "Run artifact integrity checks without applying repository changes.", _preview_method("artifact")),
        Choice("apply", "⬢ Configure apply-time validation", "Immediate, sequential, deferred, static, validation-only, or disabled.", _preview_method("apply")),
        Choice("custom", "⚙ Build a custom validation command", "Open the full editable validation command dashboard.", _preview_method("custom")),
        Choice("recipes", "★ Validation recipes", "Run, save, inspect, or remove reusable validation command presets.", _preview_method("recipes")),
    ])
    if catalog.has_session:
        choices.extend([
            Choice("plan", "Plan and phase status", "Inspect the dependency graph, attempts, and phase verdicts.", _preview_plan(catalog)),
            Choice("events", "Durable validation events", "Read recent revisioned validation events.", _preview_events(catalog)),
            Choice("logs", "Live validation logs", "Open the latest Devtool run log with bat when available.", "# Live logs\n\nRuns `devtool logs open latest --pager bat`."),
            Choice("report", "Validation report", "Open the current durable report and final evidence.", _preview_control(catalog, "report")),
            Choice("reset", "Recovery and reset", "Reset one phase and its dependents, or all verdicts.", _preview_control(catalog, "reset")),
            Choice("abort", "Abort or rollback", "Abort the session and choose whether to keep or roll back changes.", _preview_control(catalog, "abort")),
        ])
    choices.append(Choice("refresh", "↻ Refresh status and plan", "Reload repository context, durable revision, phase plan, events, and task catalog."))
    choices.append(Choice("quit", "Exit", "Return to the shell."))
    return choices


def _dispatch_main(
    ui: TerminalUI,
    config: ProjectConfig,
    base: ValidationDraft,
    catalog: ValidationCatalog,
    key: str,
) -> int | None:
    draft = copy.deepcopy(base)
    if key == "refresh":
        return None
    if key == "start":
        draft.method = "sequential-start"
        draft.action = "start"
        draft.restart_session = True
        if not _guided_wizard(ui, draft, catalog, start_at="pipeline"):
            return None
        return _review(ui, config, draft)
    if key in {"next", "complete", "retry", "safe-retry"}:
        draft.method = "sequential-continue"
        draft.action = "retry" if key in {"retry", "safe-retry"} else "continue"
        draft.validation_scope = "remaining" if key == "complete" else "next"
        draft.retry_failed_phase = key in {"retry", "safe-retry"}
        _attach_session(draft, catalog)
        recommendation = _recommended_retry_profile(catalog)
        if key == "safe-retry" and recommendation:
            draft.resource_profile = recommendation
        elif key == "retry" and recommendation:
            use_safe = ui.confirm(f"Use the recommended retry profile {recommendation!r}?", default=True)
            if use_safe:
                draft.resource_profile = recommendation
        return _review(ui, config, draft)
    if key == "through":
        draft.method = "sequential-continue"
        draft.action = "continue"
        _attach_session(draft, catalog)
        if not _configure_phase_scope(ui, draft, catalog, forced="through"):
            return None
        return _review(ui, config, draft)
    if key == "selected":
        draft.method = "sequential-continue"
        draft.action = "continue"
        _attach_session(draft, catalog)
        if not _configure_phase_scope(ui, draft, catalog, forced="selected"):
            return None
        return _review(ui, config, draft)
    if key in {"status", "plan", "events", "report"}:
        return _run_viewer(config, _control_draft(base, catalog, key))
    if key == "logs":
        viewer = copy.deepcopy(base)
        viewer.raw_override = ["logs", "open", "latest", "--pager", "bat"]
        return _run_viewer(config, viewer)
    if key in {"pause", "stop", "recover", "finalize"}:
        return _review(ui, config, _control_draft(base, catalog, key))
    if key == "accept-plan-change":
        control = _control_draft(base, catalog, "accept-plan-change")
        control.reason = ui.ask("Audit reason for accepting the new validation plan")
        if not control.reason.strip():
            return None
        if not ui.confirm("Accept this incompatible plan change with the recorded audit reason?", default=False):
            return None
        return _review(ui, config, control)
    if key == "cancel-pause":
        control = _control_draft(base, catalog, "pause")
        control.cancel_pause = True
        return _review(ui, config, control)
    if key == "reset":
        control = _control_draft(base, catalog, "reset")
        if not _configure_reset(ui, control, catalog):
            return None
        return _review(ui, config, control)
    if key == "abort":
        control = _control_draft(base, catalog, "abort")
        if not _configure_abort(ui, control):
            return None
        return _review(ui, config, control)
    if key == "one-shot":
        draft.method = "validate"
        if not _guided_wizard(ui, draft, catalog, start_at="pipeline"):
            return None
        return _review(ui, config, draft)
    if key == "all-failures":
        draft.method = "validate-all"
        draft.continue_on_error = True
        if not _guided_wizard(ui, draft, catalog, start_at="pipeline"):
            return None
        return _review(ui, config, draft)
    if key == "watch":
        draft.method = "watch"
        if not _configure_watch(ui, draft):
            return None
        if not _guided_wizard(ui, draft, catalog, start_at="pipeline", stop_before="failure"):
            return None
        return _review(ui, config, draft)
    if key == "matrix":
        draft.method = "matrix"
        if not _configure_matrix(ui, draft, catalog):
            return None
        if not _guided_wizard(ui, draft, catalog, start_at="pipeline", stop_before="failure"):
            return None
        return _review(ui, config, draft)
    if key == "artifact":
        if not _configure_artifact(ui, draft):
            return None
        return _review(ui, config, draft)
    if key == "apply":
        draft.method = "apply"
        if not _configure_apply(ui, draft):
            return None
        if draft.apply_mode in {"validate", "sequential", "validate-only"}:
            _configure_pipeline(ui, draft)
            _configure_tests(ui, draft, catalog)
            _configure_resources(ui, draft)
            _configure_guards(ui, draft)
            _configure_failure(ui, draft)
        return _review(ui, config, draft)
    if key == "custom":
        if not _custom_dashboard(ui, draft, catalog):
            return None
        return _review(ui, config, draft)
    if key == "recipes":
        return _recipes_menu(ui, config, base, catalog)
    return None


def _guided_wizard(
    ui: TerminalUI,
    draft: ValidationDraft,
    catalog: ValidationCatalog,
    *,
    start_at: str = "method",
    stop_before: str = "",
) -> bool:
    pages = ["method", "context", "pipeline", "scope", "tests", "resources", "guards", "failure", "output"]
    start_index = pages.index(start_at) if start_at in pages else 0
    index = start_index
    while index < len(pages):
        page = pages[index]
        if stop_before and page == stop_before:
            break
        handled = True
        if page == "method":
            handled = _configure_method(ui, draft, catalog)
        elif page == "context":
            handled = _configure_context(ui, draft, catalog)
        elif page == "pipeline" and draft.method not in {"artifact-overlay", "artifact-bundle", "control"}:
            handled = _configure_pipeline(ui, draft)
        elif page == "scope" and draft.is_sequential and draft.method != "control":
            handled = _configure_phase_scope(ui, draft, catalog)
        elif page == "tests" and draft.method not in {"artifact-overlay", "artifact-bundle", "control"}:
            handled = _configure_tests(ui, draft, catalog)
        elif page == "resources" and draft.method not in {"artifact-overlay", "artifact-bundle", "control"}:
            handled = _configure_resources(ui, draft)
        elif page == "guards" and (draft.is_sequential or draft.method == "apply"):
            handled = _configure_guards(ui, draft)
        elif page == "failure" and (draft.is_sequential or draft.method == "apply"):
            handled = _configure_failure(ui, draft)
        elif page == "output":
            handled = _configure_output(ui, draft)

        if not handled:
            if ui.last_action == "alt-left" and index > start_index:
                index -= 1
                continue
            return False
        index += 1
    return True


def _custom_dashboard(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    while True:
        choices = [
            Choice("method", "Validation method", draft.method_label, _preview_draft(draft)),
            Choice("context", "Repository and execution context", _context_description(draft), _preview_draft(draft)),
            Choice("pipeline", "Pipeline coverage", _pipeline_description(draft), _preview_draft(draft)),
            Choice("scope", "Sequential phase scope", _scope_description(draft), _preview_draft(draft), disabled=not draft.is_sequential),
            Choice("tests", "Test selection", _tests_description(draft), _preview_draft(draft)),
            Choice("resources", "Resource policy", _resource_description(draft), _preview_draft(draft)),
            Choice("guards", "Runtime and device guards", _guard_description(draft), _preview_draft(draft), disabled=not (draft.is_sequential or draft.method == "apply")),
            Choice("failure", "Failure and retry policy", draft.failure_action, _preview_draft(draft), disabled=not (draft.is_sequential or draft.method == "apply")),
            Choice("output", "Output, HUD, and execution", _output_description(draft), _preview_draft(draft)),
            Choice("review", "Review command", "Validate the combination and continue to execution.", _preview_draft(draft)),
            Choice("cancel", "Cancel", "Discard this draft."),
        ]
        selected = ui.choose(choices, prompt="custom", header="Configure any section, then choose Review command")
        if selected is None or selected.key == "cancel":
            return False
        if selected.key == "review":
            return True
        handlers = {
            "method": lambda: _configure_method(ui, draft, catalog),
            "context": lambda: _configure_context(ui, draft, catalog),
            "pipeline": lambda: _configure_pipeline(ui, draft),
            "scope": lambda: _configure_phase_scope(ui, draft, catalog),
            "tests": lambda: _configure_tests(ui, draft, catalog),
            "resources": lambda: _configure_resources(ui, draft),
            "guards": lambda: _configure_guards(ui, draft),
            "failure": lambda: _configure_failure(ui, draft),
            "output": lambda: _configure_output(ui, draft),
        }
        handler = handlers.get(selected.key)
        if handler:
            handler()


def _configure_method(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    choices = [
        Choice("validate", "Smart one-shot", "Normal restore, build, and test validation."),
        Choice("validate-all", "Collect all failures", "Continue independent checks and aggregate failures."),
        Choice("sequential-start", "Sequential session", "Create a durable checkpointed validation session."),
        Choice("sequential-continue", "Resume session", "Continue the active durable validation session.", disabled=not catalog.has_session),
        Choice("watch", "Watch mode", "Revalidate after filesystem changes."),
        Choice("matrix", "Matrix", "Validate multiple runtime IDs, targets, or variants."),
        Choice("wine", "Wine validation", "Use Wine-specific runner settings."),
        Choice("artifact-overlay", "Overlay integrity", "Validate one overlay archive without applying it."),
        Choice("artifact-bundle", "Bundle integrity", "Validate an overlay bundle or multiple overlays."),
        Choice("apply", "Apply-time validation", "Configure validation as part of an artifact apply."),
    ]
    selected = ui.choose(choices, prompt="method", header="Choose the validation strategy")
    if selected is None:
        return False
    draft.method = selected.key
    if draft.method == "sequential-start":
        draft.action = "start"
        draft.restart_session = True
    elif draft.method == "sequential-continue":
        draft.action = "continue"
        _attach_session(draft, catalog)
    elif draft.method == "watch":
        _configure_watch(ui, draft)
    elif draft.method == "matrix":
        _configure_matrix(ui, draft, catalog)
    elif draft.method in {"artifact-overlay", "artifact-bundle"}:
        _configure_artifact(ui, draft, forced=draft.method)
    elif draft.method == "apply":
        _configure_apply(ui, draft)
    return True


def _configure_context(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    while True:
        selected = ui.choose([
            Choice("target", "Choose target", draft.target or "default", disabled=not catalog.targets),
            Choice("variant", "Choose variant", draft.variant or "default", disabled=not catalog.variants),
            Choice("host", "Force host", "Run validation phases in the current host environment."),
            Choice("auto", "Automatic environment", "Use project and global execution policy."),
            Choice("chroot", "Named chroot", draft.chroot or catalog.config.chroot_name or "androidos"),
            Choice("native-termux", "Native Termux", "Do not route phases into a Linux chroot."),
            Choice("desktop-linux", "Desktop Linux", "Force the desktop Linux execution backend."),
            Choice("android-device", "Android device", "Use the Android device execution backend."),
            Choice("external-device", "External device", "Use a configured external-device backend."),
            Choice("overrides", "Path and project overrides", "Set solution, project, config, or project type."),
            Choice("done", "Done", _context_description(draft), _context_preview(draft, catalog)),
        ], prompt="context", header="Configure repository and execution context")
        if selected is None:
            return False
        key = selected.key
        if key == "done":
            return True
        if key == "target":
            value = ui.choose([Choice(item, item, catalog.config.targets[item].kind or "target") for item in catalog.targets], prompt="target")
            if value:
                draft.target = value.key
                draft.variant = ""
        elif key == "variant":
            value = ui.choose([Choice(item, item) for item in catalog.variants], prompt="variant")
            if value:
                draft.variant = value.key
        elif key == "host":
            draft.host, draft.chroot, draft.execution_environment = True, "", "auto"
        elif key == "auto":
            draft.host, draft.chroot, draft.execution_environment = False, "", "auto"
        elif key == "chroot":
            draft.host = False
            draft.execution_environment = "auto"
            draft.chroot = ui.ask("Chroot name", default=draft.chroot or catalog.config.chroot_name or "androidos")
        elif key in {"native-termux", "desktop-linux", "android-device", "external-device"}:
            draft.host, draft.chroot, draft.execution_environment = False, "", key
        elif key == "overrides":
            draft.config = ui.ask("Config path", default=draft.config)
            draft.solution = ui.ask("Solution override", default=draft.solution)
            draft.project = ui.ask("Project override", default=draft.project)
            draft.project_type = ui.ask("Project type", default=draft.project_type)
        try:
            _refresh_for_draft(catalog, draft)
        except (OSError, SystemExit, ValueError) as exc:
            ui.page(f"# Context refresh failed\n\n```text\n{exc}\n```")

def _configure_pipeline(ui: TerminalUI, draft: ValidationDraft) -> bool:
    selected = ui.choose([
        Choice("all", "Restore + build + test", "Run the complete configured validation pipeline."),
        Choice("build-test", "Build + test", "Skip dependency restore."),
        Choice("test", "Test only", "Skip restore and build."),
        Choice("restore-build", "Restore + build", "Skip tests."),
        Choice("restore", "Restore only", "Skip build and tests."),
        Choice("build", "Build only", "Skip restore and tests."),
        Choice("custom", "Custom stages", "Toggle restore, build, and test independently."),
    ], prompt="pipeline", header="Choose pipeline coverage")
    if selected is None:
        return False
    mapping = {
        "all": (False, False, False),
        "build-test": (True, False, False),
        "test": (True, True, False),
        "restore-build": (False, False, True),
        "restore": (False, True, True),
        "build": (True, False, True),
    }
    if selected.key in mapping:
        draft.skip_restore, draft.skip_build, draft.skip_test = mapping[selected.key]
    elif selected.key == "custom":
        chosen = ui.choose([
            Choice("restore", "Restore", "Resolve project dependencies."),
            Choice("build", "Build", "Compile the selected target."),
            Choice("test", "Test", "Execute the configured test stage."),
        ], prompt="stages", header="Select stages to run", multi=True)
        if chosen is None:
            return False
        enabled = set(chosen.keys)
        draft.skip_restore = "restore" not in enabled
        draft.skip_build = "build" not in enabled
        draft.skip_test = "test" not in enabled
    return True


def _configure_phase_scope(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog, *, forced: str = "") -> bool:
    phase_items = catalog.phases
    if not phase_items:
        detail = catalog.plan_error or "The selected runner produced no resumable validation phases."
        ui.page(f"# Validation plan unavailable\n\n```text\n{detail}\n```")
        return False
    choices = [
        Choice("next", "Next pending phase", "Default continuation behavior."),
        Choice("remaining", "Every remaining phase", "Continue until the plan is complete or blocked."),
        Choice("through", "Through a selected phase", "Run pending prerequisites and stop after the checkpoint."),
        Choice("selected", "Selected phases and prerequisites", "Multi-select phases; Devtool includes still-pending dependencies."),
        Choice("exact", "One exact phase only", "Allowed only when its prerequisites are already satisfied."),
        Choice("advanced", "Advanced boundaries", "Start phase, maximum phases, runtime limit, and pause-after checkpoint."),
        Choice("refresh", "Refresh status and plan", "Reload the selected repository context and resolved runner plan."),
    ]
    mode = forced
    if not mode:
        selected = ui.choose(choices, prompt="scope", header="Choose the durable phase scope · Ctrl-R reloads status and plan", expect=("ctrl-r",))
        if ui.last_action == "ctrl-r":
            _refresh_for_draft(catalog, draft)
            return _configure_phase_scope(ui, draft, catalog, forced=forced)
        if selected is None:
            return False
        mode = selected.key
        if mode == "refresh":
            _refresh_for_draft(catalog, draft)
            return _configure_phase_scope(ui, draft, catalog)
    _clear_phase_scope(draft)
    if mode == "next":
        draft.validation_scope = "next"
    elif mode == "remaining":
        draft.validation_scope = "remaining"
    elif mode == "through":
        selected = ui.choose(_phase_choices(phase_items, dependency_preview=True, catalog=catalog), prompt="through", header="Stop after which phase?")
        if selected is None:
            return False
        closure, already = dependency_closure(phase_items, [selected.key])
        ui.page(_phase_scope_preview("Through phase", [selected.key], closure, already, phase_items))
        if not ui.confirm("Use this checkpoint and dependency closure?", default=True):
            return False
        draft.validation_scope = "through"
        draft.through = selected.key
    elif mode == "selected":
        selected = ui.choose(_phase_choices(phase_items, dependency_preview=True, catalog=catalog), prompt="phases", header="Select one or more phases", multi=True)
        if selected is None:
            return False
        selected_names = list(selected.keys)
        closure, already = dependency_closure(phase_items, selected_names)
        ui.page(_phase_scope_preview("Selected phase closure", selected_names, closure, already, phase_items))
        if not ui.confirm("Use this combined dependency closure?", default=True):
            return False
        draft.validation_scope = "selected"
        draft.phases = selected_names
    elif mode == "exact":
        exact_choices = []
        for item in phase_items:
            ready = _phase_prerequisites_satisfied(item, phase_items)
            exact_choices.append(Choice(
                str(item.get("name")),
                str(item.get("label") or item.get("name")),
                "Prerequisites satisfied" if ready else _phase_block_reason(item, phase_items),
                _phase_preview(item, phase_items, catalog=catalog),
            ))
        selected = ui.choose(exact_choices, prompt="exact-phase", header="Run exactly one pending phase")
        if selected is None:
            return False
        item = next((value for value in phase_items if str(value.get("name")) == selected.key), {})
        if not _phase_prerequisites_satisfied(item, phase_items):
            ui.page(f"# Exact phase unavailable\n\n{_phase_block_reason(item, phase_items)}")
            return _configure_phase_scope(ui, draft, catalog, forced="exact")
        draft.phase_only = selected.key
    elif mode == "advanced":
        selected = ui.choose([
            Choice("from", "Start from phase", draft.from_phase or "not set"),
            Choice("max-phases", "Maximum phase count", str(draft.max_phases or "unlimited")),
            Choice("runtime", "Runtime budget", draft.max_runtime or "unlimited"),
            Choice("pause-after", "Pause after phase", draft.pause_after or "not set"),
            Choice("require", "Require complete", "enabled" if draft.require_complete else "disabled"),
            Choice("done", "Done", "Return to the wizard."),
        ], prompt="boundaries", header="Configure one advanced boundary at a time")
        if selected is None:
            return False
        if selected.key == "from":
            value = ui.choose(_phase_choices(phase_items), prompt="from")
            if value:
                draft.from_phase = value.key
        elif selected.key == "max-phases":
            draft.max_phases = _ask_int(ui, "Maximum phases (0 = unlimited)", draft.max_phases, minimum=0)
        elif selected.key == "runtime":
            draft.max_runtime = ui.ask("Runtime budget, for example 15m or 1h", default=draft.max_runtime)
        elif selected.key == "pause-after":
            value = ui.choose(_phase_choices(phase_items), prompt="pause-after")
            if value:
                draft.pause_after = value.key
        elif selected.key == "require":
            draft.require_complete = not draft.require_complete
        if selected.key != "done":
            return _configure_phase_scope(ui, draft, catalog, forced="advanced")
    return True


def _configure_pytest(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    """Configure pytest-native selection without perturbing the legacy test-menu numbering."""
    while True:
        selected = ui.choose([
            Choice("tests", "Exact pytest tests", _list_label(draft.pytest_tests)),
            Choice("refresh", "Refresh pytest collection", catalog.pytest_discovery_source or "collect executable pytest node IDs"),
            Choice("markers", "Marker expressions", _list_label(draft.pytest_markers)),
            Choice("k", "-k expression", draft.pytest_k or "none"),
            Choice("args", "Additional pytest arguments", _list_label(draft.pytest_args)),
            Choice(
                "defaults",
                "Include configured/default pytest targets",
                "enabled" if draft.pytest_include_defaults else "disabled (exact selection only)",
                disabled=not draft.pytest_tests,
            ),
            Choice("done", "Done", "Return to test selection."),
        ], prompt="pytest", header="Configure pytest-native selection")
        if selected is None or selected.key == "done":
            return True
        if selected.key == "tests":
            if not catalog.pytest_tests:
                refreshed = build_catalog(catalog.config, include_runtime_tests=True)
                catalog.pytest_tests = refreshed.pytest_tests
                catalog.pytest_discovery_source = refreshed.pytest_discovery_source
                catalog.pytest_discovery_error = refreshed.pytest_discovery_error
            if catalog.pytest_discovery_error:
                ui.page(
                    "# Pytest collection notice\n\n"
                    f"Source: `{catalog.pytest_discovery_source or 'unknown'}`\n\n"
                    f"```text\n{catalog.pytest_discovery_error}\n```"
                )
            if catalog.pytest_tests:
                value = ui.choose(
                    [Choice(item, item, "pytest executable node ID") for item in catalog.pytest_tests],
                    prompt="pytest tests",
                    multi=True,
                )
                if value:
                    draft.pytest_tests = list(value.keys)
                    draft.pytest_include_defaults = False
            else:
                draft.pytest_tests = _ask_csv(ui, "Pytest node IDs/files", draft.pytest_tests)
                if draft.pytest_tests:
                    draft.pytest_include_defaults = False
        elif selected.key == "refresh":
            refreshed = build_catalog(catalog.config, include_runtime_tests=True)
            catalog.pytest_tests = refreshed.pytest_tests
            catalog.pytest_discovery_source = refreshed.pytest_discovery_source
            catalog.pytest_discovery_error = refreshed.pytest_discovery_error
            if catalog.pytest_discovery_error:
                ui.page(
                    "# Pytest collection notice\n\n"
                    f"Source: `{catalog.pytest_discovery_source or 'unknown'}`\n\n"
                    f"```text\n{catalog.pytest_discovery_error}\n```"
                )
        elif selected.key == "markers":
            draft.pytest_markers = _ask_csv(ui, "Pytest -m expressions", draft.pytest_markers)
        elif selected.key == "k":
            draft.pytest_k = ui.ask("Pytest -k expression", default=draft.pytest_k)
        elif selected.key == "args":
            draft.pytest_args = _ask_csv(ui, "Additional pytest argv values", draft.pytest_args)
        elif selected.key == "defaults":
            draft.pytest_include_defaults = not draft.pytest_include_defaults


def _configure_tests(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    selected = ui.choose([
        Choice("all", "All configured tests", "Clear interactive selectors and use repository defaults."),
        Choice("profile", "Saved test profile", draft.test_profile or "none", disabled=not catalog.test_profiles),
        Choice("tasks", "Native tasks or scripts", _list_label(draft.tasks), disabled=not catalog.tasks),
        Choice("refresh-tasks", "Refresh Gradle task catalog", "Run `gradlew tasks --all -q` and reopen this page.", disabled=catalog.config.kind not in {"android", "gradle"}),
        Choice("selector", "Type, suite, class, or name", _list_label(draft.selectors)),
        Choice("id", "Exact framework IDs", _list_label(draft.test_ids)),
        Choice("tags", "Include and exclude tags", _list_label([*draft.tags, *[f"!{item}" for item in draft.exclude_tags]])),
        Choice("paths", "Source paths", _list_label(draft.paths)),
        Choice("classes", "Classes and methods", _list_label([*draft.classes, *draft.methods])),
        Choice("filter", "Framework-native filter", draft.test_filter or "none"),
        Choice("failed", "Previously failed tests", "enabled" if draft.failed_tests else "disabled"),
        Choice("changed", "Tests related to changes", f"enabled from {draft.changed_from}" if draft.changed_tests else "disabled"),
        Choice("instrumented", "Android instrumentation", "enabled" if draft.instrumented else "disabled"),
        Choice("done", "Done", "Keep the current test selection."),
        # Deliberately placed after Done so the established numbered-fallback
        # positions remain stable for scripted/keyboard users.
        Choice("pytest", "Pytest selection", _list_label(draft.pytest_tests) if draft.pytest_tests else "native exact tests and filters", disabled=catalog.config.kind != "python"),
    ], prompt="tests", header="Choose a test selector to configure")
    if selected is None:
        return False
    key = selected.key
    if key == "all":
        _clear_test_selection(draft)
    elif key == "profile":
        value = ui.choose([Choice(item, item) for item in catalog.test_profiles], prompt="profile")
        if value:
            draft.test_profile = value.key
    elif key == "tasks":
        value = ui.choose([Choice(item, item, "Configured or discovered Gradle task") for item in catalog.tasks], prompt="tasks", multi=True)
        if value:
            draft.tasks = list(value.keys)
    elif key == "refresh-tasks":
        refreshed = build_catalog(catalog.config, include_runtime_tasks=True)
        catalog.tasks = refreshed.tasks
        catalog.task_error = refreshed.task_error
        if catalog.task_error:
            ui.page(f"# Gradle task discovery failed\n\n```text\n{catalog.task_error}\n```")
        return _configure_tests(ui, draft, catalog)
    elif key == "selector":
        draft.selectors = _ask_csv(ui, "Selectors", draft.selectors)
    elif key == "id":
        draft.test_ids = _ask_csv(ui, "Exact test IDs", draft.test_ids)
    elif key == "tags":
        draft.tags = _ask_csv(ui, "Include tags", draft.tags)
        draft.exclude_tags = _ask_csv(ui, "Exclude tags", draft.exclude_tags)
    elif key == "paths":
        draft.paths = _ask_csv(ui, "Source paths", draft.paths)
    elif key == "classes":
        draft.classes = _ask_csv(ui, "Test classes", draft.classes)
        draft.methods = _ask_csv(ui, "Test methods", draft.methods)
    elif key == "filter":
        draft.test_filter = ui.ask("Native test filter", default=draft.test_filter)
    elif key == "failed":
        draft.failed_tests = not draft.failed_tests
    elif key == "changed":
        draft.changed_tests = not draft.changed_tests
        if draft.changed_tests:
            draft.changed_from = ui.ask("Changed-from Git revision", default=draft.changed_from or "HEAD")
    elif key == "instrumented":
        draft.instrumented = not draft.instrumented
    elif key == "pytest":
        _configure_pytest(ui, draft, catalog)
    if key != "done":
        return _configure_tests(ui, draft, catalog)
    return True


def _configure_resources(ui: TerminalUI, draft: ValidationDraft) -> bool:
    while True:
        selected = ui.choose([
            Choice("automatic", "Automatic", "Use repository and environment-aware defaults."),
            Choice("android-low-power", "Android low power", "One worker, two CPUs, no daemon, low priority, no parallelism."),
            Choice("standard", "Standard", "Disable Android-safe constraints and use standard project policy."),
            Choice("custom", "Custom overrides", _resource_description(draft)),
            Choice("retries", "Retry policy", "automatic retries disabled" if draft.no_auto_retry else f"infra {draft.max_infrastructure_retries}, resource {draft.max_resource_retries}"),
            Choice("phase-daemon", "Between-phase Gradle daemon", "stop" if draft.stop_gradle_after_phase else "keep" if draft.keep_gradle_daemon else "configured default"),
            Choice("done", "Done", _resource_description(draft)),
        ], prompt="resources", header="Configure resource policy")
        if selected is None:
            return False
        if selected.key == "done":
            return True
        if selected.key in {"automatic", "android-low-power", "standard"}:
            draft.resource_profile = selected.key
        elif selected.key == "custom":
            draft.max_workers = _ask_optional_int(ui, "Maximum workers (blank = configured)", draft.max_workers, minimum=1)
            draft.cpu_limit = _ask_optional_int(ui, "CPU limit (blank = configured)", draft.cpu_limit, minimum=1)
            draft.gradle_heap_mb = _ask_optional_int(ui, "Gradle heap MiB (blank = configured)", draft.gradle_heap_mb, minimum=128)
            draft.memory_guard_mb = _ask_optional_int(ui, "Memory guard MiB (0 disables, blank = configured)", draft.memory_guard_mb, minimum=0)
            draft.low_priority = _ask_tristate(ui, "Process priority", draft.low_priority, "Low priority", "Normal priority")
            draft.parallel = _ask_tristate(ui, "Gradle parallelism", draft.parallel, "Parallel", "No parallel")
            draft.build_cache = _ask_tristate(ui, "Gradle build cache", draft.build_cache, "Build cache", "No build cache")
            draft.gradle_daemon = _ask_tristate(ui, "Gradle daemon", draft.gradle_daemon, "Daemon", "No daemon")
        elif selected.key == "retries":
            draft.no_auto_retry = ui.confirm("Disable automatic infrastructure and resource retries?", default=draft.no_auto_retry)
            if draft.no_auto_retry:
                draft.max_infrastructure_retries = 0
                draft.max_resource_retries = 0
            else:
                draft.max_infrastructure_retries = _ask_int(ui, "Infrastructure retries", draft.max_infrastructure_retries, minimum=0)
                draft.max_resource_retries = _ask_int(ui, "Resource retries", draft.max_resource_retries, minimum=0)
        elif selected.key == "phase-daemon":
            value = ui.choose([
                Choice("default", "Configured default", "Do not add an invocation override."),
                Choice("stop", "Stop after each phase", "Shut Gradle daemons down at phase boundaries."),
                Choice("keep", "Keep daemon warm", "Leave Gradle daemons available between invocations."),
            ], prompt="phase-daemon")
            if value:
                draft.stop_gradle_after_phase = value.key == "stop"
                draft.keep_gradle_daemon = value.key == "keep"

def _configure_guards(ui: TerminalUI, draft: ValidationDraft) -> bool:
    draft.stop_below_battery = _ask_int(ui, "Minimum battery percent (0 disables)", draft.stop_below_battery, minimum=0, maximum=100)
    draft.require_charging = ui.confirm("Require charging before a new phase?", default=draft.require_charging)
    draft.stop_above_temperature = _ask_float(ui, "Maximum temperature °C (0 disables)", draft.stop_above_temperature, minimum=0)
    draft.stop_below_disk_mb = _ask_int(ui, "Minimum free repository storage MiB (0 disables)", draft.stop_below_disk_mb, minimum=0)
    if draft.is_sequential:
        draft.max_runtime = ui.ask("Invocation runtime budget (blank = unlimited)", default=draft.max_runtime)
        draft.require_complete = ui.confirm("Return failure when validation pauses incomplete?", default=draft.require_complete)
    return True


def _configure_failure(ui: TerminalUI, draft: ValidationDraft) -> bool:
    selected = ui.choose([
        Choice("pause", "Preserve changes and pause", "Recommended. Keep the tree and rollback point for inspection."),
        Choice("rollback", "Roll back immediately", "Roll back an attached apply transaction after validation failure."),
        Choice("keep", "Keep changes and close recovery", "Keep the applied tree after a failed session."),
        Choice("prompt", "Ask interactively", "Prompt when the failure occurs."),
    ], prompt="failure", header="Choose sequential validation failure behavior")
    if selected is None:
        return False
    draft.failure_action = selected.key
    draft.rollback_on_failure = selected.key == "rollback"
    return True


def _configure_output(ui: TerminalUI, draft: ValidationDraft) -> bool:
    while True:
        selected = ui.choose([
            Choice("verbose", "Verbose output", "enabled" if draft.verbose else "disabled"),
            Choice("dry-run", "Dry-run", "enabled" if draft.dry_run else "disabled"),
            Choice("continue", "Continue independent steps", "enabled" if draft.continue_on_error else "disabled"),
            Choice("fail-fast", "Fail fast", "enabled" if draft.fail_fast else "disabled"),
            Choice("performance", "Performance report", draft.performance_file or ("enabled" if draft.performance else "disabled")),
            Choice("copy", "Clipboard summary", "copy" if draft.copy else "disabled" if draft.no_clipboard else "automatic"),
            Choice("hud", "HUD backend", f"{draft.hud_mode}, {'enabled' if draft.auto_hud is not False else 'disabled'}"),
            Choice("json", "Stable validation JSON", draft.machine_format or ("enabled" if draft.json_output else "disabled"), disabled=not (draft.is_sequential or draft.method == "control")),
            Choice("machine", "Machine file / stdout payload", draft.machine_file or draft.stdout_payload),
            Choice("done", "Done", "Keep output choices."),
        ], prompt="output", header="Toggle one output or execution option")
        if selected is None:
            return False
        key = selected.key
        if key == "done":
            return True
        if key == "verbose":
            draft.verbose = not draft.verbose
        elif key == "dry-run":
            draft.dry_run = not draft.dry_run
        elif key == "continue":
            draft.continue_on_error = not draft.continue_on_error
            if draft.continue_on_error:
                draft.fail_fast = False
        elif key == "fail-fast":
            draft.fail_fast = not draft.fail_fast
            if draft.fail_fast:
                draft.continue_on_error = False
        elif key == "performance":
            draft.performance = not draft.performance
            if draft.performance:
                draft.performance_file = ui.ask("Performance report path (blank = default)", default=draft.performance_file)
        elif key == "copy":
            value = ui.choose([
                Choice("auto", "Automatic", "Use normal Devtool clipboard behavior."),
                Choice("copy", "Always copy", "Add --copy."),
                Choice("off", "Disable clipboard", "Add --no-clipboard."),
            ], prompt="clipboard")
            if value:
                draft.copy = value.key == "copy"
                draft.no_clipboard = value.key == "off"
        elif key == "hud":
            value = ui.choose([
                Choice("auto", "Automatic HUD", "Publish progress and use the selected backend."),
                Choice("off", "Disable HUD", "Do not publish live HUD state."),
                Choice("desktop", "Desktop", "Use the desktop backend."),
                Choice("desktop-window", "Desktop window", "Use the GTK4 desktop HUD window."),
                Choice("android-bridge", "Android bridge", "Publish to the Termux/Android app bridge."),
                Choice("terminal", "Terminal", "Use terminal HUD rendering."),
            ], prompt="hud")
            if value:
                if value.key == "off":
                    draft.auto_hud = False
                else:
                    draft.auto_hud = True
                    if value.key != "auto":
                        draft.hud_mode = value.key
                    if value.key == "android-bridge":
                        draft.hud_overlay = ui.confirm("Also request the Android floating overlay?", default=bool(draft.hud_overlay))
        elif key == "json":
            value = ui.choose([
                Choice("off", "Disabled", "Use normal human output."),
                Choice("pretty", "Pretty JSON", "Emit an indented stable validation-control envelope."),
                Choice("compact", "Compact JSON", "Emit a compact stable validation-control envelope."),
            ], prompt="json-output")
            if value:
                draft.json_output = value.key != "off"
                draft.machine_format = value.key if value.key in {"pretty", "compact"} else ""
        elif key == "machine":
            draft.stdout_payload = ui.ask("Stdout payload: auto, summary, or raw", default=draft.stdout_payload)
            draft.machine_file = ui.ask("Machine envelope file (blank = none)", default=draft.machine_file)


def _configure_watch(ui: TerminalUI, draft: ValidationDraft) -> bool:
    draft.watch_interval = _ask_float(ui, "Watch poll interval seconds", draft.watch_interval, minimum=0.1)
    return True


def _configure_matrix(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    mode = ui.choose([
        Choice("rid", "Runtime IDs", "Examples: linux-x64, win-x64, android-arm64."),
        Choice("target", "Named targets", "Validate selected .devtool.toml targets."),
        Choice("variant", "Variants", "Validate selected variants of the target."),
    ], prompt="matrix-mode", header="Choose matrix dimension")
    if mode is None:
        return False
    draft.matrix_mode = mode.key
    source = catalog.targets if mode.key == "target" else catalog.variants if mode.key == "variant" else []
    if source:
        selected = ui.choose([Choice(item, item) for item in source], prompt="matrix", multi=True)
        if selected:
            draft.matrix_items = list(selected.keys)
    else:
        draft.matrix_items = _ask_csv(ui, "Matrix items", draft.matrix_items)
    return bool(draft.matrix_items)


def _configure_artifact(ui: TerminalUI, draft: ValidationDraft, *, forced: str = "") -> bool:
    mode = forced
    if not mode:
        selected = ui.choose([
            Choice("artifact-overlay", "Single overlay", "Validate one overlay archive."),
            Choice("artifact-bundle", "Bundle or multiple overlays", "Validate a bundle and optional write conflicts."),
        ], prompt="artifact-kind")
        if selected is None:
            return False
        mode = selected.key
    draft.method = mode
    recent = _recent_artifacts(Path(draft.repo or "."))
    if mode == "artifact-overlay":
        selected = ui.choose([
            *[Choice(str(path), str(path), "Recently modified artifact") for path in recent],
            Choice("__manual__", "Enter path manually", draft.artifact or "No current path"),
        ], prompt="artifact-path", header="Choose an overlay archive") if recent else None
        if selected and selected.key != "__manual__":
            draft.artifact = selected.key
        else:
            draft.artifact = ui.ask("Overlay archive path", default=draft.artifact)
        draft.artifacts = []
    else:
        if recent:
            selected = ui.choose([Choice(str(path), str(path), "Recently modified artifact") for path in recent], prompt="artifacts", header="Select artifact paths or Back for manual entry", multi=True)
            if selected:
                draft.artifacts = list(selected.keys)
            else:
                draft.artifacts = _ask_csv(ui, "Bundle or overlay archive paths", draft.artifacts or ([draft.artifact] if draft.artifact else []))
        else:
            draft.artifacts = _ask_csv(ui, "Bundle or overlay archive paths", draft.artifacts or ([draft.artifact] if draft.artifact else []))
        draft.artifact = ""
        draft.fail_on_conflict = ui.confirm("Fail when nested overlays write the same file?", default=draft.fail_on_conflict)
    draft.ignore_checksum = ui.confirm("Ignore SHA-256 checksum validation?", default=draft.ignore_checksum)
    return bool(draft.artifact or draft.artifacts)


def _configure_apply(ui: TerminalUI, draft: ValidationDraft) -> bool:
    paths = _ask_csv(ui, "Overlay archive paths", draft.artifacts or ([draft.artifact] if draft.artifact else []))
    if not paths:
        return False
    draft.artifacts = paths
    draft.artifact = paths[0] if len(paths) == 1 else ""
    selected = ui.choose([
        Choice("validate", "Validate immediately", "Apply, then run ordinary validation."),
        Choice("sequential", "Sequential validation", "Apply and validate through a durable session."),
        Choice("deferred", "Deferred validation", "Apply now and create a resumable session without running a phase."),
        Choice("static", "Static validators only", "Run artifact/static validators without Gradle validation phases."),
        Choice("validate-only", "Validation only", "Skip apply and run validation against the current tree."),
        Choice("no-validate", "Disable validation", "Suppress manifest-requested validation."),
    ], prompt="apply-validation", header="Choose apply-time validation behavior")
    if selected is None:
        return False
    draft.apply_mode = selected.key
    draft.no_artifact_script = ui.confirm("Skip embedded artifact validation scripts?", default=draft.no_artifact_script)
    return True


def _configure_reset(ui: TerminalUI, draft: ValidationDraft, catalog: ValidationCatalog) -> bool:
    selected = ui.choose([
        Choice("phase", "Reset one phase and dependents", "Retain attempt evidence."),
        Choice("from", "Reset from a phase", "Reset the selected phase and all later/dependent work."),
        Choice("all", "Reset all phase verdicts", "Retain all attempt evidence and start the plan again."),
    ], prompt="reset")
    if selected is None:
        return False
    if selected.key == "all":
        draft.reset_all = True
        return True
    phase = ui.choose(_phase_choices(catalog.phases), prompt="reset-phase")
    if phase is None:
        return False
    if selected.key == "from":
        draft.from_phase = phase.key
        draft.phases = []
    else:
        draft.phases = [phase.key]
        draft.from_phase = ""
    return True


def _configure_abort(ui: TerminalUI, draft: ValidationDraft) -> bool:
    selected = ui.choose([
        Choice("keep", "Keep applied changes", "Abort the session without rolling back the attached transaction."),
        Choice("rollback", "Roll back changes", "Abort and restore the transaction backup."),
    ], prompt="abort", header="Choose abort behavior")
    if selected is None:
        return False
    draft.keep_changes = selected.key == "keep"
    draft.rollback = selected.key == "rollback"
    return ui.confirm(f"Confirm validation abort with {selected.label.lower()}?", default=False)


def _review(ui: TerminalUI, config: ProjectConfig, draft: ValidationDraft) -> int:
    while True:
        errors = draft.validate()
        first_error = errors[0] if errors else ""
        review = draft.review_markdown(extra_sections=[("What Will Change", _change_summary(draft))])
        choices = [
            Choice("run", "Run", f"Blocked: {first_error}" if first_error else "Execute the generated command.", review, disabled=bool(errors)),
            Choice("dry", "Dry-run", f"Blocked: {first_error}" if first_error else "Execute with the global --dry-run flag.", review, disabled=bool(errors)),
            Choice("copy", "Copy command", "Copy the exact shell command to the clipboard.", review),
            Choice("edit", "Edit command arguments", "Open the command portion in $VISUAL/$EDITOR.", review),
            Choice("save", "Save as recipe", "Write a global or repository validation recipe.", review),
            Choice("view", "Open full review", "Render the command, warnings, and blocking problems with bat.", review),
            Choice("logs", "Open latest log", "Open the latest Devtool log with bat when available.", review),
            Choice("back", "Back", "Return without executing."),
            Choice("copy-json", "Copy argv JSON", "Copy the generated argv vector as JSON.", review),
        ]
        selected = ui.choose(
            choices,
            prompt="review",
            header="Enter run · Ctrl-D dry-run · Ctrl-Y copy · Ctrl-E edit · Ctrl-S save",
            expect=TerminalUI.REVIEW_EXPECT,
        )
        if selected is None or selected.key == "back":
            return 0
        action = {
            "ctrl-d": "dry",
            "ctrl-y": "copy",
            "ctrl-e": "edit",
            "ctrl-s": "save",
            "ctrl-l": "logs",
        }.get(selected.action, selected.key)
        if action == "view":
            ui.page(review)
            continue
        if action == "logs":
            log_view = copy.deepcopy(draft)
            log_view.raw_override = ["logs", "open", "latest", "--pager", "bat"]
            _run_viewer(config, log_view)
            continue
        if action == "copy-json":
            payload = json.dumps(draft.full_argv(), indent=2)
            result = copy_text(payload)
            if result.success:
                console.print(f"[green]Argv JSON copied via {result.backend}.[/green]")
            else:
                console.print(f"[yellow]Could not copy argv JSON: {result.reason}[/yellow]")
            continue
        if action == "copy":
            command = shell_join(draft.full_argv())
            result = copy_text(command)
            if result.success:
                console.print(f"[green]Command copied via {result.backend}.[/green]")
            else:
                console.print(f"[yellow]Could not copy command: {result.reason}[/yellow]")
            continue
        if action == "edit":
            edited = ui.edit_text(shell_join(draft.command_argv()) + "\n", suffix=".sh").strip()
            if edited:
                try:
                    draft.raw_override = shlex.split(edited)
                except ValueError as exc:
                    console.print(f"[red]Could not parse edited command: {exc}[/red]")
            continue
        if action == "save":
            _save_recipe_dialog(ui, config, draft)
            continue
        if action in {"run", "dry"}:
            if errors:
                ui.page(draft.review_markdown())
                continue
            execution = copy.deepcopy(draft)
            if action == "dry":
                execution.dry_run = True
            return _execute(config, execution)


def _execute(config: ProjectConfig, draft: ValidationDraft) -> int:
    argv = [sys.executable, "-m", "devtool", *draft.global_argv(), *draft.command_argv()]
    console.print(f"[dim]$ {shell_join(['devtool', *draft.global_argv(), *draft.command_argv()])}[/dim]")
    completed = subprocess.run(argv, cwd=config.repo_root, check=False)
    return int(completed.returncode)


def _run_viewer(config: ProjectConfig, draft: ValidationDraft) -> int:
    return _execute(config, draft)


def _recipes_menu(ui: TerminalUI, config: ProjectConfig, base: ValidationDraft, catalog: ValidationCatalog) -> int:
    query = ""
    while True:
        recipes = load_recipes(config.repo_root)
        visible = _filter_recipes(recipes, query)
        choices = [
            Choice(item.name, _recipe_label(item), f"{item.scope}: {item.description}", _recipe_preview(item))
            for item in visible
        ]
        choices.extend([
            Choice("__filter__", "Filter recipes", query or "No filter"),
            Choice("__duplicate__", "Duplicate a recipe", "Copy a built-in, global, or repository recipe under a new name."),
            Choice("__rename__", "Rename a custom recipe", "Move a global or repository recipe to a new name."),
            Choice("__delete__", "Delete a custom recipe", "Built-in recipes cannot be removed."),
            Choice("__back__", "Back", "Return to validation menu."),
        ])
        selected = ui.choose(choices, prompt="recipe", header="Choose a validation recipe", initial_query=query)
        if selected is None or selected.key == "__back__":
            return 0
        if selected.key == "__filter__":
            query = ui.ask("Recipe filter: name, label, method, scope, or target", default=query)
            continue
        if selected.key == "__duplicate__":
            target = ui.choose([Choice(item.name, _recipe_label(item), item.description) for item in recipes], prompt="duplicate-recipe")
            if target:
                recipe = next(item for item in recipes if item.name == target.key)
                _copy_recipe_dialog(ui, config, recipe, rename=False)
            continue
        if selected.key == "__rename__":
            custom = [item for item in recipes if not item.builtin]
            target = ui.choose([Choice(item.name, _recipe_label(item), item.scope) for item in custom], prompt="rename-recipe")
            if target:
                recipe = next(item for item in custom if item.name == target.key)
                _copy_recipe_dialog(ui, config, recipe, rename=True)
            continue
        if selected.key == "__delete__":
            custom = [item for item in recipes if not item.builtin]
            target = ui.choose([Choice(item.name, _recipe_label(item), item.scope) for item in custom], prompt="delete-recipe")
            if target and ui.confirm(f"Delete recipe {target.key!r}?", default=False):
                delete_recipe(config.repo_root, target.key)
            continue
        recipe = next(item for item in recipes if item.name == selected.key)
        draft = _draft_from_recipe(base, recipe, ui, matrix_values=",".join(catalog.targets))
        if draft is None:
            continue
        rc = _review(ui, config, draft)
        if rc != 0:
            return rc


def _draft_from_recipe(
    base: ValidationDraft,
    recipe: ValidationRecipe,
    ui: TerminalUI,
    *,
    artifact: str = "",
    matrix_values: str = "",
) -> ValidationDraft | None:
    required_artifact = any(item == "{artifact}" for item in recipe.argv)
    required_matrix = any(item == "{matrix}" for item in recipe.argv)
    if required_artifact and not artifact:
        if not ui.interactive:
            console.print(f"[red]Recipe {recipe.label!r} requires --artifact PATH.[/red]")
            return None
        artifact = ui.ask("Artifact path")
    if required_matrix and not matrix_values:
        if not ui.interactive:
            console.print(f"[red]Recipe {recipe.label!r} requires --matrix-values VALUES.[/red]")
            return None
        matrix_values = ui.ask("Comma-separated matrix values")
    try:
        argv = expand_recipe(recipe, artifact=artifact, matrix=matrix_values)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    draft = copy.deepcopy(base)
    draft.raw_override = argv
    if recipe.prefix_argv:
        draft.global_override = list(recipe.prefix_argv)
    return draft


def _save_recipe_dialog(ui: TerminalUI, config: ProjectConfig, draft: ValidationDraft) -> None:
    label = ui.ask("Recipe label", default=f"Custom {draft.method_label}")
    name = ui.ask("Recipe name", default=label.lower().replace(" ", "-"))
    description = ui.ask("Description", default="Generated by the Devtool validation menu")
    scope = ui.choose([
        Choice("repository", "Repository recipe", str(config.repo_root / ".devtool" / "validation-recipes.toml")),
        Choice("global", "Global recipe", str(Path.home() / ".config" / "devtool" / "validation-recipes.toml")),
    ], prompt="recipe-scope")
    if scope is None:
        return
    path = save_recipe(
        config.repo_root,
        name=name,
        label=label,
        description=description,
        argv=draft.command_argv(),
        prefix_argv=draft.recipe_prefix_argv(),
        repository=scope.key == "repository",
    )
    console.print(f"[green]Saved validation recipe[/green] {path}")


def _copy_recipe_dialog(ui: TerminalUI, config: ProjectConfig, recipe: ValidationRecipe, *, rename: bool) -> None:
    label = ui.ask("Recipe label", default=recipe.label)
    name = ui.ask("Recipe name", default=recipe.name)
    description = ui.ask("Description", default=recipe.description)
    scope = ui.choose([
        Choice("repository", "Repository recipe", str(config.repo_root / ".devtool" / "validation-recipes.toml")),
        Choice("global", "Global recipe", str(Path.home() / ".config" / "devtool" / "validation-recipes.toml")),
    ], prompt="recipe-scope")
    if scope is None:
        return
    if rename:
        delete_recipe(config.repo_root, recipe.name)
    path = save_recipe(
        config.repo_root,
        name=name,
        label=label,
        description=description,
        argv=recipe.argv,
        prefix_argv=recipe.prefix_argv,
        repository=scope.key == "repository",
    )
    verb = "Renamed" if rename else "Duplicated"
    console.print(f"[green]{verb} validation recipe[/green] {path}")


def _control_draft(base: ValidationDraft, catalog: ValidationCatalog, action: str) -> ValidationDraft:
    draft = copy.deepcopy(base)
    draft.method = "control"
    draft.action = action
    _attach_session(draft, catalog)
    if action == "pause":
        draft.after_phase = True
    return draft


def _attach_session(draft: ValidationDraft, catalog: ValidationCatalog) -> None:
    draft.session_id = str(catalog.session.get("session_id") or "")
    draft.revision = catalog.revision


def _recommended_retry_profile(catalog: ValidationCatalog) -> str:
    supported = {"automatic", "android-low-power", "standard"}
    candidates = [str(catalog.session.get("recommended_retry_profile") or "").strip()]
    candidates.extend(
        str(item.get("recommended_retry_profile") or item.get("recommended_profile") or "").strip()
        for item in catalog.session.get("phases", []) or []
        if isinstance(item, dict)
    )
    for candidate in candidates:
        if candidate in supported and candidate != "automatic":
            return candidate
    return ""


def _preview_safe_retry(catalog: ValidationCatalog, profile: str) -> str:
    current = str(catalog.session.get("resource_profile") or "automatic")
    details = [
        "# Safe retry",
        "",
        f"- Current profile: `{current}`",
        f"- Recommended profile: `{profile}`",
        f"- Session revision guard: `{catalog.revision}`",
    ]
    if profile == "android-low-power":
        details.extend([
            "- Workers: configured → 1",
            "- CPU limit: configured → 2",
            "- Gradle daemon: configured → disabled",
            "- Process priority: normal/configured → low",
            "- Gradle parallelism: configured → disabled",
        ])
    elif profile == "standard":
        details.append("- Android low-power constraints are removed in favor of standard project policy.")
    return "\n".join(details)


def _phase_scope_preview(title: str, selected: list[str], closure: list[str], already: list[str], phases: list[dict]) -> str:
    status = {str(item.get("name") or item.get("key")): str(item.get("status") or "pending") for item in phases}
    runnable = [name for name in closure if name not in already]
    blocked = [name for name in closure if status.get(name) in {"blocked", "failed", "interrupted"}]
    invalidated = [name for name in closure if status.get(name) == "invalidated"]
    return "\n".join([
        f"# {title}",
        "",
        "- Selected: " + (", ".join(selected) or "none"),
        "- Pending run: " + (" -> ".join(runnable) or "none"),
        "- Already satisfied: " + (", ".join(already) or "none"),
        "- Blocked or retryable: " + (", ".join(blocked) or "none"),
        "- Invalidated: " + (", ".join(invalidated) or "none"),
    ])


def _phase_preview(item: dict, phases: list[dict], *, catalog: ValidationCatalog | None = None) -> str:
    name = str(item.get("name") or item.get("key") or "phase")
    label = str(item.get("label") or name)
    status = str(item.get("status") or "pending")
    dependencies = [str(value) for value in item.get("depends_on", []) or []]
    preview = f"# {label}\n\n- Name: `{name}`\n- Status: **{status}**\n- Dependencies: {', '.join(dependencies) or 'none'}"
    reason = _phase_block_reason(item, phases)
    if reason != "Prerequisites satisfied":
        preview += f"\n- Exact phase availability: {reason}"
    if catalog is not None:
        closure, already = dependency_closure(catalog.phases, [name])
        preview += "\n- Dependency closure: " + (" -> ".join(closure) or "none")
        if already:
            preview += "\n- Already satisfied: " + ", ".join(already)
    return preview


def _phase_choices(phases: list[dict], *, dependency_preview: bool = False, catalog: ValidationCatalog | None = None) -> list[Choice]:
    output: list[Choice] = []
    for item in phases:
        name = str(item.get("name") or item.get("key") or "phase")
        label = str(item.get("label") or name)
        status = str(item.get("status") or "pending")
        description = f"{status} · attempts {item.get('attempts', 0)}"
        preview = _phase_preview(item, phases, catalog=catalog if dependency_preview else None)
        output.append(Choice(name, label, description, preview))
    return output


def _phase_prerequisites_satisfied(item: dict, phases: list[dict]) -> bool:
    return _phase_block_reason(item, phases) == "Prerequisites satisfied"


def _phase_block_reason(item: dict, phases: list[dict]) -> str:
    by_key = {str(value.get("key")): value for value in phases}
    for dependency in item.get("depends_on", []) or []:
        dependency_key = str(dependency)
        status = str(by_key.get(dependency_key, {}).get("status") or "pending")
        if status not in {"passed", "skipped", "not-applicable"}:
            return f"Waiting for prerequisite {dependency_key} ({status})"
    status = str(item.get("status") or "pending")
    if status not in {"pending", "failed", "blocked", "interrupted"}:
        return f"Phase status is {status}"
    return "Prerequisites satisfied"


def _recent_artifacts(repo_root: Path) -> list[Path]:
    root = repo_root.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    candidates: list[Path] = []
    for directory in (root, root / "dist", root / "build", root / ".devtool-artifact"):
        if not directory.is_dir():
            continue
        for pattern in ("*.zip", "*.tar", "*.tar.gz", "*.tgz"):
            candidates.extend(directory.glob(pattern))
    return sorted(
        dict.fromkeys(path for path in candidates if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:12]


def _filter_recipes(recipes: list[ValidationRecipe], query: str) -> list[ValidationRecipe]:
    terms = [term.strip().lower() for term in query.split() if term.strip()]
    if not terms:
        return recipes
    output = []
    for recipe in recipes:
        haystack = " ".join([
            recipe.name,
            recipe.label,
            recipe.description,
            recipe.scope,
            " ".join(recipe.argv),
            " ".join(recipe.prefix_argv),
        ]).lower()
        if all(term in haystack for term in terms):
            output.append(recipe)
    return output


def _recipe_label(recipe: ValidationRecipe) -> str:
    origin = "built-in" if recipe.builtin else recipe.scope
    return f"{recipe.label} [{origin}]"


def _change_summary(draft: ValidationDraft) -> str:
    if draft.method == "control":
        return f"- Sends validation control action `{draft.action}` to session `{draft.session_id or 'active'}`."
    if draft.method == "apply":
        values = draft.artifacts or ([draft.artifact] if draft.artifact else [])
        return f"- Applies {len(values)} artifact(s) with `{draft.apply_mode}` validation policy."
    if draft.method.startswith("artifact-"):
        values = draft.artifacts or ([draft.artifact] if draft.artifact else [])
        return f"- Validates {len(values)} artifact path(s) without applying repository changes."
    if draft.is_sequential:
        scope = draft.validation_scope or draft.phase_only or draft.through or "next"
        return f"- Advances durable validation using `{scope}` scope and revision `{draft.revision}`."
    if draft.method == "watch":
        return f"- Starts watch validation every {draft.watch_interval:g} seconds until interrupted."
    if draft.method == "matrix":
        return f"- Runs `{draft.matrix_mode}` matrix across {len(draft.matrix_items)} item(s)."
    return "- Runs one validation command against the selected repository context."


def _clear_phase_scope(draft: ValidationDraft) -> None:
    draft.validation_scope = ""
    draft.phases = []
    draft.phase_only = ""
    draft.through = ""
    draft.complete = False


def _clear_test_selection(draft: ValidationDraft) -> None:
    draft.test_profile = ""
    draft.selectors = []
    draft.test_ids = []
    draft.tags = []
    draft.exclude_tags = []
    draft.paths = []
    draft.classes = []
    draft.methods = []
    draft.test_filter = ""
    draft.pytest_tests = []
    draft.pytest_markers = []
    draft.pytest_k = ""
    draft.pytest_args = []
    draft.pytest_include_defaults = False
    draft.tasks = []
    draft.failed_tests = False
    draft.changed_tests = False
    draft.changed_from = "HEAD"
    draft.instrumented = False


def _ask_csv(ui: TerminalUI, label: str, current: Iterable[str]) -> list[str]:
    raw = ui.ask(label + " (comma-separated)", default=", ".join(current))
    return list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))


def _ask_int(ui: TerminalUI, label: str, current: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    while True:
        raw = ui.ask(label, default=str(current))
        try:
            value = int(raw)
        except ValueError:
            console.print("[yellow]Enter a whole number.[/yellow]")
            continue
        if minimum is not None and value < minimum:
            console.print(f"[yellow]Minimum value is {minimum}.[/yellow]")
            continue
        if maximum is not None and value > maximum:
            console.print(f"[yellow]Maximum value is {maximum}.[/yellow]")
            continue
        return value


def _ask_optional_int(ui: TerminalUI, label: str, current: int | None, *, minimum: int = 0) -> int | None:
    while True:
        raw = ui.ask(label, default="" if current is None else str(current))
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            console.print("[yellow]Enter a whole number or leave blank.[/yellow]")
            continue
        if value < minimum:
            console.print(f"[yellow]Minimum value is {minimum}.[/yellow]")
            continue
        return value


def _ask_float(ui: TerminalUI, label: str, current: float, *, minimum: float = 0) -> float:
    while True:
        raw = ui.ask(label, default=str(current))
        try:
            value = float(raw)
        except ValueError:
            console.print("[yellow]Enter a number.[/yellow]")
            continue
        if value < minimum:
            console.print(f"[yellow]Minimum value is {minimum}.[/yellow]")
            continue
        return value


def _ask_tristate(ui: TerminalUI, label: str, current: bool | None, enabled: str, disabled: str) -> bool | None:
    selected = ui.choose([
        Choice("default", "Configured default", "Do not add an invocation override."),
        Choice("on", enabled, "Explicitly enable for this invocation."),
        Choice("off", disabled, "Explicitly disable for this invocation."),
    ], prompt="override", header=label)
    if selected is None:
        return current
    return True if selected.key == "on" else False if selected.key == "off" else None


def _list_label(values: Iterable[str]) -> str:
    items = list(values)
    return ", ".join(items) if items else "none"


def _refresh_for_draft(catalog: ValidationCatalog, draft: ValidationDraft, *, include_runtime_tasks: bool = False) -> None:
    refresh_catalog(
        catalog,
        repo=draft.repo,
        config_path=draft.config,
        target=draft.target,
        variant=draft.variant,
        solution=draft.solution,
        project=draft.project,
        project_type=draft.project_type,
        include_runtime_tasks=include_runtime_tasks,
    )


def _context_preview(draft: ValidationDraft, catalog: ValidationCatalog) -> str:
    lines = ["# Detected validation context", "", f"- Project type: `{catalog.config.kind}`", f"- Target: `{draft.target or catalog.config.selected_target or 'default'}`"]
    environment = "host" if draft.host else f"chroot:{draft.chroot}" if draft.chroot else draft.execution_environment or "auto"
    lines.append(f"- Requested environment: `{environment}`")
    if catalog.config.kind in {"android", "gradle"}:
        lines.append("- Android/Gradle project detected; the resumable runner plan is resolved from the repository configuration.")
    if catalog.config.chroot_name and (draft.execution_environment == "auto" and not draft.host and not draft.chroot):
        lines.append(f"- Configured chroot candidate: `{catalog.config.chroot_name}`")
    if catalog.plan_error:
        lines.append(f"- Plan warning: {catalog.plan_error}")
    return "\n".join(lines)


def _next_phase_description(catalog: ValidationCatalog) -> str:
    pending = catalog.pending_phases
    if not pending:
        return "No pending phase is visible in the current plan."
    item = pending[0]
    return f"{item.get('label') or item.get('name')} · {item.get('status')}"


def _preview_start(catalog: ValidationCatalog) -> str:
    phases = catalog.phases
    if not phases:
        return "# Start durable validation\n\nPlan resolution failed:\n\n```text\n" + (catalog.plan_error or "No phases resolved.") + "\n```"
    return "# Start durable validation\n\nResolved runner plan:\n\n" + "\n".join(f"{index}. {item.get('label') or item.get('name')}" for index, item in enumerate(phases, start=1))


def _preview_session(catalog: ValidationCatalog) -> str:
    return f"# Active validation session\n\n- Session: `{catalog.session.get('session_id', '')}`\n- State: **{catalog.state}**\n- Revision: {catalog.revision}\n- Resource profile: {catalog.session.get('resource_profile', 'automatic')}"


def _preview_control(catalog: ValidationCatalog, action: str) -> str:
    command = f"devtool validate {action} --session {catalog.session.get('session_id', '')}"
    if action not in {"status", "events", "plan", "report"}:
        command += f" --if-revision {catalog.revision}"
    return _preview_session(catalog) + f"\n\n## Command shape\n\n```bash\n{command}\n```"


def _preview_run_scope(catalog: ValidationCatalog, scope: str) -> str:
    base = _preview_session(catalog)
    return base + f"\n\n## Scope\n\n`{scope}`\n\nPending phases: " + ", ".join(str(item.get("name")) for item in catalog.pending_phases)


def _preview_method(method: str) -> str:
    descriptions = {
        "validate": "Run the normal validation pipeline once.",
        "validate-all": "Continue independent validation steps and collect failures.",
        "watch": "Observe repository changes and re-run validation.",
        "matrix": "Expand validation across runtime IDs, targets, or variants.",
        "artifact": "Validate archive integrity without applying changes.",
        "apply": "Choose how validation participates in artifact application.",
        "custom": "Configure every supported validation method and flag.",
        "recipes": "Run built-in or saved validation command presets.",
    }
    return f"# {method.replace('-', ' ').title()}\n\n{descriptions.get(method, '')}"


def _preview_plan(catalog: ValidationCatalog) -> str:
    lines = ["# Validation plan", ""]
    for item in catalog.phases:
        dependencies = ", ".join(str(value) for value in item.get("depends_on", []) or []) or "none"
        lines.append(f"- **{item.get('label') or item.get('name')}**: {item.get('status')} · depends on {dependencies}")
    return "\n".join(lines)


def _preview_events(catalog: ValidationCatalog) -> str:
    lines = ["# Recent validation events", ""]
    for item in catalog.session.get("events", []) or []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('timestamp', '')}` **{item.get('type', '')}** {item.get('message', '')}")
    return "\n".join(lines) if len(lines) > 2 else "# Recent validation events\n\nNo events returned."


def _preview_draft(draft: ValidationDraft) -> str:
    return draft.review_markdown()


def _recipe_preview(recipe: ValidationRecipe) -> str:
    argv = [*recipe.prefix_argv, *recipe.argv]
    return f"# {recipe.label}\n\n{recipe.description}\n\n- Scope: {recipe.scope}\n- Built-in: {'yes' if recipe.builtin else 'no'}\n\n```bash\ndevtool {shell_join(argv)}\n```"


def _context_description(draft: ValidationDraft) -> str:
    environment = "host" if draft.host else f"chroot:{draft.chroot}" if draft.chroot else draft.execution_environment
    return f"{draft.target or 'default'} / {draft.variant or 'default'} / {environment}"


def _pipeline_description(draft: ValidationDraft) -> str:
    enabled = []
    if not draft.skip_restore:
        enabled.append("restore")
    if not draft.skip_build:
        enabled.append("build")
    if not draft.skip_test:
        enabled.append("test")
    return " + ".join(enabled) or "none"


def _scope_description(draft: ValidationDraft) -> str:
    return draft.validation_scope or draft.phase_only or draft.through or "not configured"


def _tests_description(draft: ValidationDraft) -> str:
    return draft.test_profile or _list_label(draft.tasks or draft.selectors or draft.test_ids) or "all configured"


def _resource_description(draft: ValidationDraft) -> str:
    overrides = []
    for label, value in (
        ("workers", draft.max_workers),
        ("cpus", draft.cpu_limit),
        ("heap", draft.gradle_heap_mb),
        ("guard", draft.memory_guard_mb),
    ):
        if value is not None:
            overrides.append(f"{label}={value}")
    return draft.resource_profile + (" · " + ", ".join(overrides) if overrides else "")


def _guard_description(draft: ValidationDraft) -> str:
    values = []
    if draft.stop_below_battery:
        values.append(f"battery {draft.stop_below_battery}%")
    if draft.require_charging:
        values.append("charging")
    if draft.stop_above_temperature:
        values.append(f"temp {draft.stop_above_temperature}°C")
    if draft.stop_below_disk_mb:
        values.append(f"disk {draft.stop_below_disk_mb}MiB")
    return ", ".join(values) or "defaults"


def _output_description(draft: ValidationDraft) -> str:
    values = []
    if draft.verbose:
        values.append("verbose")
    if draft.dry_run:
        values.append("dry-run")
    if draft.fail_fast:
        values.append("fail-fast")
    if draft.continue_on_error:
        values.append("continue")
    if draft.auto_hud is False:
        values.append("no HUD")
    else:
        values.append(draft.hud_mode)
    return ", ".join(values)
