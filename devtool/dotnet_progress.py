from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from rich.console import Console, Group
from rich.live import Live
from rich.errors import LiveError
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
_RESTORED_RE = re.compile(r"\bRestored\s+(.+?\.(?:csproj|fsproj|vbproj))\b(?:\s+\((?P<duration>.*?)\))?", re.IGNORECASE)
_RESTORE_UPTODATE_RE = re.compile(r"\b(?:Skipping project|Project)\s+(.+?\.(?:csproj|fsproj|vbproj))\b.*?up-to-date", re.IGNORECASE)
_RESTORE_ASSETS_RE = re.compile(r"\bAssets file has not changed\. Skipping assets file writing\. Path:\s+(.+?obj[\\/](?:project\.assets\.json|packages\.lock\.json))\b", re.IGNORECASE)
_BUILD_OUTPUT_RE = re.compile(r"^\s*(?P<project>.+?)(?:\s+\(.*?\))?\s+->\s+(?P<output>.+)$")
_TEST_RUN_FOR_RE = re.compile(r"\bTest run for\s+(.+?\.(?:dll|exe))\b", re.IGNORECASE)
_TEST_ASSEMBLY_RE = re.compile(r"\b(?:Starting test execution|A total of \d+ test files matched).*?", re.IGNORECASE)
_TEST_RESULT_RE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_MSBUILD_SUMMARY_RE = re.compile(r"\bBuild\s+(?P<status>succeeded|FAILED)\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|failed|fail)\b", re.IGNORECASE)
_WARNING_RE = re.compile(r"\bwarning\b", re.IGNORECASE)


@dataclass(slots=True)
class DotnetProgressPlan:
    command: str
    target: str
    total: int | None = None
    label: str = "items"


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
    detail_text: Text = field(init=False, default_factory=Text)
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
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
            transient=True,
        )
        description = f"dotnet {self.plan.command}"
        self.detail_text = Text(self._format_initial_detail(), overflow="fold")
        self.task_id = self.progress.add_task(description, total=self.plan.total)
        self.live = Live(
            Group(self.progress, self.detail_text),
            console=self.console,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="visible",
        )
        try:
            # Do not call ``Progress.start()`` here. ``Progress.start()`` owns
            # its own Rich Live display, and this tracker already renders the
            # Progress inside a parent Live so the detail line can stay below
            # the one-line progress bar.  Starting both creates nested Live
            # displays and crashes on Termux with:
            # ``rich.errors.LiveError: Only one live display may be active``.
            self.live.start()
        except LiveError:
            # Another Rich status/progress display is already active.  Keep
            # parsing dotnet output for Tasker/final snapshots, but gracefully
            # disable the terminal render instead of crashing the devtool run.
            self.live = None
            self.progress = None
            self.task_id = None
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

        event = self._event_for_line(text)
        if event:
            unit = str(event.get("unit") or "")
            detail = str(event.get("detail") or "")
            total = event.get("total")
            if unit:
                self.seen_units.add(unit)
                completed = len(self.seen_units)
                current_total = self.total if self.total is not None else self.plan.total
                if current_total is not None and completed > current_total:
                    current_total = completed
                self._set_progress(completed=completed, total=current_total, detail=detail)
            else:
                self._set_progress(total=total if isinstance(total, int) else None, detail=detail)
            return

        if "All projects are up-to-date" in text and self.plan.total:
            self._set_progress(completed=self.plan.total, total=self.plan.total, detail="all projects up-to-date")
            return

        detail = self._detail_for_line(text)
        if detail:
            self._set_progress(detail=detail)

    def finish(self, returncode: int) -> None:
        if self.plan is None or (not self.enabled and self.update_callback is None):
            return
        if returncode == 0:
            if self.total is not None:
                completed = max(self.completed or 0, self.total)
                self._set_progress(completed=completed, total=self.total)
            elif self.completed is not None:
                self._set_progress(total=max(1, self.completed))
        status = "ok" if returncode == 0 else f"failed rc={returncode}"
        if self.errors or self.warnings:
            status += f" • {self.errors} errors, {self.warnings} warnings"
        final_detail = status
        if self.detail and self.detail != status:
            final_detail = f"{status} • {self.detail}"
        self._set_progress(detail=final_detail, force_callback=True)

    def snapshot(self) -> dict[str, Any]:
        percent = None
        if self.completed is not None and self.total:
            percent = max(0.0, min(100.0, self.completed / self.total * 100.0))
        return {
            "command": self.plan.command if self.plan else "",
            "target": self.plan.target if self.plan else "",
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
            self.detail = detail
        if self.progress is not None and self.task_id is not None:
            kwargs: dict[str, Any] = {}
            if completed is not None:
                kwargs["completed"] = completed
            if total is not None:
                kwargs["total"] = total
            if kwargs:
                self.progress.update(self.task_id, **kwargs)
        if self.live is not None:
            self.detail_text = Text(_wrap_detail(self.detail or self._format_initial_detail(), self.console.width), overflow="fold")
            if self.progress is not None:
                self.live.update(Group(self.progress, self.detail_text))
        if self.update_callback is not None and (force_callback or completed is not None or detail):
            self.update_callback(self.snapshot())

    def _format_initial_detail(self) -> str:
        if self.plan is None:
            return ""
        target = _repo_relative(self.plan.target, self.cwd)
        total = f" • expected {self.plan.total} {self.plan.label}" if self.plan.total else ""
        return f"target: {target}{total}"

    def _event_for_line(self, text: str) -> dict[str, Any] | None:
        assert self.plan is not None
        command = self.plan.command

        restored = _RESTORED_RE.search(text)
        if restored:
            project = _repo_relative(restored.group(1), self.cwd)
            duration = restored.groupdict().get("duration") or ""
            detail = f"restored {project}"
            if duration:
                detail += f" ({duration})"
            return {"unit": f"restore:{project}", "detail": detail}

        restore_uptodate = _RESTORE_UPTODATE_RE.search(text)
        if restore_uptodate:
            project = _repo_relative(restore_uptodate.group(1), self.cwd)
            return {"unit": f"restore:{project}", "detail": f"up-to-date {project}"}

        assets = _RESTORE_ASSETS_RE.search(text)
        if assets and command == "restore":
            path = _repo_relative(assets.group(1), self.cwd)
            project = _project_from_assets_path(path)
            return {"unit": f"restore:{project or path}", "detail": f"up-to-date {project or path}"}

        built = _BUILD_OUTPUT_RE.match(text)
        if built and not text.lower().startswith(("warning", "error")):
            project = built.group("project").strip()
            output = built.group("output").strip()
            unit = _normalize_project_unit(project or output, self.cwd)
            rel_output = _repo_relative(output, self.cwd)
            detail = f"built {unit} -> {rel_output}"
            return {"unit": f"build:{unit}", "detail": detail}

        test_run = _TEST_RUN_FOR_RE.search(text)
        if test_run:
            assembly = _repo_relative(test_run.group(1), self.cwd)
            unit = _test_unit_from_assembly(assembly)
            return {"unit": f"test:{unit}", "detail": f"testing {assembly}"}

        test_match = _TEST_RESULT_RE.search(text)
        if test_match:
            passed = int(test_match.group("passed"))
            failed = int(test_match.group("failed"))
            skipped = int(test_match.group("skipped"))
            detail = f"tests: {passed} passed, {failed} failed, {skipped} skipped"
            # If we have not seen any test assembly, still advance a single logical unit.
            unit = "test:summary" if not self.seen_units else ""
            return {"unit": unit, "detail": detail}

        summary = _MSBUILD_SUMMARY_RE.search(text)
        if summary:
            return {"detail": f"build {summary.group('status').lower()}"}

        return None

    def _detail_for_line(self, text: str) -> str:
        lowered = text.lower()
        if "determining projects to restore" in lowered:
            return "restore: determining projects"
        if "projects are up-to-date" in lowered:
            return "restore: all projects up-to-date"
        if "restore completed" in lowered:
            return "restore completed"
        if text.startswith("MSBuild version"):
            return "build: MSBuild starting"
        if "starting test execution" in lowered:
            return "test: starting execution"
        if "a total of" in lowered and "test files matched" in lowered:
            return _shorten(text, self.console.width)
        if _ERROR_RE.search(text) or _WARNING_RE.search(text):
            return _shorten(_repo_relative_paths_in_text(text, self.cwd), self.console.width)
        return ""


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
    label = "tests" if command == "test" else "projects"
    return DotnetProgressPlan(command=command, target=target, total=total, label=label)


def _find_dotnet_command_index(argv: list[str]) -> int | None:
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet":
            return index
    return None


def _first_dotnet_target(args: Iterable[str]) -> str:
    skip_next = False
    value_options = {"--framework", "-f", "--runtime", "-r", "--configuration", "-c", "--output", "-o", "--logger", "-l", "--verbosity", "-v"}
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
    if cleaned.endswith((".dll", ".exe")):
        return Path(cleaned).stem
    path = Path(cleaned)
    if not path.is_absolute():
        path = Path(cwd) / path
    suffix = path.suffix.lower()
    if suffix in _PROJECT_EXTENSIONS:
        return path.stem
    return cleaned


def _test_unit_from_assembly(value: str) -> str:
    return Path(value).stem or value


def _project_from_assets_path(value: str) -> str:
    path = Path(value)
    parts = path.parts
    if "obj" in parts:
        index = parts.index("obj")
        if index > 0:
            return parts[index - 1]
    return ""


def _repo_relative(value: str, cwd: str) -> str:
    cleaned = value.strip().strip('"')
    if not cleaned:
        return cleaned
    # Remove common MSBuild trailing decoration.
    cleaned = re.sub(r"\s+\(in\s+[^)]*\)\.?$", "", cleaned)
    path = Path(cleaned)
    root = Path(cwd).resolve()
    if not path.is_absolute():
        path = root / path
    try:
        rel = path.resolve().relative_to(root)
        return str(rel).replace(os.sep, "/")
    except (OSError, ValueError):
        return cleaned.replace("\\", "/")


def _repo_relative_paths_in_text(text: str, cwd: str) -> str:
    root = str(Path(cwd).resolve())
    if root and root in text:
        return text.replace(root.rstrip("/") + "/", "")
    return text


def _wrap_detail(value: str, width: int) -> str:
    # Keep the progress bar line clean; details render underneath and should not
    # overflow narrow Termux terminals.  Leave actual wrapping to Rich, but cap
    # pathological MSBuild lines so they do not dominate the screen.
    return _shorten(value, max(40, min(110, width)))


def _shorten(value: str, limit: int = 70) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1) :]
