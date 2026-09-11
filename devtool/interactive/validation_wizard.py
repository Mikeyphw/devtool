from __future__ import annotations

from dataclasses import dataclass, field
import argparse
from pathlib import Path
import shlex
from typing import Any, Iterable

from .rendering import markdown_table, shell_join_multiline


METHOD_LABELS = {
    "validate": "Smart one-shot",
    "validate-all": "Collect all failures",
    "sequential-start": "Sequential session",
    "sequential-continue": "Resume session",
    "watch": "Watch mode",
    "matrix": "Validation matrix",
    "wine": "Wine validation",
    "artifact-overlay": "Overlay integrity",
    "artifact-bundle": "Overlay bundle integrity",
    "apply": "Apply-time validation",
    "control": "Session control",
}


@dataclass(slots=True)
class ValidationDraft:
    repo: str = ""
    config: str = ""
    target: str = ""
    variant: str = ""
    solution: str = ""
    project: str = ""
    project_type: str = "auto"
    execution_environment: str = "auto"
    chroot: str = ""
    host: bool = False
    no_auto_chroot: bool = False

    method: str = "validate"
    action: str = ""
    artifact: str = ""
    artifacts: list[str] = field(default_factory=list)
    apply_mode: str = "validate"
    fail_on_conflict: bool = False
    ignore_checksum: bool = False
    no_artifact_script: bool = False

    skip_restore: bool = False
    skip_build: bool = False
    skip_test: bool = False
    watch_interval: float = 1.5
    matrix_mode: str = "rid"
    matrix_items: list[str] = field(default_factory=list)

    validation_scope: str = ""
    phases: list[str] = field(default_factory=list)
    phase_only: str = ""
    through: str = ""
    from_phase: str = ""
    pause_after: str = ""
    complete: bool = False
    max_phases: int = 0
    max_runtime: str = ""
    require_complete: bool = False
    retry_failed_phase: bool = False
    restart_session: bool = False
    session_id: str = ""
    revision: int = -1

    test_profile: str = ""
    selectors: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    test_filter: str = ""
    pytest_tests: list[str] = field(default_factory=list)
    pytest_markers: list[str] = field(default_factory=list)
    pytest_k: str = ""
    pytest_args: list[str] = field(default_factory=list)
    pytest_include_defaults: bool = False
    tasks: list[str] = field(default_factory=list)
    failed_tests: bool = False
    changed_tests: bool = False
    changed_from: str = "HEAD"
    instrumented: bool = False

    resource_profile: str = "automatic"
    max_workers: int | None = None
    cpu_limit: int | None = None
    gradle_heap_mb: int | None = None
    memory_guard_mb: int | None = None
    low_priority: bool | None = None
    parallel: bool | None = None
    build_cache: bool | None = None
    gradle_daemon: bool | None = None
    stop_gradle_after_phase: bool = False
    keep_gradle_daemon: bool = False
    max_infrastructure_retries: int = 1
    max_resource_retries: int = 1
    no_auto_retry: bool = False

    stop_below_battery: int = 0
    require_charging: bool = False
    stop_above_temperature: float = 0.0
    stop_below_disk_mb: int = 256

    failure_action: str = "pause"
    rollback_on_failure: bool = False
    keep_changes: bool = False
    rollback: bool = False
    after_phase: bool = False
    cancel_pause: bool = False
    reset_all: bool = False
    reason: str = ""

    verbose: bool = False
    yes: bool = False
    force: bool = False
    dry_run: bool = False
    continue_on_error: bool = False
    fail_fast: bool = False
    performance: bool = False
    performance_file: str = ""
    copy: bool = False
    no_clipboard: bool = False
    copy_via: str = "auto"
    stdout_payload: str = "auto"
    machine_file: str = ""
    json_output: bool = False
    machine_format: str = ""
    auto_hud: bool | None = None
    hud_mode: str = "desktop"
    hud_overlay: bool | None = None

    raw_override: list[str] = field(default_factory=list)
    global_override: list[str] = field(default_factory=list)

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "ValidationDraft":
        return cls(
            repo=str(getattr(args, "repo", "") or ""),
            config=str(getattr(args, "config", "") or ""),
            target=str(getattr(args, "target", "") or ""),
            variant=str(getattr(args, "variant", "") or ""),
            solution=str(getattr(args, "solution", "") or ""),
            project=str(getattr(args, "project", "") or ""),
            project_type=str(getattr(args, "project_type", "auto") or "auto"),
            execution_environment=str(getattr(args, "execution_environment", "auto") or "auto"),
            chroot=str(getattr(args, "chroot", "") or ""),
            host=bool(getattr(args, "host", False)),
            no_auto_chroot=bool(getattr(args, "no_auto_chroot", False)),
            verbose=bool(getattr(args, "verbose", False)),
            yes=bool(getattr(args, "yes", False)),
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            continue_on_error=bool(getattr(args, "continue_on_error", False)),
            fail_fast=bool(getattr(args, "fail_fast", False)),
            performance=bool(getattr(args, "performance", False)),
            performance_file=str(getattr(args, "performance_file", "") or ""),
            copy=bool(getattr(args, "copy", False)),
            no_clipboard=bool(getattr(args, "no_clipboard", False)),
            copy_via=str(getattr(args, "copy_via", "auto") or "auto"),
            stdout_payload=str(getattr(args, "stdout_payload", "auto") or "auto"),
            machine_file=str(getattr(args, "machine_file", "") or ""),
            json_output=bool(getattr(args, "json", False)),
            machine_format=str(getattr(args, "machine", "") or ""),
            auto_hud=getattr(args, "auto_hud", None),
            hud_mode=str(getattr(args, "hud_mode", "desktop") or "desktop"),
            hud_overlay=True if bool(getattr(args, "hud_overlay", False)) else False if bool(getattr(args, "hud_no_overlay", False)) else None,
        )

    @property
    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method)

    @property
    def is_sequential(self) -> bool:
        return self.method in {"sequential-start", "sequential-continue", "control"}

    def global_argv(self) -> list[str]:
        if self.global_override:
            return list(self.global_override)
        argv: list[str] = []
        _pair(argv, "-r", self.repo)
        _pair(argv, "-c", self.config)
        _pair(argv, "--target", self.target)
        _pair(argv, "--variant", self.variant)
        _pair(argv, "--solution", self.solution)
        _pair(argv, "--project", self.project)
        if self.project_type and self.project_type != "auto":
            _pair(argv, "--project-type", self.project_type)
        if self.host:
            argv.append("--host")
        elif self.chroot:
            _pair(argv, "--chroot", self.chroot)
        elif self.execution_environment and self.execution_environment != "auto":
            _pair(argv, "--execution-environment", self.execution_environment)
        if self.no_auto_chroot:
            argv.append("--no-auto-chroot")
        if self.verbose:
            argv.append("--verbose")
        if self.yes:
            argv.append("--yes")
        if self.force:
            argv.append("--force")
        if self.dry_run:
            argv.append("--dry-run")
        if self.continue_on_error:
            argv.append("--continue-on-error")
        if self.fail_fast:
            argv.append("--fail-fast")
        if self.performance:
            argv.append("--performance")
        _pair(argv, "--performance-file", self.performance_file)
        if self.copy:
            argv.append("--copy")
        if self.no_clipboard:
            argv.append("--no-clipboard")
        if self.copy_via != "auto":
            _pair(argv, "--copy-via", self.copy_via)
        if self.stdout_payload != "auto":
            _pair(argv, "--stdout-payload", self.stdout_payload)
        _pair(argv, "--machine-file", self.machine_file)
        if self.auto_hud is True:
            argv.append("--auto-hud")
        elif self.auto_hud is False:
            argv.append("--no-hud")
        if self.hud_mode and self.hud_mode != "desktop":
            _pair(argv, "--hud-mode", self.hud_mode)
        if self.hud_overlay is True:
            argv.append("--hud-overlay")
        elif self.hud_overlay is False:
            argv.append("--hud-no-overlay")
        return argv

    def command_argv(self) -> list[str]:
        if self.raw_override:
            return list(self.raw_override)
        if self.method == "artifact-overlay":
            argv = ["validate", "overlay", self.artifact]
            return self._artifact_flags(argv)
        if self.method == "artifact-bundle":
            values = self.artifacts or ([self.artifact] if self.artifact else [])
            argv = ["validate-overlays", *values]
            return self._artifact_flags(argv)
        if self.method == "apply":
            return self._apply_argv()
        if self.method == "control":
            return self._control_argv()

        command = {
            "validate": "validate",
            "validate-all": "validate-all",
            "sequential-start": "validate",
            "sequential-continue": "validate",
            "watch": "validate",
            "matrix": "validate",
            "wine": "wine-validate",
        }.get(self.method, "validate")
        argv = [command]
        if self.method == "sequential-start":
            argv.append("start")
        elif self.method == "sequential-continue":
            argv.append(self.action or ("retry" if self.retry_failed_phase else "continue"))
        if self.method == "watch":
            argv.extend(["--watch", "--watch-interval", _number(self.watch_interval)])
        if self.method == "matrix":
            _pair(argv, "--matrix", ",".join(self.matrix_items))
            _pair(argv, "--matrix-mode", self.matrix_mode)

        self._append_pipeline_flags(argv)
        self._append_sequential_flags(argv)
        self._append_test_flags(argv)
        self._append_resource_flags(argv)
        self._append_guard_flags(argv)
        return argv

    def full_argv(self, *, executable: str = "devtool") -> list[str]:
        return [executable, *self.global_argv(), *self.command_argv()]

    def recipe_prefix_argv(self) -> list[str]:
        """Global invocation arguments that must survive recipe round-trips."""
        return self.global_argv()

    def _append_pipeline_flags(self, argv: list[str]) -> None:
        if self.skip_restore:
            argv.append("--skip-restore")
        if self.skip_build:
            argv.append("--skip-build")
        if self.skip_test:
            argv.append("--skip-test")

    def _append_sequential_flags(self, argv: list[str]) -> None:
        if not self.is_sequential:
            return
        if self.restart_session and self.method == "sequential-start":
            argv.append("--restart-session")
        if self.retry_failed_phase and (not self.action or self.action == "continue"):
            argv.append("--retry-failed")
        if self.validation_scope:
            _pair(argv, "--scope", self.validation_scope)
        for phase in self.phases:
            _pair(argv, "--phase", phase)
        _pair(argv, "--phase-only", self.phase_only)
        _pair(argv, "--through", self.through)
        _pair(argv, "--from-phase", self.from_phase)
        _pair(argv, "--pause-after", self.pause_after)
        if self.complete and self.validation_scope != "remaining":
            argv.append("--complete")
        if self.max_phases > 0:
            _pair(argv, "--max-phases", str(self.max_phases))
        _pair(argv, "--max-runtime", self.max_runtime)
        if self.require_complete:
            argv.append("--require-complete")
        _pair(argv, "--session", self.session_id)
        if self.revision >= 0 and self.method == "sequential-continue":
            _pair(argv, "--if-revision", str(self.revision))
        if self.json_output:
            argv.append("--json")
        if self.machine_format:
            _pair(argv, "--machine", self.machine_format)

    def _append_test_flags(self, argv: list[str]) -> None:
        _pair(argv, "--test-profile", self.test_profile)
        _repeat(argv, "--select", self.selectors)
        _repeat(argv, "--id", self.test_ids)
        _repeat(argv, "--tag", self.tags)
        _repeat(argv, "--exclude-tag", self.exclude_tags)
        _repeat(argv, "--path", self.paths)
        _repeat(argv, "--class", self.classes)
        _repeat(argv, "--method", self.methods)
        _pair(argv, "--filter", self.test_filter)
        _repeat(argv, "--pytest-test", self.pytest_tests)
        _repeat(argv, "--pytest-marker", self.pytest_markers)
        _pair(argv, "--pytest-k", self.pytest_k)
        _repeat(argv, "--pytest-arg", self.pytest_args)
        if self.pytest_include_defaults:
            argv.append("--pytest-include-defaults")
        _repeat(argv, "--task", self.tasks)
        if self.failed_tests:
            argv.append("--failed")
        if self.changed_tests:
            argv.append("--changed")
            if self.changed_from and self.changed_from != "HEAD":
                _pair(argv, "--changed-from", self.changed_from)
        if self.instrumented:
            argv.append("--instrumented")

    def _append_resource_flags(self, argv: list[str]) -> None:
        if self.resource_profile and self.resource_profile != "automatic":
            _pair(argv, "--resource-profile", self.resource_profile)
        _optional_int(argv, "--max-workers", self.max_workers)
        _optional_int(argv, "--cpu-limit", self.cpu_limit)
        _optional_int(argv, "--gradle-heap-mb", self.gradle_heap_mb)
        _optional_int(argv, "--memory-guard-mb", self.memory_guard_mb, allow_zero=True)
        _tri_flag(argv, self.low_priority, "--low-priority", "--normal-priority")
        _tri_flag(argv, self.parallel, "--parallel", "--no-parallel")
        _tri_flag(argv, self.build_cache, "--build-cache", "--no-build-cache")
        _tri_flag(argv, self.gradle_daemon, "--daemon", "--no-daemon")
        if self.stop_gradle_after_phase:
            argv.append("--stop-gradle-after-phase")
        elif self.keep_gradle_daemon:
            argv.append("--keep-gradle-daemon")
        if self.no_auto_retry:
            argv.append("--no-auto-retry")
        else:
            if self.max_infrastructure_retries != 1:
                _pair(argv, "--max-infrastructure-retries", str(self.max_infrastructure_retries))
            if self.max_resource_retries != 1:
                _pair(argv, "--max-resource-retries", str(self.max_resource_retries))
        if self.failure_action and self.failure_action != "pause":
            _pair(argv, "--failure-action", self.failure_action)
        if self.rollback_on_failure:
            argv.append("--rollback-on-failure")

    def _append_guard_flags(self, argv: list[str]) -> None:
        if self.stop_below_battery > 0:
            _pair(argv, "--stop-below-battery", str(self.stop_below_battery))
        if self.require_charging:
            argv.append("--require-charging")
        if self.stop_above_temperature > 0:
            _pair(argv, "--stop-above-temperature", _number(self.stop_above_temperature))
        if self.stop_below_disk_mb != 256:
            _pair(argv, "--stop-below-disk-mb", str(max(0, self.stop_below_disk_mb)))

    def _artifact_flags(self, argv: list[str]) -> list[str]:
        if self.fail_on_conflict:
            argv.append("--fail-on-conflict")
        if self.ignore_checksum:
            argv.append("--ignore-checksum")
        return argv

    def _apply_argv(self) -> list[str]:
        values = self.artifacts or ([self.artifact] if self.artifact else [])
        command = "apply-overlays" if len(values) > 1 else "apply-overlay"
        argv = [command, *values]
        mode = self.apply_mode
        if mode == "validate":
            argv.append("--validate")
        elif mode == "sequential":
            argv.extend(["--validate", "--sequential"])
        elif mode == "deferred":
            argv.append("--defer-validate")
        elif mode == "static":
            argv.append("--static-validate")
        elif mode == "validate-only":
            argv.append("--validate-only")
        elif mode == "no-validate":
            argv.append("--no-validate")
        self._append_pipeline_flags(argv)
        if mode in {"validate", "sequential", "validate-only"}:
            self._append_test_flags(argv)
            self._append_resource_flags(argv)
            self._append_guard_flags(argv)
        if self.no_artifact_script:
            argv.append("--no-artifact-script")
        if self.ignore_checksum:
            argv.append("--ignore-checksum")
        return argv

    def _control_argv(self) -> list[str]:
        action = self.action or "status"
        argv = ["validate", action]
        _pair(argv, "--session", self.session_id)
        if self.revision >= 0 and action not in {"status", "checkpoint", "plan", "events", "report"}:
            _pair(argv, "--if-revision", str(self.revision))
        if action == "pause":
            if self.cancel_pause:
                argv.append("--cancel")
            elif self.after_phase:
                argv.append("--after-phase")
        elif action == "abort":
            if self.rollback:
                argv.append("--rollback")
            elif self.keep_changes:
                argv.append("--keep-changes")
        elif action == "reset":
            if self.reset_all:
                argv.append("--all")
            elif self.phases:
                _pair(argv, "--phase", self.phases[0])
            elif self.from_phase:
                _pair(argv, "--from-phase", self.from_phase)
        elif action == "accept-plan-change":
            _pair(argv, "--reason", self.reason)
        if self.json_output:
            argv.append("--json")
        if self.machine_format:
            _pair(argv, "--machine", self.machine_format)
        return argv

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.raw_override:
            return errors
        if self.method in {"artifact-overlay", "apply"} and not (self.artifact or self.artifacts):
            errors.append("An artifact path is required.")
        if self.method == "artifact-bundle" and not (self.artifacts or self.artifact):
            errors.append("At least one overlay or bundle path is required.")
        if self.skip_restore and self.skip_build and self.skip_test and self.apply_mode != "static":
            errors.append("Restore, build, and test cannot all be skipped.")
        if self.method == "matrix" and not self.matrix_items:
            errors.append("Matrix validation requires one or more items.")
        if self.validation_scope == "through" and not self.through:
            errors.append("Through scope requires an ending phase.")
        if self.validation_scope == "selected" and not self.phases:
            errors.append("Selected scope requires one or more phases.")
        if self.validation_scope in {"next", "remaining"} and (self.phases or self.through):
            errors.append(f"{self.validation_scope.title()} scope cannot include selected or ending phases.")
        if self.phase_only and (self.phases or self.through or self.complete):
            errors.append("Exact phase mode cannot be combined with other phase scopes.")
        if self.method == "watch" and self.is_sequential:
            errors.append("Watch mode cannot use a durable sequential session.")
        if self.no_auto_retry and (self.max_infrastructure_retries not in {0, 1} or self.max_resource_retries not in {0, 1}):
            errors.append("Retry counts are disabled while automatic retries are off.")
        if self.stop_gradle_after_phase and self.keep_gradle_daemon:
            errors.append("Gradle cannot be stopped and kept warm after the same phase.")
        if self.copy and self.no_clipboard:
            errors.append("Clipboard copy and clipboard disabling are mutually exclusive.")
        if self.method == "control" and self.action == "accept-plan-change" and not self.reason.strip():
            errors.append("Accepting a plan change requires an audit reason.")
        if self.machine_format and self.machine_format not in {"pretty", "compact"}:
            errors.append("Machine JSON format must be pretty or compact.")
        if (self.json_output or self.machine_format) and not (self.is_sequential or self.method == "control"):
            errors.append("Stable --json output is available only for resumable validation and validation-control actions.")
        if self.method == "apply" and self.apply_mode == "deferred" and (self.phases or self.through or self.phase_only):
            errors.append("Deferred apply creates the session first; phase selection happens when continuing it.")
        for path in self._artifact_paths():
            if not self._artifact_path_exists(path):
                errors.append(f"Artifact path does not exist: {path}")
        return errors

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.is_sequential and not self.require_complete and (
            self.validation_scope in {"next", "through", "selected"} or self.phase_only or self.max_phases > 0 or self.pause_after
        ):
            warnings.append("Validation may stop at a checkpoint and still return success because --require-complete is disabled.")
        if self.method == "watch":
            warnings.append("Watch mode continues until interrupted.")
        if self.failure_action == "rollback" or self.rollback_on_failure:
            warnings.append("A validation failure can roll back an attached apply transaction immediately.")
        if self.method == "apply" and self.apply_mode == "no-validate":
            warnings.append("Manifest-requested validation will be disabled for this apply.")
        if self.resource_profile == "android-low-power":
            warnings.append("Android low-power mode uses one worker, two CPUs, no daemon, low priority, and no Gradle parallelism unless explicitly overridden.")
        return warnings

    def _artifact_paths(self) -> list[str]:
        if self.method == "artifact-overlay":
            return [self.artifact] if self.artifact else []
        if self.method in {"artifact-bundle", "apply"}:
            return list(self.artifacts or ([self.artifact] if self.artifact else []))
        return []

    def _artifact_path_exists(self, value: str) -> bool:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.exists()
        if path.exists():
            return True
        if self.repo:
            return (Path(self.repo).expanduser() / path).exists()
        return False

    def summary_rows(self) -> list[tuple[str, Any]]:
        scope = self.validation_scope or ("exact " + self.phase_only if self.phase_only else "-")
        tests = self.test_profile or ", ".join(self.tasks or self.pytest_tests or self.selectors or self.test_ids) or (
            "failed" if self.failed_tests else "changed" if self.changed_tests else "all configured"
        )
        return [
            ("Method", self.method_label),
            ("Action", self.action or "-"),
            ("Repository", self.repo or "."),
            ("Target", self.target or "default"),
            ("Variant", self.variant or "default"),
            ("Environment", "host" if self.host else f"chroot: {self.chroot}" if self.chroot else self.execution_environment),
            ("Pipeline", pipeline_label(self)),
            ("Phase scope", scope),
            ("Phases", self.phases or self.through or self.pause_after or "-"),
            ("Tests", tests),
            ("Resources", self.resource_profile),
            ("Failure", self.failure_action),
            ("Machine output", self.machine_format or ("json" if self.json_output else "standard")),
            ("Guards", guard_label(self)),
        ]

    def review_markdown(self, *, executable: str = "devtool", extra_sections: Iterable[tuple[str, str]] = ()) -> str:
        command = shell_join_multiline(self.full_argv(executable=executable))
        warnings = self.warnings()
        warning_text = "\n".join(f"- {item}" for item in warnings) if warnings else "- None"
        errors = self.validate()
        error_text = "\n".join(f"- {item}" for item in errors) if errors else "- None"
        sections = "".join(
            f"\n\n## {title}\n\n{text.strip() or '- None'}"
            for title, text in extra_sections
        )
        return (
            "# Validation Review\n\n"
            + markdown_table(self.summary_rows())
            + "\n\n## Command\n\n```bash\n"
            + command
            + "\n```\n\n## Warnings\n\n"
            + warning_text
            + "\n\n## Blocking problems\n\n"
            + error_text
            + sections
        )

    def replace_from_command(self, command: str) -> None:
        values = shlex.split(command)
        if values and values[0].endswith("devtool"):
            values = values[1:]
        self.raw_override = values


def pipeline_label(draft: ValidationDraft) -> str:
    enabled = []
    if not draft.skip_restore:
        enabled.append("restore")
    if not draft.skip_build:
        enabled.append("build")
    if not draft.skip_test:
        enabled.append("test")
    return " + ".join(enabled) or "none"


def guard_label(draft: ValidationDraft) -> str:
    values: list[str] = []
    if draft.stop_below_battery:
        values.append(f"battery ≥{draft.stop_below_battery}%")
    if draft.require_charging:
        values.append("charging")
    if draft.stop_above_temperature:
        values.append(f"temperature ≤{_number(draft.stop_above_temperature)}°C")
    if draft.stop_below_disk_mb != 256:
        values.append(f"disk ≥{draft.stop_below_disk_mb} MiB")
    if draft.max_runtime:
        values.append(f"runtime {draft.max_runtime}")
    return ", ".join(values) or "defaults"


def _pair(argv: list[str], option: str, value: object) -> None:
    text = str(value or "").strip()
    if text:
        argv.extend([option, text])


def _repeat(argv: list[str], option: str, values: Iterable[str]) -> None:
    for value in values:
        _pair(argv, option, value)


def _optional_int(argv: list[str], option: str, value: int | None, *, allow_zero: bool = False) -> None:
    if value is None:
        return
    if value > 0 or (allow_zero and value == 0):
        _pair(argv, option, str(value))


def _tri_flag(argv: list[str], value: bool | None, enabled: str, disabled: str) -> None:
    if value is True:
        argv.append(enabled)
    elif value is False:
        argv.append(disabled)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
