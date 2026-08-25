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
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .artifact_registry import load_registry
from .artifacts import load_history
from .console_utils import create_console, detect_console_width, is_termux_environment

COMPACT_MAX = 60
NARROW_MAX = 96
MIN_SUPPORTED_WIDTH = 40


@dataclass(frozen=True)
class TerminalProfile:
    width: int
    height: int
    mode: str
    termux: bool

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
    mode = "compact" if resolved_width <= COMPACT_MAX else "narrow" if resolved_width <= NARROW_MAX else "wide"
    return TerminalProfile(resolved_width, resolved_height, mode, is_termux_environment())


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
        hud_payload = snapshot_from_directory(Path(DEFAULT_TASKER_DIR).expanduser(), recent_events=3)
        state = hud_payload.get("state") if isinstance(hud_payload, dict) else {}
        if isinstance(state, dict):
            state_repo = str(state.get("repo_root") or "").strip()
            if not state_repo or Path(state_repo).expanduser().resolve(strict=False) == repo_root.resolve(strict=False):
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
    }


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
    if profile.termux:
        summary_rows.append(("Runtime", "Termux"))
    return Group(
        _kv_panel("Repository", repo_rows, profile=profile),
        _kv_panel("Overview", summary_rows, profile=profile),
    )


def _build_page(snapshot: dict[str, Any], profile: TerminalProfile) -> Group:
    panels: list[Any] = []
    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    live_status = str(live.get("status") or "").strip()
    if live_status and live_status != "idle":
        progress = "-"
        current, total = live.get("current"), live.get("total")
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            progress = f"{current}/{total} ({round(current / total * 100)}%)"
        live_rows = [
            ("Status", live_status),
            ("Phase", live.get("phase_name") or live.get("event_type") or "-"),
            ("Progress", progress),
        ]
        if not profile.compact:
            live_rows.append(("Command", live.get("command") or "-"))
            live_rows.append(("Detail", live.get("text") or "-"))
        panels.append(_kv_panel("Live build/test run", live_rows, profile=profile))

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


def render_page(repo_root: Path, *, page: str, width: int | None = None, height: int | None = None, artifact: str = "") -> tuple[Group, TerminalProfile]:
    profile = terminal_profile(width=width, height=height)
    snapshot = _repo_snapshot(repo_root)
    normalized = str(page or "dashboard").lower()
    if normalized == "build":
        renderable = _build_page(snapshot, profile)
    elif normalized in {"overlay", "overlays"}:
        renderable = _overlay_page(snapshot, profile, repo_root=repo_root, artifact=artifact)
    else:
        renderable = _dashboard(snapshot, profile)
    return renderable, profile


def _header(page: str, profile: TerminalProfile) -> Text:
    label = {"dashboard": "Dashboard", "build": "Build", "overlays": "Overlays"}.get(page, page.title())
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
        if profile.compact:
            console.print(Text("d dash · b build · o ovl · q quit", style="dim"))
        else:
            console.print("[dim]d dashboard · b build · o overlays · r refresh · q quit[/dim]")
        return profile

    draw()
    if once or not console.is_terminal:
        return 0

    while True:
        try:
            choice = Prompt.ask("devtool", choices=["d", "b", "o", "r", "q"], default="r", console=console)
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if choice == "q":
            return 0
        if choice == "d":
            page = "dashboard"
        elif choice == "b":
            page = "build"
        elif choice == "o":
            page = "overlays"
        # Do not clear the terminal. Appending a fresh frame is more reliable
        # on narrow/mobile terminals and preserves scrollback for diagnostics.
        draw()
