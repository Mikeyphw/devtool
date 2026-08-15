from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from rich.console import Console
from rich.live import Live
from rich.errors import LiveError
from rich.panel import Panel
from rich.text import Text

from .rich_progress import (
    active_panel_title,
    ThreeLineProgressBody,
    ThreeLineProgressState,
    compact_display_target,
    completion_line,
)

_DOTNET_COMMANDS = {"restore", "build", "test", "publish", "pack"}
_PROJECT_EXTENSIONS = {".csproj", ".fsproj", ".vbproj"}
_SOLUTION_PROJECT_RE = re.compile(r'Project\("[^"\r\n]+"\)\s*=\s*"[^"]+"\s*,\s*"([^"]+\.(?:csproj|fsproj|vbproj))"', re.IGNORECASE)
_PROJECT_REFERENCE_RE = re.compile(r'<ProjectReference\b[^>]*\bInclude=["\']([^"\']+\.(?:csproj|fsproj|vbproj))["\']', re.IGNORECASE)
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
_MSBUILD_WARNING_SUMMARY_RE = re.compile(r"^\s*(?P<count>\d+)\s+Warning\(s\)\b", re.IGNORECASE)
_MSBUILD_ERROR_SUMMARY_RE = re.compile(r"^\s*(?P<count>\d+)\s+Error\(s\)\b", re.IGNORECASE)
# Match actual diagnostic lines, not summary text such as ``0 Error(s)`` or
# vstest text such as ``Failed: 0``.  The progress detail is only a live hint;
# final pass/fail still comes from the process exit code and parsed issues.
_DIAGNOSTIC_WARNING_RE = re.compile(r"(?:^|[:\s])warning\s+[A-Z]{1,8}\d{3,}\b|\bNU\d{4}\b.*\bwarning\b", re.IGNORECASE)
_DIAGNOSTIC_ERROR_RE = re.compile(r"(?:^|[:\s])error\s+[A-Z]{1,8}\d{3,}\b|\bNU\d{4}\b.*\berror\b", re.IGNORECASE)
_WARNING_RE = re.compile(r"\bwarning\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"\berror\b|\bfailed\b", re.IGNORECASE)


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
    phase_index: int | None = None
    phase_total: int | None = None
    display_target: str = ""
    target_phase_index: int | None = None
    target_phase_total: int | None = None
    plan: DotnetProgressPlan | None = field(init=False, default=None)
    progress_body: ThreeLineProgressBody | None = field(init=False, default=None)
    live: Live | None = field(init=False, default=None)
    seen_units: set[str] = field(init=False, default_factory=set)
    errors: int = field(init=False, default=0)
    warnings: int = field(init=False, default=0)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="")
    completed: int | None = field(init=False, default=None)
    total: int | None = field(init=False, default=None)
    returncode: int | None = field(init=False, default=None)
    tests_passed: int = field(init=False, default=0)
    tests_failed: int = field(init=False, default=0)
    tests_skipped: int = field(init=False, default=0)
    tests_total: int = field(init=False, default=0)
    seen_test_results: set[str] = field(init=False, default_factory=set)
    _completion_printed: bool = field(init=False, default=False)
    _started_at: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.plan = dotnet_progress_plan(self.argv, self.cwd)
        self.total = self.plan.total if self.plan else None
        self.enabled = bool(self.plan and (self.console.is_terminal or self.console.is_interactive))

    def __enter__(self) -> "DotnetProgressTracker":
        if not self.enabled or self.plan is None:
            return self
        self._started_at = time.monotonic()
        self.progress_body = ThreeLineProgressBody(self._progress_state)
        self.live = Live(
            self._renderable(),
            console=self.console,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="ellipsis",
        )
        try:
            self.live.start()
        except LiveError:
            self.live = None
            self.progress_body = None
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

        self._update_issue_counts(text)

        event = self._event_for_line(text)
        if event:
            unit = str(event.get("unit") or "")
            detail = str(event.get("detail") or "")
            total = event.get("total")
            if unit:
                self.seen_units.add(unit)
                completed = len(self.seen_units)
                current_total = self.total if self.total is not None else self.plan.total
                # When the initial estimate is too low, do not chase the
                # completed count by reporting 8/8, 9/9, 10/10.  That makes
                # external HUDs look complete while the command is still
                # running.  Switch to indeterminate total until the command
                # finishes; finish() will then publish the final exact total.
                if current_total is not None and completed > current_total:
                    self.total = None
                    self._set_progress(completed=completed, detail=detail, force_callback=True)
                else:
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
        self.returncode = returncode
        if returncode == 0:
            if self.total is not None:
                completed = max(self.completed or 0, self.total)
                self._set_progress(completed=completed, total=self.total)
            elif self.completed is not None:
                self._set_progress(total=max(1, self.completed))
        command = self.plan.command if self.plan else (self.command_name or "dotnet")
        status = f"{command} ok" if returncode == 0 else f"{command} failed rc={returncode}"
        if self.errors or self.warnings:
            status += f" • diagnostics: {self.errors} errors, {self.warnings} warnings"
        if command == "test" and self.tests_total:
            assemblies = self._test_assembly_count()
            noun = "assembly" if assemblies == 1 else "assemblies"
            final_detail = (
                f"{status} • tests: {self.tests_passed} passed, {self.tests_failed} failed, "
                f"{self.tests_skipped} skipped across {assemblies} test {noun}"
            )
        else:
            final_detail = status
            if self.detail and self.detail != status:
                final_detail = f"{status} • {self.detail}"
        self._set_progress(detail=final_detail, force_callback=True)
        self._print_completion_once()

    def _print_completion_once(self) -> None:
        if not self.enabled or self._completion_printed or self.returncode is None:
            return
        self._completion_printed = True
        self.console.print(
            completion_line(
                command=self.command_name or (self.plan.command if self.plan else "dotnet"),
                returncode=self.returncode,
                started_at=self._started_at,
                snapshot=self.snapshot(),
            )
        )

    def _update_issue_counts(self, text: str) -> None:
        warning_summary = _MSBUILD_WARNING_SUMMARY_RE.match(text)
        if warning_summary:
            self.warnings = int(warning_summary.group("count"))
            return
        error_summary = _MSBUILD_ERROR_SUMMARY_RE.match(text)
        if error_summary:
            self.errors = int(error_summary.group("count"))
            return
        test_match = _TEST_RESULT_RE.search(text)
        if test_match:
            # Use the complete normalized result line as the identity. The same
            # assembly name may legitimately run once per target framework.
            result_key = " ".join(text.split())
            if result_key not in self.seen_test_results:
                self.seen_test_results.add(result_key)
                passed = int(test_match.group("passed"))
                failed = int(test_match.group("failed"))
                skipped = int(test_match.group("skipped"))
                total = int(test_match.group("total"))
                self.tests_passed += passed
                self.tests_failed += failed
                self.tests_skipped += skipped
                self.tests_total += total
                if failed > 0:
                    self.errors += failed
            return
        if _DIAGNOSTIC_WARNING_RE.search(text):
            self.warnings += 1
        if _DIAGNOSTIC_ERROR_RE.search(text):
            self.errors += 1

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
            "unit_label": self.plan.label if self.plan else "items",
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "tests_total": self.tests_total,
            "test_assemblies": self._test_assembly_count(),
        }

    def _set_progress(self, *, completed: int | None = None, total: int | None = None, detail: str | None = None, force_callback: bool = False) -> None:
        if completed is not None:
            self.completed = completed
        if total is not None:
            self.total = total
        if detail is not None:
            self.detail = detail
        if self.live is not None:
            self.live.update(self._renderable())
        if self.update_callback is not None and (force_callback or completed is not None or detail):
            self.update_callback(self.snapshot())

    def _progress_description(self) -> str:
        if self.plan is None:
            return self.command_name or "dotnet"
        command = self.plan.command
        if self.target_phase_index is None and self.phase_index is not None and self.phase_total:
            return f"{command} {self.phase_index}/{self.phase_total} · dotnet {command}"
        return f"dotnet {command}"

    def _format_initial_detail(self) -> str:
        if self.plan is None:
            return ""
        target = _repo_relative(self.plan.target, self.cwd)
        total = f" • expected {self.plan.total} {self.plan.label}" if self.plan.total else ""
        return f"scope: {target}{total}"

    def _test_assembly_count(self) -> int:
        assemblies = [unit for unit in self.seen_units if unit.startswith("test:") and unit != "test:summary"]
        if assemblies:
            return len(assemblies)
        return len(self.seen_test_results)

    def _progress_state(self) -> ThreeLineProgressState:
        label = self.plan.label if self.plan else "items"
        short = "assemblies" if label == "test assemblies" else "projects" if label == "projects" else "items"
        return ThreeLineProgressState(
            completed=int(self.completed or 0),
            total=self.total,
            unit_label=label,
            unit_short=short,
            detail=self.detail or self._format_initial_detail() or "Starting .NET",
            detail_root=self.cwd,
            started_at=self._started_at,
            returncode=self.returncode,
            phase_index=self.phase_index,
            phase_total=self.phase_total,
            phase_label=self.command_name or (self.plan.command if self.plan else "dotnet"),
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            tests_skipped=self.tests_skipped,
            tests_total=self.tests_total,
            warnings=self.warnings,
            errors=self.errors,
        )

    def _renderable(self) -> Panel:
        target = (self.display_target or (self.plan.target if self.plan else "") or "repository").strip()
        if self.plan is not None and not self.display_target:
            target = _repo_relative(target, self.cwd)
        target = compact_display_target(target, cwd=self.cwd)
        if self.returncode is None:
            icon = "◆"
            border_style = "cyan"
            status_style = "bold cyan"
        elif self.returncode == 0:
            icon = "✓"
            border_style = "green"
            status_style = "bold green"
        else:
            icon = "✗"
            border_style = "red"
            status_style = "bold red"

        title = active_panel_title(
            self.command_name or (self.plan.command if self.plan else ".NET"),
            f".NET · {target}",
            icon=icon,
            width=self.console.width,
        )
        title.stylize(status_style)
        phase_bits: list[str] = []
        if self.target_phase_index is not None and self.target_phase_total:
            phase_bits.append(f"target phase {self.target_phase_index}/{self.target_phase_total}")
        subtitle = Text(" • ".join(phase_bits), style="dim") if phase_bits else None
        body = self.progress_body or ThreeLineProgressBody(self._progress_state)
        return Panel(
            body,
            title=title,
            subtitle=subtitle,
            border_style=border_style,
            padding=(0, 1),
        )

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
            assemblies = self._test_assembly_count()
            noun = "assembly" if assemblies == 1 else "assemblies"
            detail = (
                f"tests: {self.tests_passed} passed, {self.tests_failed} failed, "
                f"{self.tests_skipped} skipped across {assemblies} {noun}"
            )
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
            # VSTest boilerplate is not progress and should not refresh HUD/terminal.
            return ""
        if _DIAGNOSTIC_ERROR_RE.search(text) or _DIAGNOSTIC_WARNING_RE.search(text):
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
    label = "test assemblies" if command == "test" else "projects"
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
        projects = sorted(
            p
            for p in path.rglob("*proj")
            if p.suffix.lower() in _PROJECT_EXTENSIONS
            and not _is_under_build_output(p)
        )

    if command == "test":
        # For `dotnet test`, progress units are runnable test assemblies, not
        # every transitive library built underneath them.  Expanding
        # ProjectReference dependencies made desktop/terminal HUDs show totals
        # such as 31 when only a handful of test DLLs actually run.
        test_projects = [p for p in projects if _looks_like_test_project(p)]
        projects = test_projects or projects
        if projects:
            return len(_dedupe_paths(projects))
        return None

    projects = _expand_project_references(projects)
    if projects:
        return len(projects)
    return None


def _is_under_build_output(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return "bin" in lowered or "obj" in lowered


def _looks_like_test_project(path: Path) -> bool:
    lowered_name = path.stem.lower()
    if "test" in lowered_name or "spec" in lowered_name:
        return True
    return any(part.lower() in {"test", "tests"} or "tests" in part.lower() for part in path.parts)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path.absolute()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered

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


def _expand_project_references(projects: Iterable[Path]) -> list[Path]:
    """Return projects plus transitive ProjectReference dependencies.

    MSBuild can build referenced projects even when they are not listed in the
    solution or when the CLI target is a single csproj.  Counting those refs up
    front prevents HUD totals from looking complete early and then growing as
    output lines arrive.
    """
    ordered: list[Path] = []
    seen: set[Path] = set()

    def visit(project: Path) -> None:
        try:
            normalized = project.resolve()
        except OSError:
            normalized = project.absolute()
        if normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)
        for ref in _project_references(normalized):
            visit(ref)

    for project in projects:
        visit(project)
    return ordered


def _project_references(project: Path) -> list[Path]:
    try:
        text = project.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    refs: list[Path] = []
    for match in _PROJECT_REFERENCE_RE.finditer(text):
        rel = match.group(1).replace("\\", os.sep)
        refs.append((project.parent / rel).resolve())
    return refs


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
    # Keep the relative output path so multi-target-framework executions of the
    # same project count as distinct test assemblies.
    return value.replace("\\", "/").strip() or Path(value).stem


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
