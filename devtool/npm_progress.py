from __future__ import annotations

"""Progress tracking for Node package-manager scripts.

The generic progress tracker can only render a determinate bar after command
output exposes a fraction or percentage.  Most ``npm run`` pipelines do not do
that: they print script banners such as ``> build`` and then free-form output.
This module reads ``package.json`` ahead of time, expands nested package-manager
``run`` commands, and uses the banners to show deterministic script progress.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import time
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.progress import ProgressColumn, Task
from rich.text import Text

from .rich_progress import (
    active_panel_title,
    ThreeLineProgressBody,
    ThreeLineProgressState,
    compact_display_target,
    project_live_output_line,
)
from .tui_icons import activity_icon
from .test_counts import TestCountAccumulator


_NODE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
_NPM_SCRIPT_BANNER_RE = re.compile(r"^>\s+(?:(?P<pkg>(?:@[^/\s]+/)?[^@\s]+)@[^\s]+\s+)?(?P<script>[^\s]+)\s*$")
_NPM_SCRIPT_BANNER_WITH_BODY_RE = re.compile(
    r"^>\s+(?:(?P<pkg>(?:@[^/\s]+/)?[^@\s]+)@[^\s]+\s+)?(?P<script>[^\s]+)\s*$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass(frozen=True, slots=True)
class NpmScriptStep:
    script: str
    root: Path

    @property
    def label(self) -> str:
        package_name = _package_name(self.root)
        if package_name:
            return f"{package_name}:{self.script}"
        return self.script


@dataclass(frozen=True, slots=True)
class NpmProgressPlan:
    manager: str
    root: Path
    script: str | None
    steps: tuple[NpmScriptStep, ...]


class NpmCountColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        total = int(task.total or 0)
        completed = int(task.completed or 0)
        if total <= 0:
            return Text("?/ ? scripts", style="progress.data.speed")
        return Text(f"{completed}/{total} scripts", style="progress.data.speed")


def npm_progress_plan(argv: list[str], cwd: str) -> NpmProgressPlan | None:
    parsed = _parse_run_command(argv, Path(cwd).expanduser().resolve())
    if parsed is None:
        return None
    manager, root, script = parsed
    steps = tuple(_expand_script_tree(manager, root, script))
    if not steps:
        steps = (NpmScriptStep(script=script, root=root),)
    return NpmProgressPlan(manager=manager, root=root, script=script, steps=steps)


@dataclass(slots=True)
class NpmProgressTracker:
    argv: list[str]
    cwd: str
    command_name: str
    console: Console
    phase_index: int | None = None
    phase_total: int | None = None
    display_target: str = ""
    target_phase_index: int | None = None
    target_phase_total: int | None = None
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    script_event_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="Starting npm script")
    returncode: int | None = field(init=False, default=None)
    last_line: str = field(init=False, default="")
    completed: int | None = field(init=False, default=0)
    total: int | None = field(init=False, default=None)
    percent: float | None = field(init=False, default=None)
    unit_label: str = field(init=False, default="scripts")
    current_script: str = field(init=False, default="")
    plan: NpmProgressPlan | None = field(init=False, default=None)
    _started_count: int = field(init=False, default=0)
    _started_at: float = field(init=False, default_factory=time.monotonic)
    tests_passed: int = field(init=False, default=0)
    tests_failed: int = field(init=False, default=0)
    tests_skipped: int = field(init=False, default=0)
    tests_total: int = field(init=False, default=0)
    _test_counts: TestCountAccumulator = field(init=False)

    def __post_init__(self) -> None:
        self.plan = npm_progress_plan(self.argv, self.cwd)
        self.total = len(self.plan.steps) if self.plan is not None else None
        self.enabled = bool(self.console.is_terminal or self.console.is_interactive)
        self._test_counts = TestCountAccumulator(framework="node")

    def __enter__(self) -> "NpmProgressTracker":
        self._started_at = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def update_from_line(self, line: str) -> None:
        text = _strip_ansi(line.strip())
        if not text:
            return
        live_text = project_live_output_line(text)
        parser_text = project_live_output_line(text, limit=16384)
        self.last_line = live_text
        if self._test_counts.feed(parser_text):
            counts = self._test_counts.counts
            self.tests_passed, self.tests_failed = counts.passed, counts.failed
            self.tests_skipped, self.tests_total = counts.skipped, counts.total
        banner = _parse_npm_banner(parser_text)
        if banner is not None:
            self._script_started(banner)
        else:
            self.detail = live_text
        self._publish()

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        if self.current_script and self.script_event_callback is not None:
            try:
                self.script_event_callback({
                    "kind": "script-finish",
                    "label": self.current_script,
                    "status": "cancelled" if returncode == 130 else "succeeded" if returncode == 0 else "failed",
                    "exit_code": int(returncode),
                })
            except Exception:
                pass
        if returncode == 0 and self.total is not None:
            self.completed = self.total
            self.percent = 100.0
        status = f"{self.command_name} ok" if returncode == 0 else f"{self.command_name} failed rc={returncode}"
        if self.current_script:
            self.detail = f"{status} • {self.current_script}"
        elif self.detail and self.detail != status:
            self.detail = f"{status} • {self.detail}"
        else:
            self.detail = status
        self._publish(force=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "command": self.command_name,
            "target": self.display_target or self.cwd,
            "detail": self.detail,
            "phase_name": self._phase_label(),
            "completed": self.completed,
            "total": self.total,
            "percent": self.percent,
            "warnings": 0,
            "errors": 0,
            "unit_label": self.unit_label,
            "returncode": self.returncode,
            "last_line": self.last_line,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "tests_total": self.tests_total,
            "current_task": self.current_script or None,
        }

    def _script_started(self, banner: tuple[str, str | None]) -> None:
        script, package_name = banner
        label = f"{package_name}:{script}" if package_name else script
        previous = self.current_script
        # Every npm lifecycle banner is a new invocation boundary. Consecutive
        # invocations may legitimately share the same display label; identity
        # comes from the planned occurrence, never from display text.
        if previous and self.script_event_callback is not None:
            try:
                self.script_event_callback({"kind": "script-finish", "label": previous, "status": "succeeded", "exit_code": 0})
            except Exception:
                pass
        self.current_script = label
        if self.script_event_callback is not None:
            try:
                self.script_event_callback({"kind": "script-start", "label": label})
            except Exception:
                pass
        self.detail = f"running {label}"
        if self.plan is None:
            return
        # A banner means a script started, not that it finished. When the next
        # lifecycle/workspace banner arrives the previous script has completed;
        # the final running script is resolved by finish(). This prevents a HUD
        # from showing 100% while postbuild is still running.
        if self.total is not None:
            if self._started_count > 0:
                self.completed = min(self.total, max(self.completed or 0, self._started_count))
            self._started_count = min(self.total, self._started_count + 1)
            self.percent = min(100.0, (self.completed or 0) * 100.0 / max(1, self.total))

    def _publish(self, *, force: bool = False) -> None:
        if self.update_callback is not None:
            snapshot = self.snapshot()
            if force:
                snapshot["force"] = True
            self.update_callback(snapshot)

    def _description(self) -> str:
        if self.phase_index is not None and self.phase_total:
            return f"npm {self.phase_index}/{self.phase_total}"
        if self.plan is not None and self.plan.script:
            return f"npm run {self.plan.script}"
        return self.command_name or "npm"

    def _phase_label(self) -> str:
        if self.current_script:
            return self.current_script
        if self.plan is not None and self.plan.script:
            return self.plan.script
        return "node scripts"

    def _progress_state(self) -> ThreeLineProgressState:
        return ThreeLineProgressState(
            completed=int(self.completed or 0),
            total=self.total,
            unit_label="scripts",
            unit_short="scripts",
            detail=self.current_script or self.detail or "Starting npm script",
            detail_root=self.cwd,
            started_at=self._started_at,
            returncode=self.returncode,
            phase_index=self.phase_index,
            phase_total=self.phase_total,
            phase_label=self.command_name,
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            tests_skipped=self.tests_skipped,
            tests_total=self.tests_total,
        )

    def _renderable(self) -> Panel:
        target = compact_display_target(self.display_target or self.cwd or "npm", cwd=self.cwd)
        if self.returncode is None:
            border = "cyan"
            title_style = "bold cyan"
            icon = activity_icon(kind="node", command=self.command_name or "npm", target=str(target), cwd=self.cwd)
        elif self.returncode == 0:
            border = "green"
            title_style = "bold green"
            icon = "✓"
        else:
            border = "red"
            title_style = "bold red"
            icon = "✗"
        title = active_panel_title(
            self.command_name or "npm",
            f"npm · {target}",
            icon=icon,
            width=self.console.width,
        )
        title.stylize(title_style)
        body = ThreeLineProgressBody(self._progress_state)
        return Panel(body, title=title, border_style=border, padding=(0, 1))


def _parse_run_command(argv: list[str], cwd: Path) -> tuple[str, Path, str] | None:
    if not argv:
        return None
    values = [str(item) for item in argv]
    executable = Path(values[0]).name.lower()
    if executable not in _NODE_MANAGERS:
        return None
    manager = executable
    root = cwd
    index = 1
    while index < len(values):
        token = values[index]
        if token in {"--prefix", "-C", "--cwd"} and index + 1 < len(values):
            root = (cwd / values[index + 1]).resolve() if not Path(values[index + 1]).is_absolute() else Path(values[index + 1]).resolve()
            index += 2
            continue
        if token.startswith("--prefix="):
            raw = token.split("=", 1)[1]
            root = (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
            index += 1
            continue
        if token in {"--workspace", "-w"} and index + 1 < len(values):
            workspace = values[index + 1]
            root = _resolve_workspace_root(cwd, workspace) or root
            index += 2
            continue
        if token.startswith("--workspace="):
            workspace = token.split("=", 1)[1]
            root = _resolve_workspace_root(cwd, workspace) or root
            index += 1
            continue
        break
    if manager in {"npm", "pnpm", "bun"}:
        if index < len(values) and values[index] == "run":
            if index + 1 < len(values):
                return manager, root, values[index + 1]
        if index < len(values) and values[index] == "run-script":
            if index + 1 < len(values):
                return manager, root, values[index + 1]
        return None
    if manager == "yarn":
        if index < len(values) and values[index] == "run" and index + 1 < len(values):
            return manager, root, values[index + 1]
        if index < len(values) and values[index] not in {"install", "add", "remove", "pack"}:
            return manager, root, values[index]
    return None


def _resolve_workspace_root(root: Path, selector: str) -> Path | None:
    direct = root / selector
    if direct.is_dir():
        return direct.resolve()
    try:
        payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    workspaces = payload.get("workspaces", []) if isinstance(payload, dict) else []
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])
    if not isinstance(workspaces, list):
        return None
    for pattern in workspaces:
        for candidate in root.glob(str(pattern)):
            if not candidate.is_dir():
                continue
            if candidate.name == selector or _package_name(candidate) == selector:
                return candidate.resolve()
    return None


def _expand_script_tree(manager: str, root: Path, script: str) -> list[NpmScriptStep]:
    steps: list[NpmScriptStep] = []

    def visit(current_root: Path, current_script: str, stack: tuple[tuple[Path, str], ...] = ()) -> None:
        resolved = current_root.resolve()
        key = (resolved, current_script)
        if len(stack) > 20 or key in stack:
            return
        scripts = _scripts_for_root(resolved)
        lifecycle = [
            name for name in (f"pre{current_script}", current_script, f"post{current_script}")
            if name in scripts
        ] or [current_script]
        next_stack = (*stack, key)
        for lifecycle_script in lifecycle:
            steps.append(NpmScriptStep(script=lifecycle_script, root=resolved))
            body = scripts.get(lifecycle_script, "")
            for nested_root, nested_script in _nested_run_commands(body, resolved):
                visit(nested_root, nested_script, next_stack)

    visit(root, script)
    return steps


def _nested_run_commands(command: str, root: Path) -> list[tuple[Path, str]]:
    commands: list[tuple[Path, str]] = []
    for segment in re.split(r"\s*(?:&&|\|\|)\s*", command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        index = 0
        while index < len(tokens):
            name = Path(tokens[index]).name.lower()
            if name not in _NODE_MANAGERS:
                index += 1
                continue
            parsed = _parse_run_command(tokens[index:], root)
            if parsed is not None:
                _manager, nested_root, nested_script = parsed
                commands.append((nested_root, nested_script))
            index += 1
    return commands


def _scripts_for_root(root: Path) -> dict[str, str]:
    try:
        payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
    return {str(key): str(value) for key, value in scripts.items()} if isinstance(scripts, dict) else {}


def _package_name(root: Path) -> str:
    try:
        payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("name") or "").strip()
    return ""


def _parse_npm_banner(text: str) -> tuple[str, str | None] | None:
    match = _NPM_SCRIPT_BANNER_RE.match(text) or _NPM_SCRIPT_BANNER_WITH_BODY_RE.match(text)
    if not match:
        return None
    return match.group("script"), match.group("pkg")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)
