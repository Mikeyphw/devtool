from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .command_model import DevtoolCommandDraft, parse_environment_assignments, split_extra_arguments


@dataclass(frozen=True, slots=True)
class RunWorkflowOperation:
    id: str
    label: str
    description: str
    requires_artifact: bool = False
    supports_validation: bool = False
    supports_test_selection: bool = False
    supports_commit_message: bool = False


OPERATIONS: tuple[RunWorkflowOperation, ...] = (
    RunWorkflowOperation("validate", "Validate", "Run the repository validation pipeline.", supports_test_selection=True),
    RunWorkflowOperation("restore", "Restore", "Restore dependencies or generated state for the selected target."),
    RunWorkflowOperation("build", "Build", "Build the selected target."),
    RunWorkflowOperation("test", "Test", "Run tests with optional selectors and tags.", supports_test_selection=True),
    RunWorkflowOperation("lint", "Lint", "Run static checks for the selected target."),
    RunWorkflowOperation("run", "Run", "Launch the selected project's run command."),
    RunWorkflowOperation("package", "Package", "Create package artifacts for the selected target."),
    RunWorkflowOperation("clean", "Clean", "Clean build/cache output for the selected target."),
    RunWorkflowOperation("status", "Status", "Print repository and Devtool status."),
    RunWorkflowOperation("doctor", "Doctor", "Run diagnostics for the selected repository."),
    RunWorkflowOperation("pipeline-plan", "Pipeline · plan", "Read-only frozen-source graph preflight."),
    RunWorkflowOperation("pipeline-run", "Pipeline · dispatch", "May send a GitHub workflow_dispatch POST; explicit --yes required."),
    RunWorkflowOperation("pipeline-status", "Pipeline · status", "Read a durable pipeline journal without remote mutation."),
    RunWorkflowOperation("pipeline-resume", "Pipeline · resume", "Advance waiting nodes; explicit --yes required, never auto-redispatch uncertain requests."),
    RunWorkflowOperation(
        "apply-overlay",
        "Apply overlay",
        "Apply a single overlay archive.",
        requires_artifact=True,
        supports_validation=True,
        supports_commit_message=True,
    ),
    RunWorkflowOperation(
        "apply-overlays",
        "Apply overlay bundle",
        "Apply an overlay bundle or multi-overlay archive.",
        requires_artifact=True,
        supports_validation=True,
        supports_commit_message=True,
    ),
)

_OPERATION_BY_ID = {item.id: item for item in OPERATIONS}
_OPERATION_BY_LABEL = {item.label.lower(): item for item in OPERATIONS}


@dataclass(slots=True)
class RunWorkflowDraftPayload:
    repository: str
    target: str = ""
    variant: str = ""
    project_type: str = "auto"
    execution_environment: str = "auto"
    operation: str = "validate"
    artifact: str = ""
    pipeline_file: str = ".devtool/actions-pipeline.json"
    pipeline_id: str = ""
    test_profile: str = ""
    selectors: str = ""
    test_ids: str = ""
    tags: str = ""
    exclude_tags: str = ""
    test_path: str = ""
    test_class: str = ""
    test_method: str = ""
    test_filter: str = ""
    pytest_tests: str = ""
    pytest_markers: str = ""
    pytest_k: str = ""
    pytest_args: str = ""
    pytest_include_defaults: bool = False
    test_tasks: str = ""
    list_tests: bool = False
    failed: bool = False
    changed: bool = False
    changed_from: str = "HEAD"
    instrumented: bool = False
    validate: bool = False
    commit: bool = False
    commit_message: str = ""
    keep_artifact: bool = False
    no_manifest_check: bool = False
    copy_mode: str = "summary"
    auto_hud: bool = True
    hud_mode: str = "control-center"
    yes: bool = True
    environment: str = ""
    extra_global_args: str = ""
    extra_operation_args: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunWorkflowReview:
    argv: list[str]
    command: str
    environment: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return bool(self.argv) and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "command": self.command,
            "environment": self.environment,
            "warnings": self.warnings,
            "errors": self.errors,
            "runnable": self.runnable,
        }


@dataclass(slots=True)
class RunWorkflowSnapshot:
    repository: str
    project_type: str = "auto"
    execution_environment: str = "auto"
    selected_target: str = ""
    variant: str = ""
    targets: list[str] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    copy_modes: list[str] = field(default_factory=lambda: ["summary", "json", "none"])
    hud_modes: list[str] = field(default_factory=lambda: ["control-center", "desktop-window", "terminal", "web", "studio", "none"])
    default_draft: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_operation(value: str) -> str:
    text = (value or "").strip().lower().replace("_", "-")
    if text in _OPERATION_BY_ID:
        return text
    if text in _OPERATION_BY_LABEL:
        return _OPERATION_BY_LABEL[text].id
    if text in {"apply overlay", "ao"}:
        return "apply-overlay"
    if text in {"apply bundle", "apply overlay bundle", "aos"}:
        return "apply-overlays"
    if text in {"run project"}:
        return "run"
    return text or "validate"


def operation_spec(value: str) -> RunWorkflowOperation | None:
    return _OPERATION_BY_ID.get(normalize_operation(value))


def payload_from_mapping(data: Mapping[str, Any]) -> RunWorkflowDraftPayload:
    def text(key: str, default: str = "") -> str:
        value = data.get(key, default)
        return "" if value is None else str(value)

    def flag(key: str, default: bool = False) -> bool:
        value = data.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    return RunWorkflowDraftPayload(
        repository=text("repository", "."),
        target=text("target"),
        variant=text("variant"),
        project_type=text("project_type", "auto"),
        execution_environment=text("execution_environment", "auto"),
        operation=normalize_operation(text("operation", "validate")),
        artifact=text("artifact"),
        pipeline_file=text("pipeline_file", ".devtool/actions-pipeline.json"),
        pipeline_id=text("pipeline_id"),
        test_profile=text("test_profile"),
        selectors=text("selectors"),
        test_ids=text("test_ids"),
        tags=text("tags"),
        exclude_tags=text("exclude_tags"),
        test_path=text("test_path"),
        test_class=text("test_class"),
        test_method=text("test_method"),
        test_filter=text("test_filter"),
        pytest_tests=text("pytest_tests"),
        pytest_markers=text("pytest_markers"),
        pytest_k=text("pytest_k"),
        pytest_args=text("pytest_args"),
        pytest_include_defaults=flag("pytest_include_defaults"),
        test_tasks=text("test_tasks"),
        list_tests=flag("list_tests"),
        failed=flag("failed"),
        changed=flag("changed"),
        changed_from=text("changed_from", "HEAD"),
        instrumented=flag("instrumented"),
        validate=flag("validate"),
        commit=flag("commit"),
        commit_message=text("commit_message"),
        keep_artifact=flag("keep_artifact"),
        no_manifest_check=flag("no_manifest_check"),
        copy_mode=text("copy_mode", "summary"),
        auto_hud=flag("auto_hud", True),
        hud_mode=text("hud_mode", "control-center"),
        yes=flag("yes", True),
        environment=text("environment"),
        extra_global_args=text("extra_global_args"),
        extra_operation_args=text("extra_operation_args"),
    )


def draft_from_payload(payload: RunWorkflowDraftPayload, *, devtool: str = "devtool") -> DevtoolCommandDraft:
    return DevtoolCommandDraft(
        repository=payload.repository,
        operation=normalize_operation(payload.operation),
        devtool=devtool,
        target=payload.target,
        variant=payload.variant,
        project_type=payload.project_type,
        execution_environment=payload.execution_environment,
        artifact=payload.artifact,
        pipeline_file=payload.pipeline_file,
        pipeline_id=payload.pipeline_id,
        test_profile=payload.test_profile,
        selectors=split_extra_arguments(payload.selectors),
        test_ids=split_extra_arguments(payload.test_ids),
        tags=split_extra_arguments(payload.tags),
        exclude_tags=split_extra_arguments(payload.exclude_tags),
        test_path=payload.test_path,
        test_class=payload.test_class,
        test_method=payload.test_method,
        test_filter=payload.test_filter,
        pytest_tests=split_extra_arguments(payload.pytest_tests),
        pytest_markers=split_extra_arguments(payload.pytest_markers),
        pytest_k=payload.pytest_k,
        pytest_args=split_extra_arguments(payload.pytest_args),
        pytest_include_defaults=payload.pytest_include_defaults,
        test_tasks=split_extra_arguments(payload.test_tasks),
        list_tests=payload.list_tests,
        failed=payload.failed,
        changed=payload.changed,
        changed_from=payload.changed_from,
        instrumented=payload.instrumented,
        validate=payload.validate,
        commit=payload.commit,
        commit_message=payload.commit_message,
        keep_artifact=payload.keep_artifact,
        no_manifest_check=payload.no_manifest_check,
        copy_mode=payload.copy_mode,
        auto_hud=payload.auto_hud,
        hud_mode=payload.hud_mode,
        yes=payload.yes,
        environment=parse_environment_assignments(payload.environment),
        extra_global_args=split_extra_arguments(payload.extra_global_args),
        extra_operation_args=split_extra_arguments(payload.extra_operation_args),
    )


def review_payload(payload: RunWorkflowDraftPayload, *, devtool: str = "devtool") -> RunWorkflowReview:
    draft = draft_from_payload(payload, devtool=devtool)
    argv = draft.argv()
    command = draft.shell_command() if argv else ""
    warnings: list[str] = []
    errors: list[str] = []
    spec = operation_spec(payload.operation)
    if spec is None:
        errors.append(f"Unknown Devtool operation: {payload.operation}")
    elif spec.requires_artifact and not payload.artifact.strip():
        errors.append(f"{spec.label} requires an overlay or bundle path.")
    if payload.operation in ('pipeline-run', 'pipeline-resume') and not payload.yes:
        errors.append('Pipeline mutation requires explicit --yes; enable Non-interactive --yes after reviewing the command.')
    if payload.operation in ('pipeline-plan', 'pipeline-run') and not draft.pipeline_file.strip():
        errors.append('Pipeline manifest path is required.')
    if payload.operation in ('pipeline-status', 'pipeline-resume') and not draft.pipeline_id.strip():
        errors.append('Exact 32-character pipeline ID is required.')
    if payload.operation.startswith('pipeline-') and not argv:
        errors.append('Invalid pipeline path/ID or missing explicit mutation authorization.')
    if payload.commit and not payload.commit_message.strip():
        warnings.append("Commit is enabled but the commit message is empty; --commit will be omitted.")
    if payload.hud_mode.strip().lower() == "none" and payload.auto_hud:
        warnings.append("HUD mode is none, so the generated command disables HUD publishing.")
    if payload.copy_mode.strip().lower() not in {"summary", "json", "none"}:
        warnings.append("Unknown copy mode; the command falls back to summary clipboard output.")
    return RunWorkflowReview(argv=argv, command=command, environment=draft.environment, warnings=warnings, errors=errors)


def default_payload_from_snapshot(snapshot: Mapping[str, Any]) -> RunWorkflowDraftPayload:
    repo = snapshot.get("repository", {}) if isinstance(snapshot.get("repository"), Mapping) else {}
    project = snapshot.get("project", {}) if isinstance(snapshot.get("project"), Mapping) else {}
    return RunWorkflowDraftPayload(
        repository=str(repo.get("path") or "."),
        target=str(project.get("selected_target") or ""),
        variant=str(project.get("variant") or ""),
        project_type=str(project.get("kind") or "auto"),
        execution_environment=str(project.get("execution_environment") or "auto"),
    )


def workflow_snapshot(control_center_snapshot: Mapping[str, Any], *, devtool: str = "devtool") -> RunWorkflowSnapshot:
    payload = default_payload_from_snapshot(control_center_snapshot)
    target_names: list[str] = []
    targets = control_center_snapshot.get("targets", [])
    if isinstance(targets, list):
        for item in targets:
            if isinstance(item, Mapping) and str(item.get("name") or ""):
                target_names.append(str(item.get("name")))
    if payload.target and payload.target not in target_names:
        target_names.insert(0, payload.target)
    review = review_payload(payload, devtool=devtool)
    return RunWorkflowSnapshot(
        repository=payload.repository,
        project_type=payload.project_type,
        execution_environment=payload.execution_environment,
        selected_target=payload.target,
        variant=payload.variant,
        targets=target_names,
        operations=[asdict(item) for item in OPERATIONS],
        default_draft=payload.as_dict(),
        review=review.as_dict(),
    )


def workflow_snapshot_json(control_center_snapshot: Mapping[str, Any], *, devtool: str = "devtool") -> str:
    return json.dumps(workflow_snapshot(control_center_snapshot, devtool=devtool).as_dict(), ensure_ascii=False, indent=2)
