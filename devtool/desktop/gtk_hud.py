from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..hud import (
    compact_detail_text,
    compact_operation_title,
    completion_copy_text,
    live_issue_counts,
    operation_steps,
    parse_hud_state,
    state_context_label,
    state_metric_text,
    state_window_title,
)
from .hud_store import load_hud_snapshot


_HUD_CAPABILITY_TOKENS = ("autoSizeEnabled", "manualResize")


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _gtk_can_initialize(Gtk: Any) -> bool:
    try:
        result = Gtk.init_check()
    except Exception:
        return False
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _require_gtk_initialized(Gtk: Any) -> None:
    if not _gtk_can_initialize(Gtk):
        raise RuntimeError(
            "GTK4 desktop HUD could not initialize the desktop display. "
            "Check DISPLAY/WAYLAND_DISPLAY and X11/Wayland access."
        )


class Gtk4CompactHud:
    """GTK4/libadwaita compact HUD renderer for Unix desktops.

    The renderer is intentionally model-driven.  It consumes the same Devtool
    HUD snapshot files and active-run pointer as the terminal, web, and
    notification backends; no progress parsing lives in the UI.
    """

    def __init__(self, *, recent_events: int = 8) -> None:
        self.recent_events = recent_events

    @staticmethod
    def _require_gtk4() -> tuple[Any, Any, Any, Any]:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw  # type: ignore[import-not-found]
        except (ValueError, ImportError):
            Adw = None
        from gi.repository import Gdk, GLib, Gtk  # type: ignore[import-not-found]

        return Adw, Gdk, GLib, Gtk

    @classmethod
    def available(cls) -> bool:
        if not _has_display():
            return False
        try:
            import importlib.util

            if importlib.util.find_spec("gi") is None:
                return False
            _Adw, _Gdk, _GLib, Gtk = cls._require_gtk4()
            if not _gtk_can_initialize(Gtk):
                return False
        except Exception:
            return False
        return True

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        if once:
            snapshot = load_hud_snapshot(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps({
                "state": snapshot.state,
                "events": snapshot.events,
                "phase_highlights": snapshot.phase_highlights,
                "renderer": "gtk4-window",
            }, ensure_ascii=False, indent=2))
            return 0
        if not _has_display():
            raise RuntimeError("GTK4 desktop HUD requires DISPLAY or WAYLAND_DISPLAY.")
        try:
            Adw, Gdk, GLib, Gtk = self._require_gtk4()
        except Exception as exc:  # pragma: no cover - depends on host desktop packages
            raise RuntimeError(
                "GTK4 desktop HUD requires PyGObject, GTK 4, and libadwaita. "
                "On Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
            ) from exc
        _require_gtk_initialized(Gtk)
        _install_progress_css(Gtk, Gdk)

        app_id = "io.github.mikeceead.DevtoolHud"
        if Adw is not None:
            application = Adw.Application(application_id=app_id)
        else:
            application = Gtk.Application(application_id=app_id)

        poll_ms = max(100, int(max(0.05, interval_s) * 1000))
        exit_code = 0

        def activate(app: Any) -> None:
            nonlocal exit_code
            try:
                _activate(app)
            except RuntimeError as exc:  # pragma: no cover - host display dependent
                exit_code = 2
                print(f"devtool-gtk-hud: {exc}", file=sys.stderr)
                app.quit()

        def _activate(app: Any) -> None:
            if Adw is not None:
                window = Adw.ApplicationWindow(application=app)
            else:
                window = Gtk.ApplicationWindow(application=app)
            window.set_title("Devtool HUD")
            window.set_default_size(640, 520)
            with _suppress_exception():
                window.set_icon_name("devtool-hud")
                window.set_resizable(True)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            box.set_margin_top(20)
            box.set_margin_bottom(16)
            box.set_margin_start(20)
            box.set_margin_end(20)
            with _suppress_exception():
                box.add_css_class("devtool-hud-root")

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            header.set_hexpand(True)
            header_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            header_copy.set_hexpand(True)
            title = Gtk.Label(label="devtool")
            title.set_xalign(0.0)
            title.set_wrap(True)
            with _suppress_exception():
                title.add_css_class("devtool-hud-title")
            status = Gtk.Label(label="waiting for devtool progress")
            status.set_xalign(0.0)
            status.set_wrap(True)
            with _suppress_exception():
                status.add_css_class("devtool-hud-subtitle")
            context = Gtk.Label(label="Waiting for target")
            context.set_xalign(0.0)
            context.set_wrap(True)
            with _suppress_exception():
                context.add_css_class("devtool-hud-context")
            status_pill = Gtk.Label(label="idle")
            status_pill.set_xalign(0.5)
            with _suppress_exception():
                status_pill.add_css_class("devtool-hud-status-pill")
            header_copy.append(title)
            header_copy.append(status)
            header_copy.append(context)
            header.append(header_copy)
            header.append(status_pill)

            progress = Gtk.ProgressBar()
            with _suppress_exception():
                progress.add_css_class("devtool-progress")
            metrics = Gtk.Grid(column_spacing=10, row_spacing=10)
            metrics.set_hexpand(True)
            phase_strip = Gtk.FlowBox()
            phase_strip.set_selection_mode(Gtk.SelectionMode.NONE)
            phase_strip.set_min_children_per_line(2)
            phase_strip.set_max_children_per_line(3)
            phase_strip.set_column_spacing(10)
            phase_strip.set_row_spacing(10)
            details = Gtk.TextView()
            details.set_editable(False)
            details.set_cursor_visible(False)
            details.set_monospace(True)
            details.set_vexpand(True)
            with _suppress_exception():
                details.add_css_class("devtool-details")
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(details)
            scroller.set_vexpand(True)
            scroller.set_min_content_height(108)
            with _suppress_exception():
                scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            with _suppress_exception():
                actions.add_css_class("devtool-action-row")
            copy_button = Gtk.Button(label="Copy summary")
            diagnostics_button = Gtk.Button(label="Copy diagnostics")
            log_button = Gtk.Button(label="Open log")
            close_button = Gtk.Button(label="Close")
            actions.append(copy_button)
            actions.append(diagnostics_button)
            actions.append(log_button)
            actions.append(close_button)
            box.append(header)
            box.append(progress)
            box.append(metrics)
            box.append(phase_strip)
            box.append(scroller)
            box.append(actions)
            if Adw is not None:
                window.set_content(box)
            else:
                window.set_child(box)

            last_snapshot: dict[str, Any] = {}

            def read() -> dict[str, Any]:
                snap = load_hud_snapshot(directory, follow_active=follow_active, recent_events=self.recent_events)
                state = snap.state
                events = snap.events
                hud_state = parse_hud_state(state)
                pct = state.get("percent")
                if pct is None:
                    pct = hud_state.percent
                try:
                    fraction = max(0.0, min(1.0, float(pct) / 100.0)) if pct is not None else 0.0
                except (TypeError, ValueError):
                    fraction = 0.0
                status_text = str(state.get("status") or "idle")
                if status_text.lower() == "running" and fraction <= 0.0:
                    fraction = 0.08
                window.set_title(state_window_title(hud_state))
                title.set_label(compact_operation_title(hud_state) or str(state.get("display_title") or state.get("title") or "devtool"))
                status.set_label(compact_detail_text(hud_state) or str(state.get("display_text") or state.get("text") or "waiting for devtool progress"))
                context.set_label(state_context_label(hud_state))
                status_pill.set_label(status_text.title())
                progress.set_fraction(fraction)
                progress.set_show_text(True)
                progress.set_text(f"{int(round(fraction * 100))}%" if pct is not None else status_text)
                _set_status_css(progress, status_text)
                _set_status_css(status_pill, status_text)
                errors, warnings = live_issue_counts(events, hud_state)
                _clear_grid(Gtk, metrics)
                _append_metric_card(Gtk, metrics, 0, "Errors", str(errors), "live diagnostics")
                _append_metric_card(Gtk, metrics, 1, "Warnings", str(warnings), "live diagnostics")
                _append_metric_card(Gtk, metrics, 2, "Metric", state_metric_text(hud_state, events), "parser metrics")
                _clear_flowbox(Gtk, phase_strip)
                steps = operation_steps(hud_state, events)
                if steps:
                    for step in steps[:12]:
                        _append_step_card(Gtk, phase_strip, step.to_dict())
                else:
                    phases = _phase_cards_from_snapshot(state, snap.phase_highlights)
                    if not phases:
                        phases = [("status", str(state.get("status") or "idle"), str(state.get("phase_name") or state.get("phase") or ""))]
                    for name, value, subtitle in phases[:8]:
                        _append_phase_card(Gtk, phase_strip, name, value, subtitle)
                detail_lines = ["Run", state_context_label(hud_state), state_metric_text(hud_state, events)]
                if steps:
                    detail_lines.append("")
                    detail_lines.append("Pipeline")
                    for step in steps:
                        detail_lines.append(f"{step.status}: {step.label} ({step.progress}%)")
                lanes = _workspace_lanes_from_steps([step.to_dict() for step in steps])
                if lanes:
                    detail_lines.append("")
                    detail_lines.append("workspace lane")
                    for lane, lane_steps in lanes.items():
                        detail_lines.append(f"{lane}: " + ", ".join(str(item.get("label") or "") for item in lane_steps))
                if not steps:
                    phases = [("status", str(state.get("status") or "idle"), str(state.get("phase_name") or state.get("phase") or ""))]
                for key in ("repo_name", "target", "phase_name", "status", "updated_at"):
                    value = state.get(key)
                    if value:
                        detail_lines.append(f"{key.replace('_', ' ')}: {value}")
                diagnostics = _diagnostics_from_state(state)
                if diagnostics:
                    detail_lines.append("")
                    detail_lines.append("Diagnostics")
                    for item in diagnostics[:8]:
                        detail_lines.append(f"{item.get('severity', 'info')}: {item.get('message', '')}")
                if events:
                    detail_lines.append("")
                    detail_lines.append("Events")
                for event in events[-8:]:
                    text = event.get("message") or event.get("text") or event.get("phase") or event.get("type")
                    if text:
                        detail_lines.append(f"• {text}")
                details.get_buffer().set_text("\n".join(detail_lines))
                last_snapshot.clear()
                last_snapshot.update({
                    "state": state,
                    "events": events,
                    "phase_highlights": snap.phase_highlights,
                    "diagnostics": diagnostics,
                    "steps": [step.to_dict() for step in steps],
                    "copy_text": completion_copy_text(hud_state),
                })
                return last_snapshot

            def copy_summary(_button: Any) -> None:
                snap = last_snapshot or read()
                text = str(snap.get("copy_text") or "") or json.dumps(snap, ensure_ascii=False, indent=2)
                clipboard = Gtk.Widget.get_clipboard(window)
                clipboard.set(text)

            def open_log(_button: Any) -> None:
                snap = load_hud_snapshot(directory, follow_active=follow_active, recent_events=self.recent_events)
                candidates = [snap.directory / "events.jsonl", snap.directory / "state.json"]
                opener = shutil.which("xdg-open")
                if not opener:
                    return
                for candidate in candidates:
                    if candidate.exists():
                        subprocess.Popen([opener, str(candidate)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return

            def copy_diagnostics(_button: Any) -> None:
                snap = last_snapshot or read()
                text = json.dumps(snap.get("diagnostics") or [], ensure_ascii=False, indent=2)
                clipboard = Gtk.Widget.get_clipboard(window)
                clipboard.set(text)

            copy_button.connect("clicked", copy_summary)
            diagnostics_button.connect("clicked", copy_diagnostics)
            log_button.connect("clicked", open_log)
            close_button.connect("clicked", lambda _button: window.close())
            _install_shortcuts(Gtk, window, {
                "<Control>c": copy_summary,
                "<Control>d": copy_diagnostics,
                "<Control>l": open_log,
                "Escape": lambda _unused: window.close(),
            })
            read()
            GLib.timeout_add(poll_ms, lambda: (read(), True)[1])
            window.present()

        application.connect("activate", activate)
        app_rc = int(application.run([]))
        return exit_code or app_rc


class _suppress_exception:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return True


def _clear_flowbox(Gtk: Any, box: Any) -> None:
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()


def _clear_grid(Gtk: Any, grid: Any) -> None:
    child = grid.get_first_child()
    while child is not None:
        grid.remove(child)
        child = grid.get_first_child()


def _append_metric_card(Gtk: Any, grid: Any, column: int, title: str, value: str, subtitle: str = "") -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.set_hexpand(True)
    card.set_size_request(176, 78)
    card.set_margin_top(0)
    card.set_margin_bottom(0)
    with _suppress_exception():
        card.add_css_class("devtool-metric-card")
    title_label = Gtk.Label(label=title.upper(), xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("devtool-card-label")
    value_label = Gtk.Label(label=value or "-", xalign=0.0)
    value_label.set_wrap(True)
    value_label.set_max_width_chars(24)
    with _suppress_exception():
        value_label.add_css_class("devtool-card-value")
    subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
    subtitle_label.set_wrap(True)
    with _suppress_exception():
        subtitle_label.add_css_class("devtool-card-subtitle")
    card.append(title_label)
    card.append(value_label)
    if subtitle:
        card.append(subtitle_label)
    grid.attach(card, column, 0, 1, 1)


def _append_phase_card(Gtk: Any, box: Any, title: str, value: str, subtitle: str = "") -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    card.set_size_request(140, 72)
    card.set_margin_top(4)
    card.set_margin_bottom(4)
    card.set_margin_start(4)
    card.set_margin_end(4)
    with _suppress_exception():
        card.add_css_class("devtool-step-card")
    title_label = Gtk.Label(label=title, xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("devtool-card-label")
    value_label = Gtk.Label(label=value or "-", xalign=0.0)
    value_label.set_wrap(True)
    with _suppress_exception():
        value_label.add_css_class("devtool-card-value")
    card.append(title_label)
    card.append(value_label)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
        subtitle_label.set_wrap(True)
        card.append(subtitle_label)
    box.append(card)


def _append_step_card(Gtk: Any, box: Any, step: dict[str, Any]) -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.set_size_request(178, 92)
    card.set_margin_top(4)
    card.set_margin_bottom(4)
    card.set_margin_start(4)
    card.set_margin_end(4)
    with _suppress_exception():
        card.add_css_class("devtool-step-card")
    label = Gtk.Label(label=str(step.get("label") or step.get("key") or "step"), xalign=0.0)
    label.set_wrap(True)
    label.set_max_width_chars(22)
    with _suppress_exception():
        label.add_css_class("devtool-step-title")
    status = Gtk.Label(label=str(step.get("status") or "pending"), xalign=0.0)
    with _suppress_exception():
        status.add_css_class("devtool-card-label")
    progress = Gtk.ProgressBar()
    with _suppress_exception():
        progress.add_css_class("devtool-progress")
    try:
        percent = max(0, min(100, int(step.get("progress") or 0)))
    except (TypeError, ValueError):
        percent = 0
    status_text = str(step.get("status") or "pending")
    fraction = percent / 100.0
    if status_text == "running" and fraction <= 0.0:
        fraction = 0.08
    progress.set_fraction(fraction)
    progress.set_show_text(True)
    progress.set_text(f"{percent}%")
    _set_status_css(progress, status_text)
    card.append(label)
    card.append(status)
    card.append(progress)
    box.append(card)


def _workspace_lanes_from_steps(steps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lanes: dict[str, list[dict[str, Any]]] = {}
    for step in steps:
        label = str(step.get("label") or "")
        if " · " not in label:
            continue
        _base, lane = label.split(" · ", 1)
        lanes.setdefault(lane, []).append(step)
    return lanes


def _install_shortcuts(Gtk: Any, window: Any, actions: dict[str, Any]) -> None:
    with _suppress_exception():
        controller = Gtk.ShortcutController()
        for trigger_text, callback in actions.items():
            trigger = Gtk.ShortcutTrigger.parse_string(trigger_text)
            action = Gtk.CallbackAction.new(lambda _widget, _args, cb=callback: (cb(None), True)[1])
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        window.add_controller(controller)


def _phase_cards_from_snapshot(state: dict[str, Any], phase_highlights: Any) -> list[tuple[str, str, str]]:
    cards: list[tuple[str, str, str]] = []
    if isinstance(phase_highlights, list):
        for item in phase_highlights:
            if not isinstance(item, dict):
                continue
            cards.append((
                str(item.get("name") or item.get("phase") or "phase"),
                str(item.get("status") or item.get("text") or item.get("percent") or ""),
                str(item.get("detail") or item.get("message") or ""),
            ))
    phase_progress = state.get("phase_progress")
    if isinstance(phase_progress, list):
        for item in phase_progress:
            if not isinstance(item, dict):
                continue
            cards.append((
                str(item.get("name") or item.get("phase") or "phase"),
                str(item.get("status") or item.get("percent") or ""),
                str(item.get("detail") or item.get("message") or ""),
            ))
    return cards


def _diagnostics_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = state.get("diagnostics")
    if isinstance(diagnostics, list):
        return [dict(item) for item in diagnostics if isinstance(item, dict)]
    results = state.get("results")
    collected: list[dict[str, Any]] = []
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, dict):
                continue
            for key in ("diagnostics", "errors", "warnings"):
                nested = result.get(key)
                if isinstance(nested, list):
                    collected.extend(dict(item) for item in nested if isinstance(item, dict))
    return collected


def _install_progress_css(Gtk: Any, Gdk: Any) -> None:
    css = b"""
    .devtool-hud-root {
      background-color: #202124;
    }
    .devtool-hud-title {
      font-size: 22px;
      font-weight: 800;
    }
    .devtool-hud-subtitle {
      color: rgba(255,255,255,0.88);
      font-weight: 600;
    }
    .devtool-hud-context,
    .devtool-card-label,
    .devtool-card-subtitle {
      color: rgba(255,255,255,0.58);
    }
    .devtool-hud-status-pill {
      min-width: 86px;
      padding: 7px 12px;
      border-radius: 999px;
      background-color: rgba(255,255,255,0.10);
      color: rgba(255,255,255,0.86);
      font-weight: 700;
    }
    .devtool-hud-status-pill.devtool-progress-ok {
      background-color: rgba(46,194,126,0.20);
      color: #8ff0b7;
    }
    .devtool-hud-status-pill.devtool-progress-failed {
      background-color: rgba(224,27,36,0.22);
      color: #ffb3b8;
    }
    .devtool-hud-status-pill.devtool-progress-cancelled {
      background-color: rgba(245,194,17,0.20);
      color: #f8e45c;
    }
    .devtool-metric-card,
    .devtool-step-card {
      padding: 10px 12px;
      border-radius: 10px;
      background-color: rgba(255,255,255,0.075);
      border: 1px solid rgba(255,255,255,0.08);
    }
    .devtool-card-value,
    .devtool-step-title {
      color: rgba(255,255,255,0.94);
      font-weight: 800;
    }
    .devtool-details {
      background-color: rgba(0,0,0,0.18);
      color: rgba(255,255,255,0.86);
    }
    progressbar.devtool-progress trough {
      min-height: 26px;
      border-radius: 7px;
      background-color: rgba(255,255,255,0.14);
    }
    progressbar.devtool-progress progress {
      min-height: 26px;
      border-radius: 7px;
      background-color: #62a0ea;
    }
    progressbar.devtool-progress.devtool-progress-ok progress {
      background-color: #2ec27e;
    }
    progressbar.devtool-progress.devtool-progress-failed progress {
      background-color: #e01b24;
    }
    progressbar.devtool-progress.devtool-progress-cancelled progress {
      background-color: #f5c211;
    }
    """
    with _suppress_exception():
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def _set_status_css(widget: Any, status: str) -> None:
    for css_class in ("devtool-progress-ok", "devtool-progress-failed", "devtool-progress-cancelled"):
        with _suppress_exception():
            widget.remove_css_class(css_class)
    normalized = str(status or "").lower()
    if normalized in {"ok", "success", "complete", "completed"}:
        widget.add_css_class("devtool-progress-ok")
    elif normalized in {"failed", "error", "failure"}:
        widget.add_css_class("devtool-progress-failed")
    elif normalized in {"cancelled", "canceled", "interrupted"}:
        widget.add_css_class("devtool-progress-cancelled")
