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


class Gtk4CompactHud:
    """GTK4/libadwaita compact HUD renderer for Unix desktops.

    The renderer is intentionally model-driven.  It consumes the same Devtool
    HUD snapshot files and active-run pointer as the terminal, web, Qt, and
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
            cls._require_gtk4()
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
            Adw, _Gdk, GLib, Gtk = self._require_gtk4()
        except Exception as exc:  # pragma: no cover - depends on host desktop packages
            raise RuntimeError(
                "GTK4 desktop HUD requires PyGObject, GTK 4, and libadwaita. "
                "On Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
            ) from exc

        app_id = "io.github.mikeceead.DevtoolHud"
        if Adw is not None:
            application = Adw.Application(application_id=app_id)
        else:
            application = Gtk.Application(application_id=app_id)

        poll_ms = max(100, int(max(0.05, interval_s) * 1000))

        def activate(app: Any) -> None:
            if Adw is not None:
                window = Adw.ApplicationWindow(application=app)
            else:
                window = Gtk.ApplicationWindow(application=app)
            window.set_title("Devtool HUD")
            window.set_default_size(520, 330)
            with _suppress_exception():
                window.set_icon_name("devtool-hud")

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
            details = Gtk.Label(label="")
            details.set_xalign(0.0)
            details.set_wrap(True)
            details.set_selectable(True)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_button = Gtk.Button(label="Copy summary")
            log_button = Gtk.Button(label="Open log")
            close_button = Gtk.Button(label="Close")
            actions.append(copy_button)
            actions.append(log_button)
            actions.append(close_button)
            box.append(title)
            box.append(status)
            box.append(progress)
            box.append(details)
            box.append(actions)
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
                detail_lines = []
                for key in ("repo_name", "target", "phase_name", "status", "updated_at"):
                    value = state.get(key)
                    if value:
                        detail_lines.append(f"{key.replace('_', ' ')}: {value}")
                for event in events[-3:]:
                    text = event.get("message") or event.get("text") or event.get("phase") or event.get("type")
                    if text:
                        detail_lines.append(f"• {text}")
                details.set_label("\n".join(detail_lines))
                last_snapshot.clear()
                last_snapshot.update({"state": state, "events": events})
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

            copy_button.connect("clicked", copy_summary)
            log_button.connect("clicked", open_log)
            close_button.connect("clicked", lambda _button: window.close())
            read()
            GLib.timeout_add(poll_ms, lambda: (read(), True)[1])
            window.present()

        application.connect("activate", activate)
        return int(application.run([]))


class _suppress_exception:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return True
