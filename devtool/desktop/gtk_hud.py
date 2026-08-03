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
    canonical_diagnostics,
    compact_operation_title,
    completion_copy_text,
    live_issue_counts,
    operation_steps,
    primary_failure_message,
    parse_hud_state,
    state_context_label,
    state_metric_text,
    state_window_title,
)
from .hud_store import load_hud_snapshot
from .gtk_runtime import append_gtk_log, install_exception_hooks, install_gtk_thread_guard, show_startup_error_window, suppress_gtk_error, wrap_periodic_callback
from .preferences import DesktopPreferences


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

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False, startup_test: bool = False) -> int:
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
        install_exception_hooks("hud")
        install_gtk_thread_guard(Gtk, app_slug="hud")
        _install_progress_css(Gtk, Gdk)

        app_id = "io.github.mikeceead.DevtoolHud"
        if Adw is not None:
            application = Adw.Application(application_id=app_id)
        else:
            application = Gtk.Application(application_id=app_id)

        poll_ms = max(100, int(max(0.05, interval_s) * 1000))
        preferences = DesktopPreferences.load("hud")
        exit_code = 0

        def activate(app: Any) -> None:
            nonlocal exit_code
            try:
                _activate(app)
            except Exception as exc:  # pragma: no cover - host display dependent
                exit_code = 2
                log_path = append_gtk_log("hud", "HUD activation failed", exc)
                if not show_startup_error_window(
                    Gtk=Gtk,
                    Adw=Adw,
                    app=app,
                    app_slug="hud",
                    title="Devtool HUD startup error",
                    exc=exc,
                    log_path=log_path,
                ):
                    print(f"devtool-gtk-hud: {exc}", file=sys.stderr)
                    app.quit()

        def _activate(app: Any) -> None:
            if Adw is not None:
                window = Adw.ApplicationWindow(application=app)
            else:
                window = Gtk.ApplicationWindow(application=app)
            window.set_title("Devtool HUD")
            window.set_default_size(max(520, min(preferences.window_width, 900)), max(420, min(preferences.window_height, 760)))
            with _suppress_exception():
                if preferences.window_maximized:
                    window.maximize()
            _apply_appearance_preference(Adw, preferences)
            with _suppress_exception():
                window.set_icon_name("devtool-hud")
                window.set_resizable(True)
            with suppress_gtk_error():
                window.connect("close-request", lambda *_unused: (_save_window_preferences(window, preferences), False)[1])

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            with _suppress_exception():
                root.add_css_class("devtool-hud-root")
                if preferences.high_contrast:
                    root.add_css_class("devtool-high-contrast")

            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            header.set_margin_top(12)
            header.set_margin_bottom(10)
            header.set_margin_start(14)
            header.set_margin_end(14)
            with _suppress_exception():
                header.add_css_class("devtool-hud-header")
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            top_row.set_hexpand(True)
            header_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            header_copy.set_hexpand(True)
            title = Gtk.Label(label="devtool")
            title.set_xalign(0.0)
            title.set_wrap(False)
            title.set_single_line_mode(True)
            with _suppress_exception():
                from gi.repository import Pango  # type: ignore[import-not-found]
                title.set_ellipsize(Pango.EllipsizeMode.END)
                title.add_css_class("devtool-hud-title")
            status = Gtk.Label(label="waiting for devtool progress")
            status.set_xalign(0.0)
            status.set_wrap(False)
            status.set_single_line_mode(True)
            with _suppress_exception():
                from gi.repository import Pango  # type: ignore[import-not-found]
                status.set_ellipsize(Pango.EllipsizeMode.END)
                status.add_css_class("devtool-hud-subtitle")
            context = Gtk.Label(label="Waiting for target")
            context.set_xalign(0.0)
            context.set_wrap(False)
            context.set_single_line_mode(True)
            with _suppress_exception():
                from gi.repository import Pango  # type: ignore[import-not-found]
                context.set_ellipsize(Pango.EllipsizeMode.END)
                context.add_css_class("devtool-hud-context")
            status_pill = Gtk.Label(label="idle")
            status_pill.set_xalign(0.5)
            _set_accessible_label(Gtk, status_pill, "Run status")
            with _suppress_exception():
                status_pill.set_valign(Gtk.Align.CENTER)
                status_pill.set_halign(Gtk.Align.END)
                status_pill.add_css_class("devtool-hud-status-pill")
            header_copy.append(title)
            header_copy.append(status)
            header_copy.append(context)
            top_row.append(header_copy)
            top_row.append(status_pill)
            header.append(top_row)

            progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            progress_row.set_hexpand(True)
            progress = Gtk.ProgressBar()
            progress.set_hexpand(True)
            _set_accessible_label(Gtk, progress, "Overall Devtool run progress")
            with _suppress_exception():
                progress.add_css_class("devtool-progress")
            progress_label = Gtk.Label(label="idle")
            progress_label.set_xalign(1.0)
            with _suppress_exception():
                progress_label.add_css_class("devtool-hud-progress-label")
            progress_row.append(progress)
            progress_row.append(progress_label)
            header.append(progress_row)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            content.set_margin_top(12)
            content.set_margin_bottom(12)
            content.set_margin_start(14)
            content.set_margin_end(14)
            content.set_hexpand(True)
            content.set_vexpand(True)

            summary_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            with _suppress_exception():
                summary_card.add_css_class("devtool-summary-card")
            summary_title = Gtk.Label(label="Waiting for run state", xalign=0.0)
            summary_title.set_wrap(True)
            with _suppress_exception():
                summary_title.add_css_class("devtool-summary-title")
            summary_body = Gtk.Label(label="Devtool has not published a HUD snapshot yet.", xalign=0.0)
            summary_body.set_wrap(True)
            summary_body.set_selectable(True)
            summary_hint = Gtk.Label(label="", xalign=0.0)
            summary_hint.set_wrap(True)
            with _suppress_exception():
                summary_hint.add_css_class("devtool-card-subtitle")
            summary_card.append(summary_title)
            summary_card.append(summary_body)
            summary_card.append(summary_hint)
            content.append(summary_card)

            metrics = Gtk.FlowBox()
            metrics.set_selection_mode(Gtk.SelectionMode.NONE)
            metrics.set_min_children_per_line(1)
            metrics.set_max_children_per_line(4)
            metrics.set_column_spacing(8)
            metrics.set_row_spacing(8)
            content.append(metrics)

            stepper_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            with _suppress_exception():
                stepper_panel.add_css_class("devtool-stepper-panel")
            stepper_heading = Gtk.Label(label="Pipeline", xalign=0.0)
            with _suppress_exception():
                stepper_heading.add_css_class("devtool-card-value")
            stepper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            stepper_panel.append(stepper_heading)
            stepper_panel.append(stepper)
            content.append(stepper_panel)

            raw_text = Gtk.TextView()
            raw_text.set_editable(False)
            raw_text.set_cursor_visible(False)
            raw_text.set_monospace(True)
            _set_accessible_label(Gtk, raw_text, "Raw Devtool HUD event details")
            with _suppress_exception():
                raw_text.add_css_class("devtool-details")
            raw_scroller = Gtk.ScrolledWindow()
            raw_scroller.set_child(raw_text)
            raw_scroller.set_vexpand(False)
            with _suppress_exception():
                raw_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                raw_scroller.set_max_content_height(240)
            raw_expander = Gtk.Expander(label="Show raw event log")
            # Default remains collapsed: raw_expander.set_expanded(False).
            raw_expander.set_expanded(bool(preferences.hud_details_expanded))
            raw_expander.set_child(raw_scroller)
            def remember_hud_details_expanded(_expander: Any, *_unused: Any) -> None:
                preferences.hud_details_expanded = bool(raw_expander.get_expanded())
                preferences.save("hud")
            with _suppress_exception():
                raw_expander.connect("notify::expanded", remember_hud_details_expanded)
            content.append(raw_expander)

            scroller = Gtk.ScrolledWindow()
            scroller.set_child(content)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            with _suppress_exception():
                scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_margin_top(10)
            actions.set_margin_bottom(10)
            actions.set_margin_start(14)
            actions.set_margin_end(14)
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

            root.append(header)
            root.append(scroller)
            root.append(actions)
            if Adw is not None:
                window.set_content(root)
            else:
                window.set_child(root)

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
                status_text = str(state.get("status") or hud_state.status or "idle")
                if status_text.lower() == "running" and fraction <= 0.0:
                    fraction = 0.08
                display_title = compact_operation_title(hud_state) or str(state.get("display_title") or state.get("title") or "devtool")
                display_status = compact_detail_text(hud_state) or str(state.get("display_text") or state.get("text") or "waiting for devtool progress")
                display_context = state_context_label(hud_state)
                window.set_title(state_window_title(hud_state))
                title.set_label(display_title)
                status.set_label(display_status)
                context.set_label(display_context)
                with suppress_gtk_error():
                    title.set_tooltip_text(display_title)
                    status.set_tooltip_text(display_status)
                    context.set_tooltip_text(display_context)
                status_pill.set_label(_hud_status_chip_label(status_text))
                progress.set_fraction(fraction)
                progress.set_show_text(False)
                progress_label.set_label(_hud_progress_label(hud_state, state, status_text))
                _set_status_css(progress, status_text)
                _set_status_css(status_pill, status_text)

                errors, warnings = live_issue_counts(events, hud_state)
                diagnostics = canonical_diagnostics(events, hud_state)
                steps = operation_steps(hud_state, events)
                summary = _hud_primary_summary(hud_state, state, events, diagnostics, steps, errors, warnings)
                summary_title.set_label(summary[0])
                summary_body.set_label(summary[1])
                summary_hint.set_label(summary[2])
                _set_status_css(summary_card, status_text)

                _clear_flowbox(Gtk, metrics)
                _append_metric_chip(Gtk, metrics, "Errors", str(errors), "live diagnostics")
                _append_metric_chip(Gtk, metrics, "Warnings", str(warnings), "live diagnostics")
                _append_metric_chip(Gtk, metrics, "Events", str(len(events)), "recent log")
                _append_metric_chip(Gtk, metrics, "Counts", state_metric_text(hud_state, events), "canonical")

                _clear_box(Gtk, stepper)
                if steps:
                    for step in steps[:12]:
                        _append_stepper_row(Gtk, stepper, step.to_dict())
                else:
                    phases = _phase_cards_from_snapshot(state, snap.phase_highlights)
                    if not phases:
                        phases = [("status", str(state.get("status") or "idle"), str(state.get("phase_name") or state.get("phase") or ""))]
                    for name, value, subtitle in phases[:8]:
                        _append_stepper_row(Gtk, stepper, {"label": name, "status": value or "pending", "detail": subtitle, "progress": state.get("percent") or 0})

                detail_lines = ["Run", display_context, state_metric_text(hud_state, events)]
                if steps:
                    detail_lines.append("")
                    detail_lines.append("Pipeline")
                    for step in steps:
                        progress_suffix = f" ({step.progress}%)" if step.status not in {"skipped", "pending"} else ""
                        detail_lines.append(f"{_human_step_status(step.status, step.label)}: {step.label}{progress_suffix}")
                lanes = _workspace_lanes_from_steps([step.to_dict() for step in steps])
                if lanes:
                    detail_lines.append("")
                    detail_lines.append("workspace lane")
                    for lane, lane_steps in lanes.items():
                        detail_lines.append(f"{lane}: " + ", ".join(str(item.get("label") or "") for item in lane_steps))
                for key in ("repo_name", "target", "phase_name", "status", "updated_at"):
                    value = state.get(key)
                    if value:
                        detail_lines.append(f"{key.replace('_', ' ')}: {value}")
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
                raw_text.get_buffer().set_text("\n".join(detail_lines))
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

            def refresh_failed(exc: BaseException, log_path: Path) -> None:
                window.set_title("Devtool HUD refresh failed")
                status.set_label("HUD refresh failed; keeping last visible state")
                status_pill.set_label("Error")
                _set_status_css(status_pill, "failed")
                if not last_snapshot:
                    summary_title.set_label("HUD refresh failed")
                    summary_body.set_label(f"Full details were written to {log_path}")
                    raw_text.get_buffer().set_text(f"HUD startup/refresh failed. Full details: {log_path}")
                with suppress_gtk_error():
                    status.set_tooltip_text(str(log_path))
                    status_pill.set_tooltip_text(str(log_path))

            read_safe = wrap_periodic_callback(
                app_slug="hud",
                callback=lambda: bool(read()),
                on_error=refresh_failed,
            )

            def read_once() -> bool:
                read_safe()
                if startup_test:
                    GLib.timeout_add(150, app.quit)
                return False

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
            window.present()
            GLib.idle_add(read_once)
            GLib.timeout_add(poll_ms, read_safe)

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




def _clear_box(Gtk: Any, box: Any) -> None:
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()


def _append_metric_chip(Gtk: Any, box: Any, title: str, value: str, subtitle: str = "") -> None:
    chip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    chip.set_hexpand(True)
    with _suppress_exception():
        chip.add_css_class("devtool-metric-card")
    title_label = Gtk.Label(label=title.upper(), xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("devtool-card-label")
    value_label = Gtk.Label(label=value or "-", xalign=0.0)
    value_label.set_wrap(True)
    with _suppress_exception():
        value_label.add_css_class("devtool-card-value")
    chip.append(title_label)
    chip.append(value_label)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
        subtitle_label.set_wrap(True)
        with _suppress_exception():
            subtitle_label.add_css_class("devtool-card-subtitle")
        chip.append(subtitle_label)
    box.append(chip)


def _hud_status_chip_label(status: str) -> str:
    normalized = str(status or "idle").strip().lower()
    if normalized in {"ok", "success", "complete", "completed"}:
        return "✓ Complete"
    if normalized in {"failed", "error", "failure"}:
        return "● Failed"
    if normalized in {"cancelled", "canceled", "interrupted"}:
        return "■ Cancelled"
    if normalized == "running":
        return "● Running"
    return normalized.title() or "Idle"


def _hud_progress_label(hud_state: Any, state: dict[str, Any], status: str) -> str:
    current = getattr(hud_state, "current", None)
    total = getattr(hud_state, "total", None)
    if current is not None and total:
        return f"{current} / {total} tasks"
    for current_key, total_key in (("current", "total"), ("completed", "total"), ("done", "total")):
        raw_current = state.get(current_key)
        raw_total = state.get(total_key)
        if raw_current is not None and raw_total:
            return f"{raw_current} / {raw_total} tasks"
    pct = state.get("percent") or getattr(hud_state, "percent", None)
    if pct is not None:
        return f"{pct}%"
    return str(status or "idle").title()


def _human_step_status(status: str, label: str = "") -> str:
    normalized = str(status or "pending").strip().lower()
    if "rollback" in str(label or "").lower() and normalized in {"ok", "success", "complete", "completed"}:
        return "Rolled back"
    return {
        "ok": "Completed",
        "success": "Completed",
        "complete": "Completed",
        "completed": "Completed",
        "failed": "Failed",
        "error": "Failed",
        "running": "Running",
        "cancelled": "Cancelled",
        "canceled": "Cancelled",
        "pending": "Not run",
        "skipped": "Not run",
    }.get(normalized, normalized.title() or "Not run")


def _step_symbol(status: str, label: str = "") -> str:
    normalized = str(status or "pending").strip().lower()
    lower_label = label.strip().lower()
    if "rollback" in lower_label and normalized in {"ok", "success", "complete", "completed"}:
        return "↺"
    if normalized in {"ok", "success", "complete", "completed"}:
        return "✓"
    if normalized in {"failed", "error", "failure"}:
        return "×"
    if normalized == "running":
        return "●"
    if normalized in {"cancelled", "canceled", "interrupted"}:
        return "■"
    return "−"


def _append_stepper_row(Gtk: Any, box: Any, step: dict[str, Any]) -> None:
    label_text = str(step.get("label") or step.get("key") or "step")
    status_text = str(step.get("status") or "pending")
    detail_text = str(step.get("detail") or step.get("message") or "")
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
    row.set_hexpand(True)
    with _suppress_exception():
        row.add_css_class("devtool-stepper-row")
    symbol = Gtk.Label(label=_step_symbol(status_text, label_text), xalign=0.5)
    with _suppress_exception():
        symbol.add_css_class("devtool-step-symbol")
    copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    copy.set_hexpand(True)
    label = Gtk.Label(label=label_text, xalign=0.0)
    label.set_wrap(True)
    with _suppress_exception():
        label.add_css_class("devtool-step-title")
    detail = Gtk.Label(label=detail_text or _human_step_status(status_text, label_text), xalign=0.0)
    detail.set_wrap(True)
    with _suppress_exception():
        detail.add_css_class("devtool-card-subtitle")
    state = Gtk.Label(label=_human_step_status(status_text, label_text), xalign=1.0)
    with _suppress_exception():
        state.add_css_class("devtool-card-label")
    copy.append(label)
    copy.append(detail)
    row.append(symbol)
    row.append(copy)
    row.append(state)
    _set_status_css(row, status_text)
    _set_status_css(symbol, status_text)
    box.append(row)


def _hud_primary_summary(
    hud_state: Any,
    state: dict[str, Any],
    events: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    steps: list[Any],
    errors: int,
    warnings: int,
) -> tuple[str, str, str]:
    status = str(getattr(hud_state, "status", "") or state.get("status") or "idle").lower()
    if status in {"failed", "error", "failure"}:
        title = "Overlay validation failed" if str(getattr(hud_state, "command", "") or state.get("command") or "").strip() in {"apply-overlay", "apply-overlays"} else "Devtool run failed"
        message = primary_failure_message(events, hud_state)
        rollback_ok = any("rollback" in str(getattr(step, "label", "") or "").lower() and str(getattr(step, "status", "") or "").lower() in {"ok", "success", "complete", "completed"} for step in steps)
        hint = f"{errors} error(s) · {warnings} warning(s)" + (" · rollback completed successfully" if rollback_ok else "")
        return title, message, hint
    if status in {"ok", "success", "complete", "completed"}:
        return "Devtool run completed", compact_detail_text(hud_state) or str(state.get("text") or "The operation completed successfully."), f"{errors} error(s) · {warnings} warning(s)"
    if status == "running":
        return "Devtool run in progress", compact_detail_text(hud_state) or str(state.get("text") or "The operation is currently running."), state_context_label(hud_state)
    return "Waiting for Devtool", compact_detail_text(hud_state) or str(state.get("text") or "No active run is publishing HUD data yet."), state_context_label(hud_state)


def _clear_grid(Gtk: Any, grid: Any) -> None:
    child = grid.get_first_child()
    while child is not None:
        grid.remove(child)
        child = grid.get_first_child()


def _append_metric_card(Gtk: Any, grid: Any, column: int, title: str, value: str, subtitle: str = "") -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.set_hexpand(True)
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


def _apply_appearance_preference(Adw: Any, preferences: DesktopPreferences) -> None:
    if Adw is None:
        return
    with _suppress_exception():
        manager = Adw.StyleManager.get_default()
        scheme = str(preferences.appearance or "system").lower()
        if scheme == "dark":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif scheme == "light":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def _save_window_preferences(window: Any, preferences: DesktopPreferences) -> None:
    with _suppress_exception():
        width = int(window.get_width())
        height = int(window.get_height())
        if width > 0 and height > 0:
            preferences.window_width = width
            preferences.window_height = height
    with _suppress_exception():
        preferences.window_maximized = bool(window.is_maximized())
    preferences.save("hud")


def _set_accessible_label(Gtk: Any, widget: Any, label: str) -> None:
    with _suppress_exception():
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        return
    with _suppress_exception():
        widget.set_tooltip_text(label)


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
      background-color: @window_bg_color;
    }
    .devtool-hud-header {
      border-bottom: 1px solid alpha(@window_fg_color, 0.10);
    }
    .devtool-hud-title {
      font-size: 18px;
      font-weight: 800;
    }
    .devtool-hud-subtitle {
      color: alpha(@window_fg_color, 0.88);
      font-weight: 600;
    }
    .devtool-hud-context,
    .devtool-hud-progress-label,
    .devtool-card-label,
    .devtool-card-subtitle {
      color: alpha(@window_fg_color, 0.62);
    }
    .devtool-hud-status-pill {
      padding: 6px 10px;
      border-radius: 999px;
      background-color: alpha(@window_fg_color, 0.08);
      color: alpha(@window_fg_color, 0.88);
      font-weight: 800;
    }
    .devtool-hud-status-pill.devtool-progress-ok,
    .devtool-stepper-row.devtool-progress-ok .devtool-step-symbol {
      background-color: alpha(@success_color, 0.16);
      color: @success_color;
    }
    .devtool-hud-status-pill.devtool-progress-failed,
    .devtool-summary-card.devtool-progress-failed,
    .devtool-stepper-row.devtool-progress-failed .devtool-step-symbol {
      background-color: alpha(@error_color, 0.16);
      color: @error_color;
    }
    .devtool-hud-status-pill.devtool-progress-cancelled,
    .devtool-stepper-row.devtool-progress-cancelled .devtool-step-symbol {
      background-color: alpha(@warning_color, 0.16);
      color: @warning_color;
    }
    .devtool-summary-card,
    .devtool-stepper-panel,
    .devtool-metric-card {
      padding: 10px 12px;
      border-radius: 12px;
      background-color: alpha(@window_fg_color, 0.055);
      border: 1px solid alpha(@window_fg_color, 0.08);
    }
    .devtool-summary-title,
    .devtool-card-value,
    .devtool-step-title {
      color: alpha(@window_fg_color, 0.95);
      font-weight: 800;
    }
    .devtool-stepper-row {
      padding: 6px 4px;
      border-radius: 10px;
    }
    .devtool-step-symbol {
      border-radius: 999px;
      padding: 3px 7px;
      background-color: alpha(@window_fg_color, 0.09);
      font-weight: 900;
    }
    .devtool-details {
      background-color: alpha(@window_fg_color, 0.04);
      color: alpha(@window_fg_color, 0.88);
    }
    .devtool-action-row {
      border-top: 1px solid alpha(@window_fg_color, 0.10);
      background-color: alpha(@window_bg_color, 0.96);
    }
    progressbar.devtool-progress trough {
      min-height: 10px;
      border-radius: 999px;
      background-color: alpha(@window_fg_color, 0.14);
    }
    progressbar.devtool-progress progress {
      min-height: 10px;
      border-radius: 999px;
      background-color: @accent_bg_color;
    }
    progressbar.devtool-progress.devtool-progress-ok progress {
      background-color: @success_color;
    }
    progressbar.devtool-progress.devtool-progress-failed progress {
      background-color: @error_color;
    }
    progressbar.devtool-progress.devtool-progress-cancelled progress {
      background-color: @warning_color;
    }
    .devtool-high-contrast {
      border: 2px solid @window_fg_color;
    }
    .devtool-high-contrast .devtool-hud-status-pill,
    .devtool-high-contrast .devtool-summary-card {
      border: 1px solid @window_fg_color;
      background-color: @window_bg_color;
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
