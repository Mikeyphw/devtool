from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass(slots=True)
class DotnetTarget:
    name: str
    solution: str = ""
    restore_target: str = ""
    build_target: str = ""
    test_target: str = ""
    run_project: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def effective_restore_target(self) -> str:
        return self.restore_target or self.solution or self.build_target

    def effective_build_target(self) -> str:
        return self.build_target or self.solution or self.restore_target

    def effective_test_target(self) -> str:
        return self.test_target or self.solution or self.effective_build_target()

    def effective_run_project(self) -> str:
        return self.run_project or self.effective_build_target()


@dataclass(slots=True)
class ProjectConfig:
    repo_root: Path
    kind: str
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
    default_target: str = ""
    selected_target: str = ""
    targets: dict[str, DotnetTarget] = field(default_factory=dict)

    def resolve_repo_relative(self, value: str) -> str:
        if not value:
            return value
        path = Path(value)
        if not path.is_absolute():
            return str((self.repo_root / path).resolve())
        return str(path)
