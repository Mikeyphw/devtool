from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .repository_identity import RepositoryIdentity


@dataclass(slots=True)
class Issue:
    severity: str
    command: str
    file: str
    code: str
    message: str
    line: int | None = None


@dataclass(slots=True)
class CommandSpec:
    name: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    destructive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OperationResult:
    name: str
    argv: list[str]
    returncode: int
    duration_seconds: float
    output: str
    issues: list[Issue]
    skipped: bool = False
    log_path: str = ""
    failure_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def execution_status(self):
        """Canonical status while retaining the legacy OperationResult shape."""
        return self.to_execution_result().status

    @property
    def run_id(self) -> str:
        return self.to_execution_result().run_id

    @property
    def node_id(self) -> str:
        return self.to_execution_result().node_id

    def to_execution_result(self):
        from .core.execution_contract import ExecutionResult
        return ExecutionResult.from_operation(self)

    def to_command_result(self):
        """Return the architecture command result without changing legacy fields."""
        from .core.results import CommandResult
        return CommandResult.from_execution_result(self.to_execution_result())


@dataclass(slots=True, frozen=True)
class WorkflowStepRef:
    """Normalized reference to one step in a configured target workflow.

    A plain string step keeps the MP02 ordered-list contract. MP03 also accepts
    inline step tables with stable ``id`` and ``depends_on`` fields so the same
    configuration can express branches and joins without shell orchestration.

    ``resolution_*`` fields preserve how a human-authored reference resolved
    without forcing every downstream surface to reverse-engineer it from the
    executable internal name. This is especially important for target-local
    jobs, where ``job:contracts`` resolves to an internal key such as
    ``dustui_android.contracts``.
    """

    raw: str
    kind: str
    name: str
    action: str = ""
    step_id: str = ""
    depends_on: tuple[str, ...] = ()
    depends_on_declared: bool = False
    resolution_scope: str = ""
    resolution_target: str = ""
    resolution_local_name: str = ""
    resolution_key: str = ""


@dataclass(slots=True)
class WorkflowConfig:
    name: str
    steps: tuple[WorkflowStepRef, ...] = ()


@dataclass(slots=True)
class JobConfig:
    """Reusable, runner-owned work declared under ``[jobs.*]``."""

    name: str
    runner: str
    root: str = ""
    action: str = ""
    command: tuple[str, ...] = ()
    shell_command: str = ""
    env: dict[str, str] = field(default_factory=dict)
    execution_environment: str = "auto"
    chroot_name: str = ""
    device_serial: str = ""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    declared_fields: frozenset[str] = field(default_factory=frozenset)

    @property
    def command_mode(self) -> str:
        if self.command:
            return "argv"
        if self.shell_command:
            return "shell"
        return "runner-action"


@dataclass(slots=True, frozen=True)
class TargetGroup:
    name: str
    members: tuple[str, ...] = ()


@dataclass(slots=True)
class WorkspaceConfig:
    default_target: str = ""
    groups: dict[str, TargetGroup] = field(default_factory=dict)
    scheduler: "WorkflowSchedulerConfig" = field(default_factory=lambda: WorkflowSchedulerConfig())


@dataclass(slots=True)
class WorkflowSchedulerConfig:
    """Repository-level workflow scheduling policy.

    Safe sequential execution remains the default. Parallel scheduling is an
    opt-in and is still constrained by dependency, read/write-scope, and
    resource-class conflicts calculated by MP09.
    """

    parallel: bool = False
    max_parallel_nodes: int = 0
    max_gradle_nodes: int = 1
    max_dotnet_nodes: int = 1


@dataclass(slots=True)
class TargetConfig:
    """Language-neutral target identity.

    ``runner`` selects the command planner. ``platform`` describes the product
    surface (server/web/android/tv/desktop/shared/etc.) and is intentionally
    independent from the runner. ``kind`` remains a compatibility alias for
    repositories written before MP01.
    """

    name: str
    runner: str = ""
    platform: str = ""
    kind: str = ""
    root: str = ""
    solution: str = ""
    restore_target: str = ""
    build_target: str = ""
    test_target: str = ""
    run_project: str = ""
    cwd: str = ""
    restore_command: str = ""
    build_command: str = ""
    test_command: str = ""
    package_command: str = ""
    run_command: str = ""
    env: dict[str, str] = field(default_factory=dict)
    execution_environment: str = "auto"
    chroot_name: str = ""
    device_serial: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: dict[str, WorkflowConfig] = field(default_factory=dict)
    jobs: dict[str, JobConfig] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    # Fields explicitly present in the target TOML table.  This preserves the
    # semantic difference between an absent setting and an explicit empty
    # value used to clear a lower-precedence value.
    declared_fields: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        runner = str(self.runner or self.kind or "").strip().lower()
        legacy_kind = str(self.kind or runner).strip().lower()
        self.runner = runner
        self.kind = legacy_kind
        self.platform = str(self.platform or "").strip().lower()

    @property
    def effective_runner(self) -> str:
        return self.runner or self.kind

    def effective_restore_target(self) -> str:
        return self.restore_target or self.solution or self.build_target

    def effective_build_target(self) -> str:
        return self.build_target or self.solution or self.restore_target

    def effective_test_target(self) -> str:
        return self.test_target or self.solution or self.effective_build_target()

    def effective_run_project(self) -> str:
        return self.run_project or self.effective_build_target()


# Historical public name retained for callers and plugins written before MP01.
DotnetTarget = TargetConfig


@dataclass(slots=True)
class WrapperOptionConfig:
    name: str
    type: str = "string"
    description: str = ""
    aliases: tuple[str, ...] = ()
    required: bool = False
    repeatable: bool = False
    choices: tuple[str, ...] = ()
    default: Any = None
    has_default: bool = False
    conflicts: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    relative_to: str = ""
    global_argv: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()
    passthrough_argv: tuple[str, ...] = ()


@dataclass(slots=True)
class WrapperPositionalConfig:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    repeatable: bool = False
    choices: tuple[str, ...] = ()
    relative_to: str = ""
    global_argv: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()
    passthrough_argv: tuple[str, ...] = ()


@dataclass(slots=True)
class WrapperCommandConfig:
    name: str
    description: str = ""
    devtool: str = ""
    workflow: str = ""
    aliases: tuple[str, ...] = ()
    target: str = ""
    group: str = ""
    all_targets: bool = False
    allow_passthrough: bool = False
    options: dict[str, WrapperOptionConfig] = field(default_factory=dict)
    positionals: tuple[WrapperPositionalConfig, ...] = ()


@dataclass(slots=True)
class WrapperConfig:
    schema: int = 1
    default_command: str = ""
    commands: dict[str, WrapperCommandConfig] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class FederatedRepositoryConfig:
    name: str
    declared_path: str
    root: str
    required: bool = True
    available: bool = False


@dataclass(slots=True, frozen=True)
class FederationGroup:
    name: str
    repositories: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class FederatedWorkflowStep:
    step_id: str
    ref: str
    repository: str
    workflow: str
    depends_on: tuple[str, ...] = ()
    depends_on_declared: bool = False


@dataclass(slots=True, frozen=True)
class FederatedWorkflowConfig:
    name: str
    steps: tuple[FederatedWorkflowStep, ...] = ()


@dataclass(slots=True)
class FederationConfig:
    schema: int = 1
    repositories: dict[str, FederatedRepositoryConfig] = field(default_factory=dict)
    groups: dict[str, FederationGroup] = field(default_factory=dict)
    workflows: dict[str, FederatedWorkflowConfig] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.repositories or self.groups or self.workflows)


@dataclass(slots=True)
class ProjectConfig:
    repo_root: Path
    kind: str
    # MP01 canonical identity. ``kind`` remains the effective-runner
    # compatibility field so existing runner code does not need a flag day.
    runner: str = ""
    platform: str = ""
    project_family: str = ""
    build_system: str = ""
    project_subtype: str = ""
    build_target: str = ""
    run_target: str = ""
    restore_target: str = ""
    test_target: str = ""
    python_executable: str = "python3"
    run_module: str = ""
    restore_command: str = ""
    build_command: str = ""
    test_command: str = ""
    package_command: str = ""
    run_command: str = ""
    env: dict[str, str] = field(default_factory=dict)
    config_path: Path | None = None
    repository_identity: RepositoryIdentity | None = None
    selected_target: str = ""
    variant: str = ""
    targets: dict[str, TargetConfig] = field(default_factory=dict)
    jobs: dict[str, JobConfig] = field(default_factory=dict)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    wrapper: WrapperConfig = field(default_factory=WrapperConfig)
    federation: FederationConfig = field(default_factory=FederationConfig)
    ui_config: dict[str, Any] = field(default_factory=dict)
    execution_environment: str = "auto"
    chroot_name: str = "androidos"
    device_serial: str = ""
    project_root: str = ""
    toolchain_manager: str = "auto"
    runner_options: dict[str, Any] = field(default_factory=dict)
    global_config_path: Path | None = None
    config_provenance: dict[str, str] = field(default_factory=dict)
    configured_runner_options: dict[str, Any] = field(default_factory=dict)
    runtime_runner_overrides: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        effective_runner = str(self.runner or self.kind or "unknown").strip().lower() or "unknown"
        self.runner = effective_runner
        self.kind = effective_runner
        self.platform = str(self.platform or "").strip().lower()

    def resolve_repo_relative(self, value: str) -> str:
        if not value:
            return value
        path = Path(value)
        if not path.is_absolute():
            return str((self.repo_root / path).resolve())
        return str(path)
