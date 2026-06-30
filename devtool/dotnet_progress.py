from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text

_DOTNET_COMMANDS = {"restore", "build", "test", "publish", "pack"}
_PROJECT_EXTENSIONS = {".csproj", ".fsproj", ".vbproj"}
_SOLUTION_PROJECT_RE = re.compile(r'Project\("[^"\r\n]+"\)\s*=\s*"[^"]+"\s*,\s*"([^"]+\.(?:csproj|fsproj|vbproj))"', re.IGNORECASE)
_RESTORED_RE = re.compile(r"\bRestored\s+(.+?\.(?:csproj|fsproj|vbproj))\b", re.IGNORECASE)
_RESTORE_UPTODATE_RE = re.compile(r"\b(?:All projects are up-to-date for restore|Skipping project .* because it was not found)\b", re.IGNORECASE)
_BUILD_OUTPUT_RE = re.compile(r"^\s*(.+?)(?:\s+\(.*?\))?\s+->\s+(.+)$")
_TEST_RESULT_RE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_TEST_RUN_FOR_RE = re.compile(r"\bTest run for\s+(.+?)(?:\s+\(|$)", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|failed|fail)\b", re.IGNORECASE)
_WARNING_RE = re.compile(r"\bwarning\b", re.IGNORECASE)


@dataclass(slots=True)
class DotnetProgressPlan:
    command: str
    target: str
    total: int | None = None


@dataclass(slots=True)
class DotnetProgressTracker:
    argv: list[str]
    cwd: str
    command_name: str
    console: Console
    plan: DotnetProgressPlan | None = field(init=False, default=None)
    progress: Progress | None = field(init=False, default=None)
    live: Live | None = field(init=False, default=None)
    task_id: TaskID | None = field(init=False, default=None)
    seen_units: set[str] = field(init=False, default_factory=set)
    errors: int = field(init=False, default=0)
    warnings: int = field(init=False, default=0)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="")
    completed: int | None = field(init=False, default=None)
    total: int | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.plan = dotnet_progress_plan(self.argv, self.cwd)
        self.total = self.plan.total if self.plan else None
        self.enabled = bool(self.plan and self.console.is_interactive)

    def __enter__(self) -> "DotnetProgressTracker":
        if not self.enabled or self.plan is None:
            return self
        bar_width = max(10, min(34, self.console.width - 42))
        columns: list[Any] = [
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=bar_width),
            MofNCompleteColumn(),
        ]
        if self.console.width >= 72:
            columns.append(TimeElapsedColumn())
        self.progress = Progress(*columns, console=self.console, expand=True)
        description = f"dotnet {self.plan.command}"
        target = _display_path(self.plan.target, self.cwd, self._detail_limit())
        self.detail = f"target: {target}"
        self.task_id = self.progress.add_task(description, total=self.plan.total)
        self.live = Live(self._render_group(), console=self.console, transient=True, refresh_per_second=8)
        self.live.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self.live is not None:
            self.live.stop()

    def update_from_line(self, line: str) -> None:
        if self.plan is None or (not self.enabled and self.update_callback is None):
            return
        text = line.strip()
        if not text:
            return

        if _WARNING_RE.search(text):
            self.warnings += 1
        if _ERROR_RE.search(text):
            self.errors += 1

        detail = self._detail_for_line(text)
        unit = self._unit_for_line(text)
        if unit:
            self.seen_units.add(unit)
            completed = len(self.seen_units)
            total = self.plan.total
            if total is not None:
                completed = min(completed, total)
            action = self._unit_action()
            self._set_progress(completed=completed, total=self.plan.total, detail=detail or f"{action}: {unit}")
            return

        test_match = _TEST_RESULT_RE.search(text)
        if test_match:
            passed = int(test_match.group("passed"))
            failed = int(test_match.group("failed"))
            skipped = int(test_match.group("skipped"))
            detail = f"tests: {passed} passed, {failed} failed, {skipped} skipped"
            completed = self.plan.total or len(self.seen_units) or 1
            self._set_progress(completed=completed, total=self.plan.total, detail=detail)
            return

        if _RESTORE_UPTODATE_RE.search(text) and self.plan.total:
            self._set_progress(completed=self.plan.total, total=self.plan.total, detail="all projects up-to-date")
            return

        if detail:
            self._set_progress(detail=detail)

    def finish(self, returncode: int) -> None:
        if self.plan is None or (not self.enabled and self.update_callback is None):
            return
        if returncode == 0 and self.plan.total is not None:
            self._set_progress(completed=self.plan.total, total=self.plan.total)
        status = "ok" if returncode == 0 else f"failed rc={returncode}"
        if self.errors or self.warnings:
            status += f" • {self.errors} errors, {self.warnings} warnings"
        self._set_progress(detail=status, force_callback=True)

    def snapshot(self) -> dict[str, Any]:
        percent = None
        if self.completed is not None and self.total:
            percent = max(0.0, min(100.0, self.completed / self.total * 100.0))
        return {
            "command": self.plan.command if self.plan else "",
            "target": _display_path(self.plan.target, self.cwd, self._detail_limit()) if self.plan else "",
            "detail": self.detail,
            "completed": self.completed,
            "total": self.total,
            "percent": percent,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def _set_progress(self, *, completed: int | None = None, total: int | None = None, detail: str | None = None, force_callback: bool = False) -> None:
        if completed is not None:
            self.completed = completed
        if total is not None:
            self.total = total
        if detail is not None:
            self.detail = _shorten(detail, self._detail_limit())
        if self.progress is not None and self.task_id is not None:
            kwargs: dict[str, Any] = {}
            if completed is not None:
                kwargs["completed"] = completed
            if total is not None:
                kwargs["total"] = total
            if kwargs:
                self.progress.update(self.task_id, **kwargs)
        self._refresh_live()
        if self.update_callback is not None and (force_callback or completed is not None or detail):
            self.update_callback(self.snapshot())

    def _detail_for_line(self, text: str) -> str:
        lowered = text.lower()
        if "determining projects to restore" in lowered:
            return "determining projects"
        if "projects are up-to-date" in lowered:
            return "all projects up-to-date"
        if "restore completed" in lowered:
            return "restore completed"
        if text.startswith("MSBuild version"):
            return "msbuild starting"
        if lowered.startswith("build succeeded"):
            return "build succeeded"
        if lowered.startswith("build failed"):
            return "build failed"
        test_run = _TEST_RUN_FOR_RE.search(text)
        if test_run:
            return f"test: {_display_path(test_run.group(1), self.cwd, self._detail_limit())}"
        if "starting test execution" in lowered:
            return "starting tests"
        if "test files matched" in lowered:
            return _shorten(text, self._detail_limit())
        if _ERROR_RE.search(text) or _WARNING_RE.search(text):
            return _shorten(_pathish_text_to_relative(text, self.cwd), self._detail_limit())
        return ""

    def _unit_for_line(self, text: str) -> str:
        restored = _RESTORED_RE.search(text)
        if restored:
            return _normalize_project_unit(restored.group(1), self.cwd)

        built = _BUILD_OUTPUT_RE.match(text)
        if built:
            left = built.group(1).strip()
            right = built.group(2).strip()
            if not left.lower().startswith(("warning", "error")):
                return _normalize_project_unit(left if left else right, self.cwd)

        if self.plan and self.plan.command == "test" and text.lower().startswith(("passed!", "failed!")):
            return f"test-result:{len(self.seen_units) + 1}"

        return ""

    def _unit_action(self) -> str:
        if self.plan is None:
            return "current"
        return {
            "restore": "restored",
            "build": "built",
            "test": "tested",
            "publish": "published",
            "pack": "packed",
        }.get(self.plan.command, "current")

    def _detail_limit(self) -> int:
        return max(28, self.console.width - 4)

    def _render_group(self) -> Group:
        detail_text = Text(self.detail or "", overflow="fold", no_wrap=False)
        return Group(self.progress or Text(""), detail_text)

    def _refresh_live(self) -> None:
        if self.live is not None:
            self.live.update(self._render_group())


def dotnet_progress_plan(argv: list[str], cwd: str) -> DotnetProgressPlan | None:
    command_index = _find_dotnet_command_index(argv)
    if command_index is None or command_index + 1 >= len(argv):
        return None

    command = argv[command_index + 1]
    if command not in _DOTNET_COMMANDS:
        return None

    command_args = argv[command_index + 2 :]
    target = _first_dotnet_target(command_args) or cwd
    total = _estimate_dotnet_total(command, target, cwd)
    return DotnetProgressPlan(command=command, target=target, total=total)


def _find_dotnet_command_index(argv: list[str]) -> int | None:
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet":
            return index
    return None


def _first_dotnet_target(args: Iterable[str]) -> str:
    skip_next = False
    value_options = {"--framework", "-f", "--runtime", "-r", "--configuration", "-c", "--output", "-o", "--logger", "-l", "--filter", "--settings"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_options:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return ""


def _estimate_dotnet_total(command: str, target_text: str, cwd: str) -> int | None:
    path = Path(target_text)
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        path = path.resolve()
    except OSError:
        pass

    projects: list[Path] = []
    if path.suffix.lower() in _PROJECT_EXTENSIONS:
        projects = [path]
    elif path.suffix.lower() == ".sln" and path.exists():
        projects = _projects_from_solution(path)
    elif path.is_dir():
        projects = sorted(p for p in path.rglob("*proj") if p.suffix.lower() in _PROJECT_EXTENSIONS)

    if command == "test":
        test_projects = [p for p in projects if "test" in p.stem.lower() or any("test" in part.lower() for part in p.parts)]
        if test_projects:
            return len(test_projects)

    if projects:
        return len(projects)
    return None


def _projects_from_solution(solution: Path) -> list[Path]:
    try:
        text = solution.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    projects: list[Path] = []
    for match in _SOLUTION_PROJECT_RE.finditer(text):
        rel = match.group(1).replace("\\", os.sep)
        projects.append((solution.parent / rel).resolve())
    return projects


def _normalize_project_unit(value: str, cwd: str) -> str:
    cleaned = value.strip().strip('"')
    path = Path(cleaned)
    if not path.is_absolute() and (os.sep in cleaned or "/" in cleaned or "\\" in cleaned):
        path = Path(cwd) / cleaned
    suffix = path.suffix.lower()
    if suffix in _PROJECT_EXTENSIONS or suffix in {".dll", ".exe"} or path.is_absolute():
        return _display_path(str(path), cwd, 10_000)
    return cleaned


def _display_path(value: str, cwd: str, limit: int = 70) -> str:
    text = value.strip().strip('"')
    if not text:
        return ""
    path = Path(text)
    looks_pathish = path.is_absolute() or os.sep in text or "/" in text or "\\" in text or bool(path.suffix)
    if looks_pathish:
        if not path.is_absolute():
            path = Path(cwd) / path
        try:
            rel = path.resolve().relative_to(Path(cwd).resolve())
            text = rel.as_posix()
        except (OSError, ValueError):
            text = path.as_posix()
    return _shorten(text, limit)


def _pathish_text_to_relative(text: str, cwd: str) -> str:
    cwd_path = Path(cwd).resolve()
    return text.replace(str(cwd_path) + os.sep, "").replace(str(cwd_path) + "/", "")


def _shorten(value: str, limit: int = 70) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return "…" + text[-(limit - 1) :]
