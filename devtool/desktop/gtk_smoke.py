from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Mapping, Sequence


GTK_WARNING_PATTERNS = (
    re.compile(r"\b(?:Gtk|Gdk|Adwaita|Adw)-WARNING\b", re.IGNORECASE),
    re.compile(r"\b(?:Gtk|Gdk|Adwaita|Adw)-CRITICAL\b", re.IGNORECASE),
    re.compile(r"exceeds\s+.*available", re.IGNORECASE),
    re.compile(r"requested\s+\d+\s*px,\s*\d+\s*px\s+available", re.IGNORECASE),
)


@dataclass(slots=True)
class GtkStartupProbe:
    name: str
    command: list[str]
    wrapped_command: list[str]
    status: str
    required: bool
    detail: str
    output: str = ""
    log_path: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed" or (self.status == "skipped" and not self.required)


def has_xvfb() -> bool:
    return shutil.which("xvfb-run") is not None


def has_display(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def gtk_runtime_import_probe(python: str = "/usr/bin/python3", *, timeout: int = 15) -> tuple[bool, str]:
    probe = (
        "import gi, rich, devtool; "
        "gi.require_version('Gtk','4.0'); "
        "gi.require_version('Adw','1'); "
        "from gi.repository import Gtk, Adw; "
        "from devtool.desktop.control_center_cli import main as control_main; "
        "from devtool.desktop.gtk_hud_cli import main as hud_main; "
        "print('gtk-runtime-imports-ok')"
    )
    try:
        completed = subprocess.run(
            [python, "-c", probe],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = completed.stdout.strip()
    return completed.returncode == 0, output


def wrap_with_display(command: Sequence[str], *, environment: Mapping[str, str] | None = None, geometry: str = "1280x720x24") -> list[str] | None:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    if has_display(env):
        return list(command)
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        return None
    return [xvfb, "-a", "-s", f"-screen 0 {geometry}", *list(command)]


def warning_lines(output: str) -> list[str]:
    lines: list[str] = []
    for line in output.splitlines():
        if any(pattern.search(line) for pattern in GTK_WARNING_PATTERNS):
            lines.append(line)
    return lines


def run_gtk_startup_test(
    name: str,
    command: Sequence[str],
    *,
    root: Path,
    logs_dir: Path,
    required: bool,
    timeout: int = 30,
    geometry: str = "1280x720x24",
    environment: Mapping[str, str] | None = None,
) -> GtkStartupProbe:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{name}.log"
    env = os.environ.copy()
    env.update({
        "G_MESSAGES_DEBUG": "all",
        "DEVTOOL_DESKTOP_STARTUP_TEST": "1",
    })
    if environment:
        env.update(environment)
    wrapped = wrap_with_display(command, environment=env, geometry=geometry)
    if wrapped is None:
        detail = "DISPLAY/WAYLAND_DISPLAY and xvfb-run are unavailable"
        log_path.write_text(detail + "\n", encoding="utf-8")
        return GtkStartupProbe(name=name, command=list(command), wrapped_command=[], status="failed" if required else "skipped", required=required, detail=detail, log_path=str(log_path))
    try:
        completed = subprocess.run(
            wrapped, cwd=root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        detail = f"window did not close before startup-test timeout ({timeout}s)"
        log_path.write_text(output + ("\n" if output else "") + detail + "\n", encoding="utf-8")
        return GtkStartupProbe(name=name, command=list(command), wrapped_command=wrapped, status="failed", required=required, detail=detail, output=output, log_path=str(log_path))
    output = completed.stdout or ""
    problems = warning_lines(output)
    if completed.returncode != 0:
        detail = f"startup-test exited {completed.returncode}"
        status = "failed"
    elif problems:
        detail = "GTK/Adwaita warnings detected: " + "; ".join(problems[:3])
        status = "failed"
    else:
        detail = "real GTK window startup-test passed without warnings"
        status = "passed"
    log_path.write_text(output + ("\n" if output else "") + detail + "\n", encoding="utf-8")
    return GtkStartupProbe(name=name, command=list(command), wrapped_command=wrapped, status=status, required=required, detail=detail, output=output, log_path=str(log_path))


SHORT_WORKAREA_GEOMETRY = "1024x722x24"
CONSTRAINED_LAYOUT_ENV = {
    "DEVTOOL_DESKTOP_ASSERT_CONSTRAINED_LAYOUT": "1",
    "DEVTOOL_GTK_THREAD_GUARD": "1",
}


def constrained_layout_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(CONSTRAINED_LAYOUT_ENV)
    if environment:
        env.update({str(key): str(value) for key, value in environment.items()})
    return env


def run_gtk_constrained_layout_test(
    name: str,
    command: Sequence[str],
    *,
    root: Path,
    logs_dir: Path,
    required: bool,
    timeout: int = 30,
    environment: Mapping[str, str] | None = None,
) -> GtkStartupProbe:
    """Run a mapped-window probe in the short work area that exposed the HUD bug.

    The user's original failure was an Adwaita height overflow at 722 px of
    available height. This gate intentionally uses the same vertical budget and
    enables the GTK thread guard so layout warnings and worker-thread mutations
    become release blockers instead of invisible desktop-launch goblins.
    """
    env = constrained_layout_environment(environment)
    return run_gtk_startup_test(
        name,
        command,
        root=root,
        logs_dir=logs_dir,
        required=required,
        timeout=timeout,
        geometry=SHORT_WORKAREA_GEOMETRY,
        environment=env,
    )


def control_center_startup_command(*, repo: Path | str = ".", python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", "devtool", "control-center", "--backend", "gtk4", "--startup-test", "--repo", str(repo)]


def hud_startup_command(*, state_dir: Path | str, python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", "devtool.desktop.gtk_hud_cli", "--startup-test", "--state-dir", str(state_dir)]
