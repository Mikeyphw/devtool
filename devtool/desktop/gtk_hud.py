from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .hud_store import load_hud_snapshot


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
            window.set_default_size(560, 430)
            with _suppress_exception():
                window.set_icon_name("devtool-hud")
                window.set_resizable(True)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(16)
            box.set_margin_bottom(16)
            box.set_margin_start(16)
            box.set_margin_end(16)
            title = Gtk.Label(label="devtool")
            title.set_xalign(0.0)
            with _suppress_exception():
                title.add_css_class("title-2")
            status = Gtk.Label(label="waiting for devtool progress")
            status.set_xalign(0.0)
            status.set_wrap(True)
            progress = Gtk.ProgressBar()
            with _suppress_exception():
                progress.add_css_class("devtool-progress")
            phase_strip = Gtk.FlowBox()
            phase_strip.set_selection_mode(Gtk.SelectionMode.NONE)
            phase_strip.set_min_children_per_line(2)
            phase_strip.set_max_children_per_line(4)
            phase_strip.set_column_spacing(8)
            phase_strip.set_row_spacing(8)
            details = Gtk.TextView()
            details.set_editable(False)
            details.set_cursor_visible(False)
            details.set_monospace(True)
            details.set_vexpand(True)
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(details)
            scroller.set_vexpand(True)
            with _suppress_exception():
                scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_button = Gtk.Button(label="Copy summary")
            diagnostics_button = Gtk.Button(label="Copy diagnostics")
            log_button = Gtk.Button(label="Open log")
            close_button = Gtk.Button(label="Close")
            actions.append(copy_button)
            actions.append(diagnostics_button)
            actions.append(log_button)
            actions.append(close_button)
            box.append(title)
            box.append(status)
            box.append(progress)
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
                pct = state.get("percent")
                try:
                    fraction = max(0.0, min(1.0, float(pct) / 100.0)) if pct is not None else 0.0
                except (TypeError, ValueError):
                    fraction = 0.0
                title.set_label(str(state.get("display_title") or state.get("title") or "devtool"))
                status.set_label(str(state.get("display_text") or state.get("text") or "waiting for devtool progress"))
                progress.set_fraction(fraction)
                progress.set_show_text(True)
                progress.set_text(f"{int(round(fraction * 100))}%" if pct is not None else "idle")
                _set_status_css(progress, str(state.get("status") or "idle"))
                _clear_flowbox(Gtk, phase_strip)
                phases = _phase_cards_from_snapshot(state, snap.phase_highlights)
                if not phases:
                    phases = [("status", str(state.get("status") or "idle"), str(state.get("phase_name") or state.get("phase") or ""))]
                for name, value, subtitle in phases[:8]:
                    _append_phase_card(Gtk, phase_strip, name, value, subtitle)
                detail_lines = ["Run"]
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
                last_snapshot.update({"state": state, "events": events, "phase_highlights": snap.phase_highlights, "diagnostics": diagnostics})
                return last_snapshot

            def copy_summary(_button: Any) -> None:
                text = json.dumps(last_snapshot or read(), ensure_ascii=False, indent=2)
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


def _append_phase_card(Gtk: Any, box: Any, title: str, value: str, subtitle: str = "") -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    card.set_size_request(116, 58)
    card.set_margin_top(4)
    card.set_margin_bottom(4)
    card.set_margin_start(4)
    card.set_margin_end(4)
    with _suppress_exception():
        card.add_css_class("card")
    title_label = Gtk.Label(label=title, xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("dim-label")
    value_label = Gtk.Label(label=value or "-", xalign=0.0)
    value_label.set_wrap(True)
    with _suppress_exception():
        value_label.add_css_class("heading")
    card.append(title_label)
    card.append(value_label)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
        subtitle_label.set_wrap(True)
        card.append(subtitle_label)
    box.append(card)


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
    progressbar.devtool-progress trough {
      min-height: 18px;
      border-radius: 4px;
      background-color: alpha(@window_fg_color, 0.18);
    }
    progressbar.devtool-progress progress {
      min-height: 18px;
      border-radius: 4px;
      background-color: #1c71d8;
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
