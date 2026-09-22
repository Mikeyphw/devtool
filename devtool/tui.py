from __future__ import annotations

"""Responsive terminal control surface for Devtool.

The TUI intentionally avoids curses/full-screen terminal assumptions.  It uses
Rich renderables and prompt-driven navigation so it remains usable in Termux,
tmux panes, SSH sessions, and terminals that resize while Devtool is running.
"""

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterable

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .artifact_registry import load_registry
from .artifacts import load_history
from .console_utils import create_console, detect_console_width, is_termux_environment
from .core.events import tui_event_snapshot
from .ui_core.navigation import navigation_label_for_route, navigation_shortcut_map
from .ui_core.copy import copy_text
from .ui_core.icons import resolve_icon
from .ui_core.viewmodels import runtime_state_to_ui_snapshot
from .ipc import request_run_cancel
from .ui_core.preferences import resolve_user_ui_preferences
from .ui_core.responsive import resolve_responsive_layout

COMPACT_MAX = 60
NARROW_MAX = 96
MIN_SUPPORTED_WIDTH = 40


@dataclass(frozen=True)
class TerminalProfile:
    width: int
    height: int
    mode: str
    termux: bool
    density: str = "compact"

    @property
    def compact(self) -> bool:
        return self.mode == "compact"

    @property
    def narrow(self) -> bool:
        return self.mode in {"compact", "narrow"}


def terminal_profile(*, width: int | None = None, height: int | None = None) -> TerminalProfile:
    detected = shutil.get_terminal_size(fallback=(100, 24))
    resolved_width = max(MIN_SUPPORTED_WIDTH, int(width or detect_console_width(detected.columns)))
    resolved_height = max(8, int(height or detected.lines))
    preferences = resolve_user_ui_preferences(surface="tui")
    layout = resolve_responsive_layout("tui", width=resolved_width, height=resolved_height, preferences=preferences)
    mode = {"single-pane": "compact", "two-pane": "narrow", "three-pane": "wide"}.get(layout.mode, "narrow")
    return TerminalProfile(resolved_width, resolved_height, mode, is_termux_environment(), layout.effective_density)


def _git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True,
            timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _short_path(value: str, *, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    if width <= 8:
        return text[:width]
    return "…" + text[-(width - 1):]


def _repo_snapshot(repo_root: Path) -> dict[str, Any]:
    branch = _git(repo_root, "branch", "--show-current") or "detached"
    commit = _git(repo_root, "rev-parse", "--short", "HEAD") or "-"
    dirty_text = _git(repo_root, "status", "--porcelain")
    registry = load_registry(repo_root)
    artifacts = [item for item in registry.get("items", []) if isinstance(item, dict)]
    history = load_history(repo_root)
    live: dict[str, Any] = {}
    try:
        from .hud import snapshot_from_directory
        from .tasker import DEFAULT_TASKER_DIR
        hud_payload = snapshot_from_directory(Path(DEFAULT_TASKER_DIR).expanduser(), recent_events=3, prefer_execution=True)
        state = hud_payload.get("runtime_state") if isinstance(hud_payload, dict) else {}
        if not isinstance(state, dict) or not state:
            state = hud_payload.get("state") if isinstance(hud_payload, dict) else {}
        if isinstance(state, dict):
            state_repo = str(state.get("repo_root") or "").strip()
            project = str((state.get("execution") or {}).get("project") or "") if isinstance(state.get("execution"), dict) else ""
            repo_matches = not state_repo or Path(state_repo).expanduser().resolve(strict=False) == repo_root.resolve(strict=False)
            project_matches = not project or project == repo_root.name
            if repo_matches and project_matches:
                live = state
    except Exception:
        live = {}
    return {
        "repo": repo_root.name,
        "root": str(repo_root),
        "branch": branch,
        "commit": commit,
        "dirty": bool(dirty_text),
        "dirty_count": len([line for line in dirty_text.splitlines() if line.strip()]),
        "artifacts": artifacts,
        "history": history,
        "live": live,
        "core_events": tui_event_snapshot(),
    }



_TERMINAL_ACTION_KEYS = {
    "operation.cancel": "c",
    "operation.retry": "y",
    "operation.rerun": "y",
    "validation.retry_failed": "f",
    "validation.resume": "v",
    "logs.open": "l",
    "summary.copy": "s",
    "diagnostics.copy": "x",
    "workflow.inspect": "i",
}


def _semantic_snapshot(snapshot: dict[str, Any], profile: TerminalProfile):
    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    if not live:
        return None
    presentations = live.get("ui_presentations") if isinstance(live.get("ui_presentations"), dict) else {}
    preferences = presentations.get("tui") if isinstance(presentations.get("tui"), dict) else None
    return runtime_state_to_ui_snapshot(
        live, surface="tui", preferences=preferences,
        viewport={"width": profile.width, "height": profile.height},
    )


def _action_key_map(ui_snapshot: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ui_snapshot is None:
        return result
    for action in ui_snapshot.actions:
        key = _TERMINAL_ACTION_KEYS.get(action.id)
        if key and action.enabled and key not in result:
            result[key] = action
    return result


def _semantic_live_panels(snapshot: dict[str, Any], profile: TerminalProfile) -> list[Any]:
    ui = _semantic_snapshot(snapshot, profile)
    if ui is None or ui.operation.status == "idle":
        return []
    icon_mode = ui.presentation.icons
    status_icon = resolve_icon(ui.operation.token, mode=icon_mode, surface="tui")
    progress = ui.progress_summary
    progress_text = "-"
    overall = progress.get("overall_percent")
    if overall is not None:
        progress_text = f"{overall}%"
    completed = progress.get("completed_nodes")
    total = progress.get("total_nodes")
    if completed is not None and total:
        progress_text += f" · {completed}/{total}"
    rows: list[tuple[str, Any]] = [
        ("Status", f"{status_icon} {copy_text(ui.operation.token, surface='tui') if ui.operation.token in {'status.success','status.running','status.pending','status.failed','status.warning','status.skipped','status.cancelled','status.blocked'} else ui.operation.status}"),
        ("Phase", ui.operation.phase or "-"),
        ("Progress", progress_text),
    ]
    if not profile.compact:
        rows.extend([
            ("Target", ui.operation.target or ", ".join(ui.selection.targets) or "-"),
            ("Runner", ui.operation.runner or "-"),
            ("Environment", ui.operation.execution_environment or "-"),
            ("Detail", ui.operation.detail or "-"),
        ])
    panels: list[Any] = [_kv_panel("Live build/test run", rows, profile=profile)]

    completed_statuses = {"ok", "passed", "success", "completed", "cached", "skipped"}
    visible = [node for node in ui.progress if ui.presentation.show_completed_steps or node.status not in completed_statuses]
    if ui.presentation.max_steps > 0:
        visible = visible[: ui.presentation.max_steps]
    if visible:
        step_rows = []
        for node in visible[: (4 if profile.compact else 8)]:
            pct = f" {node.percent}%" if node.percent is not None else ""
            step_rows.append((node.label, f"{node.status}{pct}" + (f" · {node.detail}" if node.detail and not profile.compact else "")))
        panels.append(_kv_panel("Progress", step_rows, profile=profile))

    if ui.diagnostic_presentations:
        diag_rows = []
        for item in ui.diagnostic_presentations[: (2 if profile.compact else 4)]:
            diag_rows.append((str(item.get("severity") or "info").upper(), item.get("what_happened") or item.get("summary") or "Issue"))
        panels.append(_kv_panel("Diagnostics", diag_rows, profile=profile))

    actions = _action_key_map(ui)
    if actions:
        panels.append(_kv_panel("Actions", [(key, action.short_label + (" · confirm" if action.requires_confirmation else "")) for key, action in actions.items()], profile=profile))
    return panels


def _copy_terminal_text(text: str) -> tuple[bool, str]:
    text = str(text or "")
    for argv in (["termux-clipboard-set"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(argv[0]) is None:
            continue
        try:
            subprocess.run(argv, input=text, text=True, timeout=2, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Copied to clipboard."
        except (OSError, subprocess.SubprocessError):
            continue
    return False, "Clipboard integration unavailable; content printed below."


def _dispatch_terminal_action(action: Any, ui: Any, *, console: Console) -> None:
    if action.requires_confirmation:
        prompt = action.confirmation_prompt or (action.label + "?")
        if not Confirm.ask(prompt, default=bool(action.confirmation_default), console=console):
            console.print("[dim]Action cancelled.[/dim]")
            return
    if action.id == "operation.cancel":
        ok, message = request_run_cancel(ui.operation.run_id, reason="TUI request")
        console.print(("[green]" if ok else "[yellow]") + message + ("[/green]" if ok else "[/yellow]"))
        return
    if action.id == "logs.open":
        console.print(f"[bold]Log[/bold] {ui.operation.log_path or action.payload.get('log_path') or '-'}")
        return
    if action.id == "summary.copy":
        text = f"{ui.operation.title}\nstatus={ui.operation.status}\nphase={ui.operation.phase}\ntarget={ui.operation.target}\ndetail={ui.operation.detail}\nlog={ui.operation.log_path}"
        ok, message = _copy_terminal_text(text)
        console.print(f"[green]{message}[/green]" if ok else f"[yellow]{message}[/yellow]\n{text}")
        return
    if action.id == "diagnostics.copy":
        text = "\n\n".join(f"{d.severity.upper()}: {d.message}" + (f"\n{d.location}" if d.location else "") for d in ui.diagnostics)
        ok, message = _copy_terminal_text(text)
        console.print(f"[green]{message}[/green]" if ok else f"[yellow]{message}[/yellow]\n{text}")
        return
    if action.id == "workflow.inspect":
        target = ui.operation.target or (ui.selection.targets[0] if ui.selection.targets else "")
        console.print(f"[bold]Workflow[/bold] devtool workflows" + (f" --target {target}" if target else ""))
        return
    # Retry/resume actions are semantically exposed even when exact original
    # argv is unavailable. Give an executable conservative command rather than
    # inventing hidden state.
    command = ui.operation.command or ui.selection.operation or "validate"
    target = ui.operation.target or (ui.selection.targets[0] if ui.selection.targets else "")
    hint = f"devtool {command}" + (f" --target {target}" if target else "")
    console.print(f"[bold]{action.short_label}[/bold]: {hint}")

def _kv_panel(title: str, rows: Iterable[tuple[str, Any]], *, profile: TerminalProfile) -> Panel:
    if profile.compact:
        body = Text()
        first = True
        for key, value in rows:
            if not first:
                body.append("\n")
            first = False
            body.append(f"{key}: ", style="bold")
            body.append(str(value))
        return Panel(body, title=title, box=box.ROUNDED, padding=(0, 1))

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style="bold", no_wrap=True, ratio=1)
    table.add_column(ratio=3, overflow="fold")
    for key, value in rows:
        table.add_row(str(key), str(value))
    return Panel(table, title=title, box=box.ROUNDED, padding=(0, 1))


def _dashboard(snapshot: dict[str, Any], profile: TerminalProfile) -> Group:
    state = "dirty" if snapshot["dirty"] else "clean"
    repo_rows = [
        ("Repo", snapshot["repo"]),
        ("Branch", snapshot["branch"]),
        ("Commit", snapshot["commit"]),
        ("Tree", f"{state} ({snapshot['dirty_count']} changes)" if snapshot["dirty"] else state),
    ]
    if not profile.compact:
        repo_rows.append(("Root", _short_path(snapshot["root"], width=max(24, profile.width - 20))))

    history = snapshot["history"]
    artifacts = snapshot["artifacts"]
    summary_rows = [
        ("Artifacts", len(artifacts)),
        ("Apply history", len(history)),
        ("Terminal", f"{profile.width}×{profile.height} {profile.mode}"),
    ]
    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    live_status = str(live.get("status") or "").strip()
    if live_status and live_status != "idle":
        phase = str(live.get("phase_name") or live.get("event_type") or "").strip()
        summary_rows.insert(0, ("Live run", f"{live_status}: {phase}" if phase else live_status))
    events = snapshot.get("core_events") if isinstance(snapshot.get("core_events"), list) else []
    if events:
        latest = events[-1]
        summary_rows.append(("Event", str(latest.get("type") or "-") + (f": {latest.get('task')}" if latest.get("task") else "")))
    if profile.termux:
        summary_rows.append(("Runtime", "Termux"))
    return Group(
        _kv_panel("Repository", repo_rows, profile=profile),
        _kv_panel("Overview", summary_rows, profile=profile),
        *_semantic_live_panels(snapshot, profile),
    )


def _build_page(snapshot: dict[str, Any], profile: TerminalProfile) -> Group:
    panels: list[Any] = []
    panels.extend(_semantic_live_panels(snapshot, profile))

    artifacts = snapshot["artifacts"][:8 if profile.mode == "wide" else 5 if profile.mode == "narrow" else 3]
    if not artifacts:
        panels.append(_kv_panel("Build artifacts", [("Status", "No registered artifacts")], profile=profile))
        return Group(*panels)

    if profile.compact:
        for item in artifacts:
            name = str(item.get("name") or item.get("path") or "artifact")
            panels.append(_kv_panel(name, [
                ("Valid", "yes" if item.get("valid", True) else "no"),
                ("Size", item.get("size", "-")),
                ("Commit", str(item.get("commit") or "-")[:12]),
            ], profile=profile))
        return Group(*panels)

    table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
    table.add_column("Artifact", ratio=3, overflow="ellipsis")
    table.add_column("State", no_wrap=True)
    if profile.mode == "wide":
        table.add_column("Size", justify="right", no_wrap=True)
        table.add_column("Commit", no_wrap=True)
        table.add_column("Registered", ratio=2, overflow="ellipsis")
    for item in artifacts:
        row = [
            str(item.get("name") or item.get("path") or "artifact"),
            "ok" if item.get("valid", True) else "bad",
        ]
        if profile.mode == "wide":
            row.extend([
                str(item.get("size", "-")),
                str(item.get("commit") or "-")[:10],
                str(item.get("registered_at") or "-"),
            ])
        table.add_row(*row)
    panels.append(Panel(table, title="Build artifacts", box=box.ROUNDED, padding=(0, 1)))
    return Group(*panels)


def _overlay_page(snapshot: dict[str, Any], profile: TerminalProfile, *, repo_root: Path, artifact: str = "") -> Group:
    panels: list[Any] = []
    artifact = str(artifact or "").strip()
    if artifact:
        try:
            from .overlay import check_overlay
            check = check_overlay(repo_root, artifact)
            rows = [
                ("Artifact", Path(check.artifact).name),
                ("Structural", "ok" if check.structural_ok else "fail"),
                ("Source SHA", "ok" if check.source_state_ok else "changed"),
                ("Conflicts", len(check.conflicts)),
                ("Rebase", "available" if check.rebaseable else "not needed" if check.ok else "blocked"),
            ]
            if profile.mode == "wide" and check.messages:
                rows.append(("Note", check.messages[-1]))
            panels.append(_kv_panel("Overlay preflight", rows, profile=profile))
        except Exception as exc:
            panels.append(_kv_panel("Overlay preflight", [("Artifact", Path(artifact).name), ("Status", f"unavailable: {exc}")], profile=profile))
    history = list(reversed(snapshot["history"]))
    limit = 8 if profile.mode == "wide" else 5 if profile.mode == "narrow" else 3
    history = history[:limit]
    if not history:
        panels.append(_kv_panel("Overlay history", [("Status", "No apply history")], profile=profile))
        return Group(*panels)

    if profile.compact:
        for item in history:
            name = str(item.get("artifact_name") or Path(str(item.get("artifact") or "overlay")).name)
            panels.append(_kv_panel(name, [
                ("Status", item.get("status") or "-"),
                ("Commit", str(item.get("commit_hash") or "-")[:12]),
                ("Validation", item.get("validation_status") or "-"),
            ], profile=profile))
        return Group(*panels)

    table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
    table.add_column("Overlay", ratio=4, overflow="ellipsis")
    table.add_column("Status", no_wrap=True)
    table.add_column("Commit", no_wrap=True)
    if profile.mode == "wide":
        table.add_column("Validation", no_wrap=True)
        table.add_column("Applied", ratio=2, overflow="ellipsis")
    for item in history:
        name = str(item.get("artifact_name") or Path(str(item.get("artifact") or "overlay")).name)
        row = [name, str(item.get("status") or "-"), str(item.get("commit_hash") or "-")[:10]]
        if profile.mode == "wide":
            row.extend([str(item.get("validation_status") or "-"), str(item.get("applied_at") or "-")])
        table.add_row(*row)
    panels.append(Panel(table, title="Overlay history", box=box.ROUNDED, padding=(0, 1)))
    return Group(*panels)




def _workers_page(snapshot: dict[str, Any], profile: TerminalProfile, *, repo_root: Path) -> Group:
    try:
        from .ssh_profiles import list_profiles
        payload = list_profiles(repo=repo_root)
        records = list(payload.get("records", []) or [])
    except Exception as exc:
        return Group(_kv_panel("SSH workers", [("Status", f"unavailable: {exc}")], profile=profile))

    panels: list[Any] = [_kv_panel("SSH worker configuration", [
        ("Default", payload.get("default_profile") or "-"),
        ("Profiles", len(records)),
        ("Global", _short_path(str(payload.get("global_config") or "-"), width=max(18, profile.width - 18))),
        ("Repository", _short_path(str(payload.get("repo_config") or "-"), width=max(18, profile.width - 18))),
    ], profile=profile)]
    if not records:
        panels.append(_kv_panel("Workers", [
            ("Status", "No SSH workers configured"),
            ("Add", "devtool ssh add tablet --host HOST --port 8022 --user USER"),
        ], profile=profile))
        return Group(*panels)

    if profile.compact:
        for item in records[:4]:
            destination = f"{item.get('user') + '@' if item.get('user') else ''}{item.get('host')}:{item.get('port')}"
            panels.append(_kv_panel(str(item.get("name") or "worker"), [
                ("Default", "yes" if item.get("default") else "no"),
                ("Scope", item.get("scope") or "-"),
                ("Host", destination),
                ("Workspace", item.get("workspace_mode") or "-"),
                ("Provider", item.get("execution_provider") or "ssh-native"),
            ], profile=profile))
    else:
        table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
        table.add_column("Worker", ratio=2, overflow="ellipsis")
        table.add_column("Scope", no_wrap=True)
        table.add_column("Destination", ratio=3, overflow="ellipsis")
        table.add_column("Workspace", no_wrap=True)
        table.add_column("Provider", no_wrap=True)
        if profile.mode == "wide":
            table.add_column("Default", no_wrap=True)
        for item in records[:8]:
            destination = f"{item.get('user') + '@' if item.get('user') else ''}{item.get('host')}:{item.get('port')}"
            row = [
                str(item.get("name") or "-"),
                str(item.get("scope") or "-"),
                destination,
                str(item.get("workspace_mode") or "-"),
                str(item.get("execution_provider") or "ssh-native"),
            ]
            if profile.mode == "wide":
                row.append("yes" if item.get("default") else "")
            table.add_row(*row)
        panels.append(Panel(table, title="Workers", box=box.ROUNDED, padding=(0, 1)))

    selected = str(payload.get("default_profile") or records[0].get("name") or "").strip()
    if selected:
        panels.append(_kv_panel("Worker actions", [
            ("Doctor", f"devtool ssh doctor {selected}"),
            ("Qualify", f"devtool -r {repo_root} ssh qualify {selected}"),
            ("Runs", f"devtool ssh runs {selected}"),
            ("Full seal", f"devtool -r {repo_root} ssh qualify {selected} --project-validation --target TARGET --task TASK"),
        ], profile=profile))
    return Group(*panels)


def render_page(repo_root: Path, *, page: str, width: int | None = None, height: int | None = None, artifact: str = "") -> tuple[Group, TerminalProfile]:
    profile = terminal_profile(width=width, height=height)
    snapshot = _repo_snapshot(repo_root)
    normalized = str(page or "dashboard").lower()
    if normalized == "build":
        renderable = _build_page(snapshot, profile)
    elif normalized in {"overlay", "overlays"}:
        renderable = _overlay_page(snapshot, profile, repo_root=repo_root, artifact=artifact)
    elif normalized in {"worker", "workers", "ssh"}:
        renderable = _workers_page(snapshot, profile, repo_root=repo_root)
    else:
        renderable = _dashboard(snapshot, profile)
    return renderable, profile


def _header(page: str, profile: TerminalProfile) -> Text:
    label = navigation_label_for_route("tui", page, default=page.title())
    suffix = " · Termux" if profile.termux else ""
    return Text(f"Devtool · {label}{suffix}", style="bold")


def run_tui(args: Any, *, repo_root: Path, console: Console | None = None) -> int:
    console = console or create_console()
    page = str(getattr(args, "page", "dashboard") or "dashboard")
    width_override = int(getattr(args, "width", 0) or 0) or None
    artifact = str(getattr(args, "artifact", "") or "")
    once = bool(getattr(args, "once", False))

    def draw() -> TerminalProfile:
        # Recompute the terminal profile on every draw. This is important for
        # Termux/tmux where rotation and pane resizing are common.
        current_width = width_override or detect_console_width(console.width)
        renderable, profile = render_page(repo_root, page=page, width=current_width, height=console.height, artifact=artifact)
        console.print(_header(page, profile))
        console.print(renderable)
        nav_shortcuts = navigation_shortcut_map("tui")
        if profile.compact:
            bits = [f"{key} {navigation_label_for_route('tui', route).lower()[:5]}" for key, route in nav_shortcuts.items()]
            console.print(Text(" · ".join([*bits, "q quit"]), style="dim"))
        else:
            bits = [f"{key} {navigation_label_for_route('tui', route).lower()}" for key, route in nav_shortcuts.items()]
            console.print("[dim]" + " · ".join([*bits, "r refresh", "q quit"]) + "[/dim]")
        return profile

    draw()
    if once or not console.is_terminal:
        return 0

    while True:
        nav_shortcuts = navigation_shortcut_map("tui")
        current_profile = terminal_profile(width=width_override or detect_console_width(console.width), height=console.height)
        current_snapshot = _repo_snapshot(repo_root)
        ui = _semantic_snapshot(current_snapshot, current_profile)
        action_shortcuts = _action_key_map(ui)
        choices = list(dict.fromkeys([*nav_shortcuts, *action_shortcuts, "r", "q"]))
        try:
            choice = Prompt.ask("devtool", choices=choices, default="r", console=console)
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if choice == "q":
            return 0
        if choice in nav_shortcuts:
            page = nav_shortcuts[choice]
        elif choice in action_shortcuts and ui is not None:
            _dispatch_terminal_action(action_shortcuts[choice], ui, console=console)
        # Do not clear the terminal. Appending a fresh frame is more reliable
        # on narrow/mobile terminals and preserves scrollback for diagnostics.
        draw()
