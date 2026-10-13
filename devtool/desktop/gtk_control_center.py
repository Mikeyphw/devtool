from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..tasker import DEFAULT_TASKER_DIR
from ..ui_core.actions import action_text
from ..ui_core.navigation import navigation_icon_map, navigation_route_map
from ..ui_core.preferences import resolve_user_ui_preferences
from ..ui_core.responsive import resolve_responsive_layout
from .control_center_model import DesktopControlCenterModel
from .ui_semantics import desktop_action, primary_desktop_action
from .repository_store import DesktopRepositoryStore
from .run_workflow import payload_from_mapping, review_payload, workflow_snapshot
from .gtk_runtime import append_gtk_log, install_exception_hooks, install_gtk_thread_guard, redact_text, show_startup_error_window, suppress_gtk_error, wrap_periodic_callback
from .preferences import DesktopPreferences
from .run_process import DesktopRunHandle, DesktopRunLauncher



def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, payload: object, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp_name = handle.name
            os.chmod(tmp_name, mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, mode)
        _fsync_directory(path.parent)
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass

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
            "GTK4 Control Center could not initialize the desktop display. "
            "Check DISPLAY/WAYLAND_DISPLAY and X11/Wayland access."
        )


class Gtk4ControlCenter:
    """GTK4/libadwaita Control Center shell.

    The GTK implementation keeps Qt removed but preserves the old desktop app's
    richer surfaces: dashboard cards, active-run progress, command workflow,
    artifact history, diagnostics, repositories, targets, and HUD settings.
    """

    def __init__(self, *, history_limit: int = 25, recent_events: int = 8) -> None:
        self.history_limit = max(0, int(history_limit))
        self.recent_events = max(0, int(recent_events))

    @staticmethod
    def _require_gtk4() -> tuple[Any, Any, Any, Any]:
        import gi  # type: ignore[import-not-found]

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gio", "2.0")
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw  # type: ignore[import-not-found]
        except (ValueError, ImportError):
            Adw = None
        from gi.repository import Gio, GLib, Gtk  # type: ignore[import-not-found]

        return Adw, Gio, GLib, Gtk

    @classmethod
    def available(cls) -> bool:
        if not _has_display():
            return False
        try:
            import importlib.util

            if importlib.util.find_spec("gi") is None:
                return False
            _Adw, _Gio, _GLib, Gtk = cls._require_gtk4()
            if not _gtk_can_initialize(Gtk):
                return False
        except Exception:
            return False
        return True

    def snapshot(
        self,
        *,
        repository: Path | str,
        config_path: str | None = None,
        target: str = "",
        variant: str = "",
        state_dir: Path | str = DEFAULT_TASKER_DIR,
        follow_active: bool = False,
    ) -> dict[str, Any]:
        return DesktopControlCenterModel(
            repository=repository,
            config_path=config_path,
            target=target,
            variant=variant,
            state_dir=state_dir,
            follow_active=follow_active,
            history_limit=self.history_limit,
            recent_events=self.recent_events,
        ).snapshot().as_dict()

    def run(
        self,
        *,
        repository: Path | str,
        config_path: str | None = None,
        target: str = "",
        variant: str = "",
        state_dir: Path | str = DEFAULT_TASKER_DIR,
        follow_active: bool = False,
        once: bool = False,
        page: str = "overview",
        interval_s: float = 1.0,
        startup_test: bool = False,
    ) -> int:
        if once:
            payload = self.snapshot(
                repository=repository,
                config_path=config_path,
                target=target,
                variant=variant,
                state_dir=state_dir,
                follow_active=follow_active,
            )
            payload["renderer"] = "gtk4-control-center"
            payload["run_workflow"] = workflow_snapshot(payload).as_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if not _has_display():
            raise RuntimeError("GTK4 Control Center requires DISPLAY or WAYLAND_DISPLAY.")
        try:
            Adw, Gio, GLib, Gtk = self._require_gtk4()
        except Exception as exc:  # pragma: no cover - depends on host desktop packages
            raise RuntimeError(
                "GTK4 Control Center requires PyGObject, GTK 4, and libadwaita. "
                "On Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
            ) from exc
        _require_gtk_initialized(Gtk)
        install_exception_hooks("control-center")
        install_gtk_thread_guard(Gtk, app_slug="control-center")

        app_id = "io.github.mikeceead.DevtoolControlCenter"
        application = Adw.Application(application_id=app_id) if Adw is not None else Gtk.Application(application_id=app_id)
        poll_ms = max(250, int(max(0.25, interval_s) * 1000))
        preferences = DesktopPreferences.load("control-center")
        selected_page = page if page in _PAGE_KEYS else "overview"
        if page == "overview" and preferences.selected_page in _PAGE_KEYS:
            selected_page = preferences.selected_page
        run_launcher = DesktopRunLauncher()
        exit_code = 0

        def activate(app: Any) -> None:
            nonlocal exit_code
            try:
                _activate(app)
            except Exception as exc:  # pragma: no cover - host display dependent
                exit_code = 2
                log_path = append_gtk_log("control-center", "Control Center activation failed", exc)
                if not show_startup_error_window(
                    Gtk=Gtk,
                    Adw=Adw,
                    app=app,
                    app_slug="control-center",
                    title="Devtool Control Center startup error",
                    exc=exc,
                    log_path=log_path,
                ):
                    print(f"devtool-control-center: {exc}", file=sys.stderr)
                    app.quit()

        def _activate(app: Any) -> None:
            ui_preferences = preferences.resolved_ui_preferences()
            if Adw is not None:
                window = Adw.ApplicationWindow(application=app)
            else:
                window = Gtk.ApplicationWindow(application=app)
            window.set_title("Devtool Control Center")
            window.set_default_size(preferences.window_width, preferences.window_height)
            with _suppress_exception():
                if preferences.window_maximized:
                    window.maximize()
            _apply_appearance_preference(Adw, preferences)
            with _suppress_exception():
                window.set_icon_name("io.github.mikeceead.DevtoolControlCenter")

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            with _suppress_exception():
                root.add_css_class("devtool-app-root")
                if preferences.high_contrast:
                    root.add_css_class("devtool-high-contrast")
                if ui_preferences.density in {"dense", "compact"}:
                    root.add_css_class("devtool-compact-density")
            _install_control_center_css(Gtk, window)
            header = Gtk.HeaderBar()
            with _suppress_exception():
                header.add_css_class("devtool-app-header")
            header_title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            title = Gtk.Label(label="Devtool")
            title.set_xalign(0.0)
            with _suppress_exception():
                title.add_css_class("devtool-app-title")
            repo_status = Gtk.Label(label="Loading repository state")
            repo_status.set_xalign(0.0)
            with _suppress_exception():
                repo_status.add_css_class("devtool-app-context")
            header_title.append(title)
            header_title.append(repo_status)
            header.set_title_widget(header_title)
            ipc_status = Gtk.Label(label="IPC idle")
            with _suppress_exception():
                ipc_status.add_css_class("devtool-status-pill")
            navigation_button = Gtk.ToggleButton(label="Compact nav")
            navigation_button.set_active(bool(preferences.compact_navigation))
            hud_button = Gtk.Button(label="Open HUD")
            refresh_button = Gtk.Button(label="Refresh")
            _style_action_button(navigation_button)
            _style_action_button(hud_button, primary=True)
            _style_action_button(refresh_button)
            header.pack_start(navigation_button)
            header.pack_end(refresh_button)
            header.pack_end(hud_button)
            header.pack_end(ipc_status)
            root.append(header)

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            body.set_vexpand(True)
            with _suppress_exception():
                body.add_css_class("devtool-body")
            nav = Gtk.ListBox()
            nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
            nav.set_size_request(190, -1)
            with _suppress_exception():
                nav.add_css_class("navigation-sidebar")
            stack = Gtk.Stack()
            stack.set_hexpand(True)
            stack.set_vexpand(True)
            with _suppress_exception():
                stack.add_css_class("devtool-content-stack")
            pages: dict[str, Any] = {}
            nav_rows: dict[str, Any] = {}
            nav_labels: dict[str, Any] = {}
            for key, label in _PAGE_KEYS.items():
                row = Gtk.ListBoxRow()
                row_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                icon = Gtk.Label(label=_PAGE_ICONS.get(key, "•"), xalign=0.5)
                nav_label = Gtk.Label(label=label, xalign=0.0)
                nav_label.set_hexpand(True)
                with _suppress_exception():
                    icon.add_css_class("devtool-nav-icon")
                    nav_label.add_css_class("devtool-nav-label")
                row_content.append(icon)
                row_content.append(nav_label)
                row.set_child(row_content)
                row.set_name(key)
                nav.append(row)
                nav_rows[key] = row
                nav_labels[key] = nav_label
                row.set_tooltip_text(label)
                page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                page_box.set_margin_top(24)
                page_box.set_margin_bottom(24)
                page_box.set_margin_start(24)
                page_box.set_margin_end(24)
                page_box.set_hexpand(True)
                page_box.set_vexpand(True)
                with _suppress_exception():
                    page_box.add_css_class("devtool-page")
                scroller = Gtk.ScrolledWindow()
                scroller.set_child(page_box)
                scroller.set_hexpand(True)
                scroller.set_vexpand(True)
                with _suppress_exception():
                    scroller.add_css_class("devtool-page-scroller")
                    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                stack.add_titled(scroller, key, label)
                pages[key] = page_box
            body.append(nav)
            body.append(stack)
            root.append(body)
            if Adw is not None:
                window.set_content(root)
            else:
                window.set_child(root)

            navigation_state = {"syncing": False, "manual": False}

            def apply_compact_navigation(compact: bool, *, persist: bool = True) -> None:
                compact = bool(compact)
                nav.set_size_request(66 if compact else 190, -1)
                for label_widget in nav_labels.values():
                    label_widget.set_visible(not compact)
                _set_css_class(nav, "devtool-navigation-compact", compact)
                if navigation_button.get_active() != compact:
                    navigation_state["syncing"] = True
                    navigation_button.set_active(compact)
                    navigation_state["syncing"] = False
                navigation_button.set_label("Expand nav" if compact else "Compact nav")
                if persist:
                    preferences.compact_navigation = compact
                    preferences.save("control-center")

            initial_ui_preferences = resolve_user_ui_preferences(surface="desktop")
            initial_layout = resolve_responsive_layout("desktop", width=preferences.window_width, height=preferences.window_height, preferences=initial_ui_preferences)
            apply_compact_navigation(bool(preferences.compact_navigation or initial_layout.panes == 1), persist=False)

            def navigation_toggled(button: Any) -> None:
                if navigation_state["syncing"]:
                    return
                navigation_state["manual"] = True
                apply_compact_navigation(bool(button.get_active()))

            def sync_navigation_width() -> bool:
                if not navigation_state["manual"]:
                    with _suppress_exception():
                        responsive_layout = resolve_responsive_layout(
                            "desktop", width=window.get_width(), height=window.get_height(), preferences=initial_ui_preferences
                        )
                        apply_compact_navigation(bool(preferences.compact_navigation or responsive_layout.panes == 1), persist=False)
                return not bool(refresh_state.get("closed"))

            def current_snapshot() -> dict[str, Any]:
                return self.snapshot(
                    repository=repository,
                    config_path=config_path,
                    target=target,
                    variant=variant,
                    state_dir=state_dir,
                    follow_active=follow_active,
                )

            last_payload: dict[str, Any] = {}
            dirty_pages: set[str] = set(_PAGE_KEYS)
            refresh_state: dict[str, Any] = {
                "in_flight": False,
                "queued": False,
                "closed": False,
                "token": 0,
            }

            def visible_page() -> str:
                name = stack.get_visible_child_name()
                return str(name or selected_page or "overview")

            def render_visible_page(*, stale: bool = False) -> None:
                page_name = visible_page()
                if not last_payload:
                    _render_loading_page(Gtk, pages[page_name], page_name, "Loading repository, history, IPC, and Git state…")
                    return
                _render_control_center_page(Gtk, pages, page_name, last_payload, stale=stale, run_launcher=run_launcher, GLib=GLib)
                dirty_pages.discard(page_name)

            def refresh_failed(exc: BaseException, log_path: Path) -> None:
                label = "Refresh failed; showing stale data" if last_payload else f"Refresh failed; see {log_path}"
                repo_status.set_label(label)
                ipc_status.set_label("UI error")
                if not last_payload:
                    _render_loading_page(Gtk, pages[visible_page()], visible_page(), f"Refresh failed. Full details: {log_path}")
                else:
                    render_visible_page(stale=True)
                with suppress_gtk_error():
                    repo_status.set_tooltip_text(str(log_path))
                    ipc_status.set_tooltip_text(str(log_path))

            def finish_refresh(payload: dict[str, Any], token: int) -> bool:
                if refresh_state.get("closed") or token != refresh_state.get("token"):
                    return False
                last_payload.clear()
                last_payload.update(payload)
                dirty_pages.update(_PAGE_KEYS)
                _update_header(repo_status, ipc_status, payload)
                render_visible_page()
                refresh_state["in_flight"] = False
                refresh_button.set_sensitive(True)
                if refresh_state.pop("queued", False):
                    GLib.idle_add(lambda: (request_refresh("coalesced"), False)[1])
                return False

            def fail_refresh(exc: BaseException, token: int) -> bool:
                if refresh_state.get("closed") or token != refresh_state.get("token"):
                    return False
                path = append_gtk_log("control-center", "Control Center background refresh failed", exc)
                refresh_state["in_flight"] = False
                refresh_button.set_sensitive(True)
                refresh_failed(exc, path)
                if refresh_state.pop("queued", False):
                    GLib.idle_add(lambda: (request_refresh("coalesced-after-error"), False)[1])
                return False

            def refresh_worker(token: int) -> None:
                try:
                    payload = current_snapshot()
                except Exception as exc:  # pragma: no cover - worker scheduling is display dependent
                    GLib.idle_add(fail_refresh, exc, token)
                    return
                GLib.idle_add(finish_refresh, payload, token)

            def request_refresh(reason: str = "manual") -> bool:
                if refresh_state.get("closed"):
                    return False
                if refresh_state.get("in_flight"):
                    refresh_state["queued"] = True
                    repo_status.set_label("Refresh queued; current snapshot still loading")
                    return True
                refresh_state["token"] = int(refresh_state.get("token") or 0) + 1
                token = int(refresh_state["token"])
                refresh_state["in_flight"] = True
                refresh_state["queued"] = False
                refresh_button.set_sensitive(False)
                repo_status.set_label("Refreshing repository state…" if last_payload else "Loading repository state…")
                thread = threading.Thread(
                    target=refresh_worker,
                    args=(token,),
                    name=f"devtool-control-center-refresh-{token}",
                    daemon=True,
                )
                thread.start()
                return True

            render_safe = wrap_periodic_callback(
                app_slug="control-center",
                callback=request_refresh,
                on_error=refresh_failed,
            )

            def on_row_selected(_box: Any, row: Any) -> None:
                if row is None:
                    return
                name = row.get_name() or "overview"
                stack.set_visible_child_name(name)
                preferences.selected_page = name
                preferences.save("control-center")
                if name in dirty_pages or not last_payload:
                    render_visible_page(stale=bool(last_payload and refresh_state.get("in_flight")))

            def on_close_request(*_unused: Any) -> bool:
                refresh_state["closed"] = True
                refresh_state["token"] = int(refresh_state.get("token") or 0) + 1
                _save_window_preferences(window, stack, preferences)
                return False

            nav.connect("row-selected", on_row_selected)
            with suppress_gtk_error():
                window.connect("close-request", on_close_request)
            refresh_button.connect("clicked", lambda _button: render_safe())
            navigation_button.connect("toggled", navigation_toggled)
            hud_button.connect("clicked", lambda _button: _launch_hud(state_dir, follow_active))
            if selected_page in nav_rows:
                nav.select_row(nav_rows[selected_page])
            stack.set_visible_child_name(selected_page)
            _render_loading_page(Gtk, pages[selected_page], selected_page, "Loading repository, history, IPC, and Git state…")
            _install_navigation_shortcuts(Gtk, window, stack, nav, nav_rows)
            _install_app_shortcuts(Gtk, window, refresh_button)
            for widget, label in ((navigation_button, "Toggle compact navigation"), (refresh_button, "Refresh repository state"), (hud_button, "Open Devtool desktop HUD"), (ipc_status, "IPC status")):
                _set_accessible_label(Gtk, widget, label)
            window.present()
            GLib.idle_add(lambda: (request_refresh("startup"), False)[1])
            if startup_test:
                GLib.timeout_add(150, app.quit)
            GLib.timeout_add(poll_ms, render_safe)
            GLib.timeout_add(300, sync_navigation_width)

        application.connect("activate", activate)
        app_rc = int(application.run([]))
        return exit_code or app_rc



# Compatibility mirror for older desktop integrations and source-contract checks.
# Rendering still resolves labels from the central UI Core navigation registry.
_LEGACY_PAGE_LABELS = {
    "overview": "Overview",
    "runs": "Active runs",
    "history": "History",
    "artifacts": "Artifacts",
    "diagnostics": "Diagnostics",
    "repositories": "Repository",
    "targets": "Targets",
    "run": "Run",
    "settings": "Settings",
}

_PAGE_KEYS = navigation_route_map("desktop")

_PAGE_ICONS = navigation_icon_map("desktop", mode=resolve_user_ui_preferences(surface="desktop").icons)


def _update_header(repo_status: Any, ipc_status: Any, payload: dict[str, Any]) -> None:
    repo = payload.get("repository", {}) if isinstance(payload.get("repository"), dict) else {}
    project = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
    repo_bits = [
        str(Path(str(repo.get("path") or ".")).name),
        str(project.get("selected_target") or "auto"),
        "dirty" if repo.get("dirty") else "clean",
    ]
    repo_status.set_label(" · ".join(bit for bit in repo_bits if bit))
    ipc = payload.get("ipc", {}) if isinstance(payload.get("ipc"), dict) else {}
    ipc_status.set_label("IPC connected" if ipc.get("ok") else "IPC fallback")


def _launch_hud(state_dir: Path | str, follow_active: bool) -> None:
    launcher = shutil.which("devtool-control-center-hud") or shutil.which("devtool")
    if not launcher:
        return
    argv = [launcher]
    if Path(launcher).name == "devtool":
        argv.extend(["hud", "--mode", "desktop-window"])
    argv.extend(["--state-dir", str(state_dir)])
    if follow_active:
        argv.append("--follow-active")
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _install_control_center_css(Gtk: Any, window: Any) -> None:
    css = b"""
    .devtool-app-root,
    .devtool-content-stack,
    .devtool-page-scroller,
    .devtool-page {
      background-color: transparent;
    }
    .devtool-app-header {
      min-height: 58px;
      background-color: transparent;
      border: none;
      box-shadow: inset 0 -1px alpha(currentColor, 0.07);
    }
    .devtool-app-title {
      font-size: 18px;
      font-weight: 850;
    }
    .devtool-app-context,
    .devtool-body-copy,
    .devtool-section-subtitle,
    .devtool-metric-subtitle,
    .devtool-inline-status {
      color: alpha(currentColor, 0.62);
    }
    .navigation-sidebar {
      padding: 12px 8px;
      background-color: alpha(currentColor, 0.025);
      border: none;
      box-shadow: inset -1px 0 alpha(currentColor, 0.07);
    }
    .navigation-sidebar row {
      margin: 2px 4px;
      padding: 9px 10px;
      border-radius: 8px;
    }
    .navigation-sidebar row:hover {
      background-color: alpha(currentColor, 0.055);
    }
    .navigation-sidebar row:selected {
      background-color: rgba(53, 132, 228, 0.14);
      color: currentColor;
      box-shadow: inset 3px 0 rgb(53, 132, 228);
    }
    .devtool-nav-icon {
      min-width: 18px;
      color: alpha(currentColor, 0.56);
      font-weight: 850;
    }
    .navigation-sidebar row:selected .devtool-nav-icon {
      color: rgb(53, 132, 228);
    }
    .devtool-nav-label {
      font-weight: 680;
    }
    .devtool-navigation-compact {
      padding-left: 6px;
      padding-right: 6px;
    }
    .devtool-navigation-compact row {
      padding-left: 9px;
      padding-right: 9px;
    }
    .devtool-navigation-compact .devtool-nav-icon {
      min-width: 24px;
    }
    .devtool-page-title {
      font-size: 26px;
      font-weight: 850;
      margin-bottom: 2px;
    }
    .devtool-section-title {
      font-size: 15px;
      font-weight: 800;
    }
    .devtool-surface {
      padding: 16px;
      border: none;
      border-radius: 12px;
      background-color: alpha(currentColor, 0.045);
    }
    .devtool-surface-compact {
      padding: 12px;
      border-radius: 10px;
    }
    .devtool-accent-surface {
      background-color: rgba(53, 132, 228, 0.10);
      box-shadow: inset 3px 0 rgb(53, 132, 228);
    }
    .devtool-danger-surface {
      background-color: rgba(192, 28, 40, 0.09);
      box-shadow: inset 3px 0 rgb(192, 28, 40);
    }
    .devtool-section-heading {
      margin-bottom: 8px;
    }
    .devtool-metric-strip {
      padding: 0;
      border: none;
      border-radius: 12px;
      background-color: alpha(currentColor, 0.045);
    }
    .devtool-metric-strip flowboxchild {
      padding: 0;
      margin: 0;
      background-color: transparent;
      border-radius: 0;
    }
    .devtool-metric-strip flowboxchild + flowboxchild {
      box-shadow: inset 1px 0 alpha(currentColor, 0.07);
    }
    .devtool-metric {
      padding: 14px 16px;
      min-width: 140px;
      background-color: transparent;
    }
    .devtool-metric-label,
    .devtool-data-key {
      color: alpha(currentColor, 0.58);
      font-size: 12px;
      font-weight: 700;
    }
    .devtool-metric-value {
      font-size: 21px;
      font-weight: 850;
    }
    .devtool-data-group {
      border-radius: 10px;
      background-color: alpha(currentColor, 0.035);
    }
    .devtool-data-row {
      padding: 10px 12px;
      box-shadow: inset 0 -1px alpha(currentColor, 0.06);
    }
    .devtool-data-row-last {
      box-shadow: none;
    }
    .devtool-data-value {
      color: alpha(currentColor, 0.92);
    }
    .devtool-status-pill {
      padding: 5px 9px;
      border: none;
      border-radius: 7px;
      background-color: alpha(currentColor, 0.065);
      font-weight: 760;
    }
    .devtool-action-row {
      margin-top: 2px;
    }
    button.devtool-flat-button,
    button.devtool-primary-button,
    button.devtool-danger-button {
      min-height: 36px;
      padding: 0 12px;
      border: none;
      border-radius: 8px;
      box-shadow: none;
    }
    button.devtool-flat-button {
      background-color: alpha(currentColor, 0.065);
    }
    button.devtool-flat-button:hover {
      background-color: alpha(currentColor, 0.10);
    }
    button.devtool-primary-button {
      background-color: rgb(53, 132, 228);
      color: currentColor;
    }
    button.devtool-danger-button {
      background-color: rgba(192, 28, 40, 0.13);
      color: rgb(192, 28, 40);
    }
    entry,
    combobox button,
    searchentry entry {
      min-height: 36px;
      border: none;
      border-radius: 8px;
      background-color: alpha(currentColor, 0.055);
      box-shadow: none;
    }
    entry:focus,
    combobox button:focus,
    searchentry entry:focus {
      box-shadow: 0 0 0 3px rgba(53, 132, 228, 0.18);
    }
    checkbutton {
      padding: 8px 10px;
      border-radius: 8px;
      background-color: alpha(currentColor, 0.045);
    }
    textview.devtool-command-preview,
    textview.devtool-run-output {
      padding: 12px;
      border: none;
      border-radius: 8px;
      background-color: alpha(currentColor, 0.055);
    }
    .devtool-command-workspace {
      min-height: 560px;
    }
    paned.devtool-command-workspace > separator {
      min-width: 1px;
      margin: 0 4px;
      background-color: alpha(currentColor, 0.07);
    }
    notebook {
      border: none;
      background-color: transparent;
    }
    notebook > header {
      border: none;
      border-radius: 8px;
      background-color: alpha(currentColor, 0.045);
    }
    notebook > header tabs tab {
      min-height: 32px;
      padding: 0 10px;
      border: none;
      border-radius: 7px;
    }
    notebook > header tabs tab:checked {
      background-color: rgba(53, 132, 228, 0.13);
    }
    .devtool-composer-column {
      padding: 0 6px;
    }
    .devtool-composer-column-left {
      padding-right: 12px;
    }
    .devtool-composer-column-right {
      padding-left: 12px;
    }
    .devtool-options-flow flowboxchild {
      margin: 0;
      padding: 2px;
    }
    .devtool-list {
      margin-top: 2px;
    }
    .devtool-diagnostic-error {
      background-color: rgba(192, 28, 40, 0.09);
      box-shadow: inset 3px 0 rgb(192, 28, 40);
    }
    .devtool-diagnostic-warning {
      background-color: rgba(229, 165, 10, 0.09);
      box-shadow: inset 3px 0 rgb(229, 165, 10);
    }
    progressbar.devtool-progress trough {
      min-height: 10px;
      border: none;
      border-radius: 999px;
      background-color: alpha(currentColor, 0.12);
    }
    progressbar.devtool-progress progress {
      min-height: 10px;
      border: none;
      border-radius: 999px;
      background-color: rgb(53, 132, 228);
    }
    .devtool-stale-banner {
      padding: 11px 13px;
      border: none;
      border-radius: 9px;
      background-color: rgba(229, 165, 10, 0.12);
      box-shadow: inset 3px 0 rgb(229, 165, 10);
    }
    .devtool-compact-density .devtool-surface {
      padding: 11px;
    }
    .devtool-compact-density .devtool-metric {
      padding: 10px 12px;
    }
    .devtool-compact-density .devtool-data-row {
      padding: 7px 10px;
    }
    .devtool-compact-density button.devtool-flat-button,
    .devtool-compact-density button.devtool-primary-button,
    .devtool-compact-density button.devtool-danger-button {
      min-height: 32px;
    }
    .devtool-high-contrast {
      border: 2px solid currentColor;
    }
    .devtool-high-contrast .devtool-surface,
    .devtool-high-contrast .devtool-status-pill {
      border: 1px solid currentColor;
      background-color: transparent;
    }
    """
    with _suppress_exception():
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _install_navigation_shortcuts(Gtk: Any, window: Any, stack: Any, nav: Any, nav_rows: dict[str, Any]) -> None:
    keys = list(_PAGE_KEYS)
    with _suppress_exception():
        controller = Gtk.ShortcutController()
        for index, key in enumerate(keys[:9], start=1):
            for prefix in ("<Control>", "<Alt>"):
                trigger = Gtk.ShortcutTrigger.parse_string(f"{prefix}{index}")
                action = Gtk.CallbackAction.new(lambda _widget, _args, page=key: (_select_page(stack, nav, nav_rows, page), True)[1])
                controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        trigger = Gtk.ShortcutTrigger.parse_string("F9")
        action = Gtk.CallbackAction.new(lambda _widget, _args: (_select_page(stack, nav, nav_rows, "runs"), True)[1])
        controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        window.add_controller(controller)


def _select_page(stack: Any, nav: Any, nav_rows: dict[str, Any], page: str) -> None:
    if page in nav_rows:
        nav.select_row(nav_rows[page])
    stack.set_visible_child_name(page)


def _apply_appearance_preference(Adw: Any, preferences: DesktopPreferences) -> None:
    if Adw is None:
        return
    with _suppress_exception():
        manager = Adw.StyleManager.get_default()
        scheme = str(preferences.resolved_ui_preferences().theme or "auto").lower()
        if scheme == "dark":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif scheme == "light":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def _save_window_preferences(window: Any, stack: Any, preferences: DesktopPreferences) -> None:
    with _suppress_exception():
        width = int(window.get_width())
        height = int(window.get_height())
        if width > 0 and height > 0:
            preferences.window_width = width
            preferences.window_height = height
    with _suppress_exception():
        preferences.window_maximized = bool(window.is_maximized())
    with _suppress_exception():
        page = str(stack.get_visible_child_name() or preferences.selected_page or "overview")
        if page in _PAGE_KEYS:
            preferences.selected_page = page
    preferences.save("control-center")


def _set_accessible_label(Gtk: Any, widget: Any, label: str) -> None:
    # GTK 4 exposes AccessibleProperty.LABEL; older introspection builds may not.
    with _suppress_exception():
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
        return
    with _suppress_exception():
        widget.set_tooltip_text(label)


def _set_css_class(widget: Any, class_name: str, enabled: bool) -> None:
    with _suppress_exception():
        if enabled:
            widget.add_css_class(class_name)
        else:
            widget.remove_css_class(class_name)


def _install_app_shortcuts(Gtk: Any, window: Any, refresh_button: Any) -> None:
    with _suppress_exception():
        controller = Gtk.ShortcutController()
        for accelerator, callback in (
            ("<Control>r", lambda _widget, _args: (refresh_button.emit("clicked"), True)[1]),
            ("F5", lambda _widget, _args: (refresh_button.emit("clicked"), True)[1]),
            ("<Control>q", lambda _widget, _args: (window.close(), True)[1]),
            ("Escape", lambda _widget, _args: (window.close(), True)[1]),
        ):
            trigger = Gtk.ShortcutTrigger.parse_string(accelerator)
            action = Gtk.CallbackAction.new(callback)
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))
        window.add_controller(controller)


def _prefers_reduced_motion(Gtk: Any) -> bool:
    if os.environ.get("DEVTOOL_REDUCED_MOTION", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    with _suppress_exception():
        if DesktopPreferences.load("control-center").resolved_ui_preferences().motion == "reduced":
            return True
    with _suppress_exception():
        settings = Gtk.Settings.get_default()
        return not bool(settings.get_property("gtk-enable-animations"))
    return False


def _render_loading_page(Gtk: Any, box: Any, page: str, message: str) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, _PAGE_KEYS.get(page, page.title()))
    spinner = Gtk.Spinner()
    if not _prefers_reduced_motion(Gtk):
        with _suppress_exception():
            spinner.start()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.append(spinner)
    label = Gtk.Label(label=message, xalign=0.0)
    label.set_wrap(True)
    row.append(label)
    box.append(row)


def _append_stale_banner(Gtk: Any, box: Any) -> None:
    banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    banner.set_margin_top(8)
    banner.set_margin_bottom(8)
    with _suppress_exception():
        banner.add_css_class("devtool-stale-banner")
    label = Gtk.Label(label="Showing last good snapshot. A background refresh failed.", xalign=0.0)
    label.set_wrap(True)
    banner.append(label)
    box.prepend(banner)


def _render_control_center_page(
    Gtk: Any,
    pages: dict[str, Any],
    page: str,
    payload: dict[str, Any],
    *,
    stale: bool = False,
    run_launcher: DesktopRunLauncher | None = None,
    GLib: Any | None = None,
) -> None:
    target = pages.get(page)
    if target is None:
        return
    if page == "overview":
        _render_overview(Gtk, target, payload)
    elif page == "runs":
        _render_runs(Gtk, target, payload)
    elif page == "history":
        _render_history(Gtk, target, payload)
    elif page == "artifacts":
        _render_artifacts(Gtk, target, payload)
    elif page == "diagnostics":
        _render_diagnostics(Gtk, target, payload)
    elif page == "repositories":
        _render_repositories(Gtk, target, payload)
    elif page == "targets":
        _render_targets(Gtk, target, payload)
    elif page == "run":
        _render_run_workflow(Gtk, target, payload, run_launcher=run_launcher, GLib=GLib)
    elif page == "settings":
        _render_settings(Gtk, target, payload)
    else:
        _render_loading_page(Gtk, target, page, "Unknown Control Center page.")
    if stale:
        _append_stale_banner(Gtk, target)


def _clear(Gtk: Any, box: Any) -> None:
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()


def _append_heading(Gtk: Any, box: Any, text: str) -> None:
    label = Gtk.Label(label=text, xalign=0.0)
    with _suppress_exception():
        label.add_css_class("devtool-page-title")
    box.append(label)


def _append_subheading(Gtk: Any, box: Any, text: str) -> None:
    label = Gtk.Label(label=text, xalign=0.0)
    with _suppress_exception():
        label.add_css_class("devtool-section-title")
    box.append(label)


def _append_text(Gtk: Any, box: Any, text: str) -> None:
    label = Gtk.Label(label=text, xalign=0.0)
    label.set_wrap(True)
    label.set_selectable(True)
    with _suppress_exception():
        label.add_css_class("devtool-body-copy")
    box.append(label)


def _new_surface(Gtk: Any, *, accent: bool = False, compact: bool = False, danger: bool = False) -> Any:
    surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    surface.set_hexpand(True)
    with _suppress_exception():
        surface.add_css_class("devtool-surface")
        if accent:
            surface.add_css_class("devtool-accent-surface")
        if compact:
            surface.add_css_class("devtool-surface-compact")
        if danger:
            surface.add_css_class("devtool-danger-surface")
    return surface


def _append_section_heading(Gtk: Any, box: Any, title: str, subtitle: str = "") -> None:
    heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    with _suppress_exception():
        heading.add_css_class("devtool-section-heading")
    title_label = Gtk.Label(label=title, xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("devtool-section-title")
    heading.append(title_label)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
        subtitle_label.set_wrap(True)
        with _suppress_exception():
            subtitle_label.add_css_class("devtool-section-subtitle")
        heading.append(subtitle_label)
    box.append(heading)


def _append_card(Gtk: Any, box: Any, title: str, value: str, subtitle: str = "") -> None:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    card.set_hexpand(True)
    with _suppress_exception():
        card.add_css_class("devtool-metric")
    title_label = Gtk.Label(label=title, xalign=0.0)
    with _suppress_exception():
        title_label.add_css_class("devtool-metric-label")
    value_label = Gtk.Label(label=value or "-", xalign=0.0)
    value_label.set_wrap(True)
    with _suppress_exception():
        value_label.add_css_class("devtool-metric-value")
    card.append(title_label)
    card.append(value_label)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle, xalign=0.0)
        subtitle_label.set_wrap(True)
        with _suppress_exception():
            subtitle_label.add_css_class("devtool-metric-subtitle")
        card.append(subtitle_label)
    box.append(card)


def _append_progress(Gtk: Any, box: Any, percent: Any, status: str = "") -> None:
    bar = Gtk.ProgressBar()
    with _suppress_exception():
        bar.add_css_class("devtool-progress")
    try:
        value = max(0.0, min(1.0, float(percent) / 100.0)) if percent is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    bar.set_fraction(value)
    bar.set_show_text(True)
    bar.set_text(f"{int(round(value * 100))}%" if percent is not None else (status or "idle"))
    box.append(bar)


def _append_run_card(Gtk: Any, box: Any, run: dict[str, Any]) -> None:
    surface = _new_surface(Gtk, accent=str(run.get("status") or "").lower() == "running")
    _append_section_heading(Gtk, surface, str(run.get("title") or "devtool"), str(run.get("text") or run.get("detail_text") or "Waiting for progress"))
    _append_progress(Gtk, surface, run.get("percent"), str(run.get("status") or "idle"))
    _append_metric_strip(Gtk, surface, [
        ("Status", str(run.get("status") or "idle"), str(run.get("phase") or "")),
        ("Errors", str(run.get("errors") or 0), "live diagnostics"),
        ("Warnings", str(run.get("warnings") or 0), "live diagnostics"),
        ("Counts", str(run.get("metric_text") or "-"), str(run.get("context") or "")),
    ])
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    if steps:
        strip = Gtk.FlowBox()
        strip.set_selection_mode(Gtk.SelectionMode.NONE)
        strip.set_min_children_per_line(2)
        strip.set_max_children_per_line(5)
        strip.set_column_spacing(6)
        strip.set_row_spacing(6)
        for step in steps[:12]:
            if isinstance(step, dict):
                _append_step_card(Gtk, strip, step)
        surface.append(strip)
    _append_rows(Gtk, surface, [
        ("Run ID", str(run.get("run_id") or "")),
        ("Target", str(run.get("target") or "")),
        ("Phase", str(run.get("phase") or "")),
        ("Updated", str(run.get("updated_at") or "")),
        ("Log", str(run.get("log_path") or "")),
    ])
    box.append(surface)


def _append_step_card(Gtk: Any, box: Any, step: dict[str, Any]) -> None:
    card = _new_surface(Gtk, compact=True)
    card.set_size_request(132, -1)
    label = Gtk.Label(label=str(step.get("label") or step.get("key") or "step"), xalign=0.0)
    label.set_wrap(True)
    status = Gtk.Label(label=str(step.get("status") or "pending"), xalign=0.0)
    with _suppress_exception():
        status.add_css_class("devtool-metric-subtitle")
    card.append(label)
    card.append(status)
    _append_progress(Gtk, card, step.get("progress"), str(step.get("status") or "pending"))
    box.append(card)


def _append_metric_strip(Gtk: Any, box: Any, metrics: list[tuple[str, str, str]]) -> None:
    strip = Gtk.FlowBox()
    strip.set_selection_mode(Gtk.SelectionMode.NONE)
    strip.set_max_children_per_line(4)
    strip.set_min_children_per_line(2)
    strip.set_column_spacing(0)
    strip.set_row_spacing(0)
    with _suppress_exception():
        strip.add_css_class("devtool-metric-strip")
    for title, value, subtitle in metrics:
        _append_card(Gtk, strip, title, value, subtitle)
    box.append(strip)


def _style_action_button(button: Any, *, primary: bool = False, danger: bool = False) -> None:
    with _suppress_exception():
        if danger:
            button.add_css_class("devtool-danger-button")
        elif primary:
            button.add_css_class("devtool-primary-button")
        else:
            button.add_css_class("devtool-flat-button")


def _append_button_row(Gtk: Any, box: Any, buttons: list[tuple[str, Any | None]]) -> None:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    with _suppress_exception():
        row.add_css_class("devtool-action-row")
    primary_labels = {"Launch", "Build workflow", "Validate repository", "Open HUD"}
    danger_labels = {"Cancel", "Force cancel"}
    for label, callback in buttons:
        button = Gtk.Button(label=label)
        _style_action_button(button, primary=label in primary_labels, danger=label in danger_labels)
        if callback is None:
            with _suppress_exception():
                button.set_sensitive(False)
        else:
            button.connect("clicked", callback)
        row.append(button)
    box.append(row)


def _copy_text_callback(Gtk: Any, widget: Any, text: str) -> Any:
    def copy(_button: Any) -> None:
        if not text:
            return
        with _suppress_exception():
            clipboard = Gtk.Widget.get_clipboard(widget)
            clipboard.set(text)

    return copy


def _open_path_callback(path: str) -> Any | None:
    """Open a directory or a file's containing folder."""
    if not path:
        return None

    def open_path(_button: Any) -> None:
        opener = shutil.which("xdg-open")
        if not opener:
            return
        target = Path(path).expanduser()
        if target.is_file():
            target = target.parent
        if target.exists():
            subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return open_path


def _open_file_callback(path: str) -> Any | None:
    """Open the exact file when it exists, instead of opening only its folder."""
    if not path:
        return None

    def open_file(_button: Any) -> None:
        opener = shutil.which("xdg-open")
        if not opener:
            return
        target = Path(path).expanduser()
        if target.exists():
            subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return open_file


def _open_terminal_callback(path: str) -> Any | None:
    if not path:
        return None

    def open_terminal(_button: Any) -> None:
        cwd = Path(path).expanduser()
        if not cwd.is_dir():
            return
        candidates = [
            ("gnome-terminal", ["gnome-terminal", "--working-directory", str(cwd)]),
            ("kgx", ["kgx", "--working-directory", str(cwd)]),
            ("xfce4-terminal", ["xfce4-terminal", "--working-directory", str(cwd)]),
            ("konsole", ["konsole", "--workdir", str(cwd)]),
            ("x-terminal-emulator", ["x-terminal-emulator"]),
            ("xterm", ["xterm"]),
        ]
        for binary, argv in candidates:
            launcher = shutil.which(binary)
            if launcher:
                argv[0] = launcher
                subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

    return open_terminal


def _append_inline_status(Gtk: Any, box: Any, text: str) -> Any:
    label = Gtk.Label(label=text, xalign=0.0)
    label.set_wrap(True)
    with _suppress_exception():
        label.add_css_class("dim-label")
    box.append(label)
    return label


def _item_search_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in item.values():
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        elif isinstance(value, dict):
            parts.append(_item_search_text(value))
        elif isinstance(value, list):
            parts.extend(str(entry) for entry in value if isinstance(entry, (str, int, float, bool)))
    return " ".join(parts).casefold()


def _matches_search(item: dict[str, Any], query: str) -> bool:
    query = query.strip().casefold()
    return not query or all(part in _item_search_text(item) for part in query.split())


def _clear_list(Gtk: Any, box: Any) -> None:
    _clear(Gtk, box)


def _run_again_callback(Gtk: Any, parent: Any, command: str, cwd: str) -> Any | None:
    if not command:
        return None

    def run_again(_button: Any) -> None:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            _append_inline_status(Gtk, parent, f"Run again blocked: {exc}")
            return
        if not argv:
            _append_inline_status(Gtk, parent, "Run again blocked: empty command")
            return
        review = Gtk.Window(title="Review command before running")
        review.set_default_size(720, 420)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        _append_text(Gtk, outer, "Review the reconstructed argv before launching this previous command.")
        text = Gtk.TextView()
        text.set_editable(False)
        text.set_monospace(True)
        text.get_buffer().set_text("\n".join(shlex.quote(part) for part in argv))
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(text)
        scroller.set_vexpand(True)
        outer.append(scroller)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label=action_text("operation.cancel", "short"))
        launch = Gtk.Button(label="Launch reviewed argv")
        row.append(cancel)
        row.append(launch)
        outer.append(row)
        review.set_child(outer)

        def do_launch(_launch: Any) -> None:
            try:
                subprocess.Popen(argv, cwd=str(Path(cwd or ".").expanduser()), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:  # pragma: no cover - host process dependent
                _append_inline_status(Gtk, parent, f"Run again failed: {exc}")
            else:
                _append_inline_status(Gtk, parent, "Previous command launched after review.")
            review.close()

        cancel.connect("clicked", lambda _unused: review.close())
        launch.connect("clicked", do_launch)
        review.present()

    return run_again


def _remember_repository_callback(Gtk: Any, box: Any, repo_path: str, state_dir: str = "") -> Any | None:
    if not repo_path:
        return None

    def remember(_button: Any) -> None:
        try:
            saved = DesktopRepositoryStore(repo_path).remember(state_dir=state_dir or None)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            _append_inline_status(Gtk, box, f"Could not add repository: {exc}")
            return
        _append_inline_status(Gtk, box, f"Repository added to {saved}")

    return remember


def _refresh_git_state_callback(Gtk: Any, box: Any, repo_path: str) -> Any | None:
    if not repo_path:
        return None

    def refresh(_button: Any) -> None:
        output = DesktopRepositoryStore(repo_path).git_status()
        _append_subheading(Gtk, box, "Git state refreshed")
        _append_text(Gtk, box, output)

    return refresh


def _show_active_run_callback(Gtk: Any, box: Any, payload: dict[str, Any]) -> Any | None:
    runs = [item for item in payload.get("active_runs", []) if isinstance(item, dict)]
    if not runs:
        return None

    def show(_button: Any) -> None:
        _append_subheading(Gtk, box, "Active run")
        _append_run_card(Gtk, box, runs[0])

    return show


def _build_workflow_callback(Gtk: Any, box: Any, payload: dict[str, Any]) -> Any:
    def build(_button: Any) -> None:
        workflow = workflow_snapshot(payload)
        review = review_payload(payload_from_mapping(dict(workflow.default_draft)))
        _append_subheading(Gtk, box, "Default workflow")
        _append_text(Gtk, box, review.command or "No runnable default workflow yet. Open the Run page to fill missing fields.")
        if review.errors:
            _append_text(Gtk, box, "Errors: " + "; ".join(review.errors))

    return build


def _append_search_bar(Gtk: Any, box: Any, placeholder: str) -> Any:
    search = Gtk.SearchEntry()
    search.set_placeholder_text(placeholder)
    search.set_hexpand(True)
    box.append(search)
    return search


def _append_rows(Gtk: Any, box: Any, rows: list[tuple[str, str]]) -> None:
    group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    group.set_hexpand(True)
    with _suppress_exception():
        group.add_css_class("devtool-data-group")
    for index, (key, value) in enumerate(rows):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.set_hexpand(True)
        with _suppress_exception():
            row.add_css_class("devtool-data-row")
            if index == len(rows) - 1:
                row.add_css_class("devtool-data-row-last")
        key_label = Gtk.Label(label=key, xalign=0.0)
        key_label.set_size_request(130, -1)
        with _suppress_exception():
            key_label.add_css_class("devtool-data-key")
        value_label = Gtk.Label(label=value or "-", xalign=0.0)
        value_label.set_hexpand(True)
        value_label.set_selectable(True)
        value_label.set_wrap(True)
        with _suppress_exception():
            value_label.add_css_class("devtool-data-value")
        row.append(key_label)
        row.append(value_label)
        group.append(row)
    box.append(group)


def _render_overview(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    repo = payload.get("repository", {})
    project = payload.get("project", {})
    runs = payload.get("active_runs", [])
    diagnostics = payload.get("diagnostics", [])
    _append_heading(Gtk, box, "Overview")
    _append_text(Gtk, box, "Repository health, active execution, and the next useful action in one control surface.")

    hero = _new_surface(Gtk, accent=True)
    desktop_ui = payload.get("desktop_ui", {}) if isinstance(payload.get("desktop_ui"), dict) else {}
    primary_action = primary_desktop_action(desktop_ui)
    next_action = str(primary_action.get("label") or ("Review active run" if runs else "Validate repository"))
    next_label = Gtk.Label(label="Next action", xalign=0.0)
    with _suppress_exception():
        next_label.add_css_class("devtool-metric-label")
    hero.append(next_label)
    _append_section_heading(Gtk, hero, next_action, f"{str(project.get('selected_target') or 'Automatic target')} · {str(repo.get('branch') or 'no branch')}")
    _append_button_row(Gtk, hero, [
        ("Build workflow", _build_workflow_callback(Gtk, box, payload)),
        ("Open HUD", lambda _button: _launch_hud(str(payload.get("hud", {}).get("directory") or DEFAULT_TASKER_DIR), bool(payload.get("settings", {}).get("follow_active")))),
        ("Copy repository path", _copy_text_callback(Gtk, box, str(repo.get("path") or ""))),
    ])
    box.append(hero)

    _append_metric_strip(Gtk, box, [
        ("Active runs", str(len(runs)), str(runs[0].get("status", "")) if runs and isinstance(runs[0], dict) else "idle"),
        ("Diagnostics", str(len(diagnostics)), _severity_summary(diagnostics)),
        ("History", str(len(payload.get("history", []))), "recent artifacts"),
        ("Targets", str(len(payload.get("targets", []))), str(project.get("selected_target") or "auto")),
    ])

    overview_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
    overview_grid.set_hexpand(True)
    repository_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, repository_surface, "Repository", "Current Git and project selection")
    _append_rows(Gtk, repository_surface, [
        ("Path", str(repo.get("path") or "")),
        ("Branch", str(repo.get("branch") or "")),
        ("Head", str(repo.get("head") or "")),
        ("Working tree", "dirty" if repo.get("dirty") else "clean"),
        ("Platform", str(project.get("platform") or "-")),
        ("Runner", str(project.get("runner") or project.get("kind") or "auto")),
        ("Target", str(project.get("selected_target") or "-")),
        ("Variant", str(project.get("variant") or "-")),
    ])
    health_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, health_surface, "Run readiness", "What needs attention before the next command")
    readiness = "Ready to run" if not diagnostics else _severity_summary(diagnostics)
    _append_rows(Gtk, health_surface, [
        ("State", readiness),
        ("IPC", "connected" if payload.get("ipc", {}).get("ok") else "fallback"),
        ("Active", str(len(runs))),
        ("Errors", str(_count_severity(diagnostics, "error"))),
        ("Warnings", str(_count_severity(diagnostics, "warning"))),
    ])
    overview_grid.attach(repository_surface, 0, 0, 1, 1)
    overview_grid.attach(health_surface, 1, 0, 1, 1)
    box.append(overview_grid)

    if runs:
        _append_subheading(Gtk, box, "Live pipeline")
        _render_runs(Gtk, box, payload, embedded=True)
    if diagnostics:
        diagnostic_surface = _new_surface(Gtk, danger=_count_severity(diagnostics, "error") > 0)
        _append_section_heading(Gtk, diagnostic_surface, "Top diagnostics", "The first issues reported by the canonical active run")
        for item in diagnostics[:3]:
            if isinstance(item, dict):
                _append_text(Gtk, diagnostic_surface, f"{item.get('severity', 'info')}: {item.get('message', '')}")
        box.append(diagnostic_surface)


def _render_runs(Gtk: Any, box: Any, payload: dict[str, Any], *, embedded: bool = False) -> None:
    if not embedded:
        _clear(Gtk, box)
        _append_heading(Gtk, box, "Active runs")
    runs = payload.get("active_runs", [])
    if not runs:
        _append_text(Gtk, box, "No active run is currently published by the HUD state or IPC broker.")
        return
    for run in runs:
        if not isinstance(run, dict):
            continue
        _append_run_card(Gtk, box, run)


def _render_history(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "History")
    prefs = DesktopPreferences.load("control-center")
    filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    search = Gtk.SearchEntry()
    search.set_placeholder_text("Search artifacts, commits, hashes, commands")
    search.set_hexpand(True)
    status_filter = Gtk.ComboBoxText()
    for value, label in (("all", "All statuses"), ("ok", "Succeeded"), ("failed", "Failed"), ("paused", "Paused")):
        status_filter.append(value, label)
    search.set_text(prefs.filter_value("history", "query"))
    status_filter.set_active_id(prefs.filter_value("history", "status", "all") or "all")
    filters.append(search)
    filters.append(status_filter)
    box.append(filters)
    count_label = _append_inline_status(Gtk, box, "")
    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(list_box)
    history = [item for item in payload.get("history", []) if isinstance(item, dict)]
    repo_path = str(payload.get("repository", {}).get("path") or ".") if isinstance(payload.get("repository"), dict) else "."
    if not history:
        count_label.set_label("0 visible")
        _append_text(Gtk, list_box, "No artifact history entries were found in this repository.")
        return

    def render_filtered(*_unused: Any) -> None:
        _clear_list(Gtk, list_box)
        query = str(search.get_text() or "")
        status_id = str(status_filter.get_active_id() or "all")
        prefs.set_filter_value("history", "query", query)
        prefs.set_filter_value("history", "status", status_id)
        prefs.save("control-center")
        visible = []
        for item in reversed(history[-50:]):
            item_status = str(item.get("status") or item.get("result") or item.get("validation") or "ok").strip().lower()
            if status_id != "all" and status_id not in item_status:
                continue
            if not _matches_search(item, query):
                continue
            visible.append(item)
        count_label.set_label(f"{len(visible)} visible")
        if not visible:
            _append_text(Gtk, list_box, "No history entries match the current filters.")
            return
        for item in visible[:50]:
            title = item.get("artifact") or item.get("artifact_name") or item.get("name") or item.get("history_id") or "artifact"
            item_surface = _new_surface(Gtk, compact=True)
            _append_section_heading(Gtk, item_surface, f"{title}", str(item.get("status") or item.get("result") or "Recorded run"))
            _append_rows(Gtk, item_surface, [
                ("Applied", str(item.get("applied_at") or item.get("created_at") or "")),
                ("Commit", str(item.get("commit_hash") or "")),
                ("SHA-256", str(item.get("sha256") or "")),
                ("Command", str(item.get("command") or "")),
            ])
            command = str(item.get("command") or "")
            artifact_path = str(item.get("artifact_path") or item.get("path") or item.get("file") or "")
            _append_button_row(Gtk, item_surface, [
                ("Copy command", _copy_text_callback(Gtk, item_surface, command)),
                ("Inspect artifact", _copy_text_callback(Gtk, item_surface, json.dumps(item, ensure_ascii=False, indent=2))),
                (action_text("operation.rerun"), _run_again_callback(Gtk, item_surface, command, repo_path)),
                ("Open artifact folder", _open_path_callback(artifact_path)),
            ])
            list_box.append(item_surface)

    search.connect("search-changed", render_filtered)
    status_filter.connect("changed", render_filtered)
    render_filtered()


def _render_artifacts(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Artifacts")
    prefs = DesktopPreferences.load("control-center")
    inputs = Gtk.Grid(column_spacing=8, row_spacing=8)
    artifact_entry = Gtk.Entry()
    artifact_entry.set_placeholder_text("Artifact path, overlay bundle, or manifest")
    repo_entry = Gtk.Entry()
    repo_entry.set_placeholder_text("Repository override")
    repo_entry.set_text(str(payload.get("repository", {}).get("path") or ""))
    inputs.attach(Gtk.Label(label="Artifact", xalign=0.0), 0, 0, 1, 1)
    inputs.attach(artifact_entry, 1, 0, 1, 1)
    inputs.attach(Gtk.Label(label="Repository", xalign=0.0), 0, 1, 1, 1)
    inputs.attach(repo_entry, 1, 1, 1, 1)
    box.append(inputs)
    search = _append_search_bar(Gtk, box, "Filter by artifact name, package, version, SHA-256")
    search.set_text(prefs.filter_value("artifacts", "query"))
    count_label = _append_inline_status(Gtk, box, "")
    history = [item for item in payload.get("history", []) if isinstance(item, dict)]
    hud_state = payload.get("hud", {}).get("state", {}) if isinstance(payload.get("hud"), dict) else {}
    candidates = list(reversed(history[-50:]))
    if isinstance(hud_state, dict) and hud_state:
        candidates.insert(0, hud_state)
    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(list_box)
    if not candidates:
        count_label.set_label("0 visible")
        _append_text(Gtk, list_box, "No artifacts have been recorded yet.")
        return

    def render_filtered(*_unused: Any) -> None:
        _clear_list(Gtk, list_box)
        query = str(search.get_text() or "")
        prefs.set_filter_value("artifacts", "query", query)
        prefs.save("control-center")
        manual_artifact = str(artifact_entry.get_text() or "").strip()
        manual_repo = str(repo_entry.get_text() or "").strip()
        manual_candidates: list[dict[str, Any]] = []
        if manual_artifact:
            manual_candidates.append({
                "artifact": Path(manual_artifact).name or manual_artifact,
                "artifact_path": manual_artifact,
                "repo_root": manual_repo,
                "status": "manual-input",
                "validation": "pending inspection",
            })
        visible = [item for item in [*manual_candidates, *candidates] if isinstance(item, dict) and _matches_search(item, query)]
        count_label.set_label(f"{len(visible)} visible")
        if not visible:
            _append_text(Gtk, list_box, "No artifacts match the current filter.")
            return
        metric_item = visible[0]
        _append_metric_strip(Gtk, list_box, [
            ("FILES", str(metric_item.get("file_count") or metric_item.get("paths") or "-"), "inventory"),
            ("ADDED", str(metric_item.get("added") or "-"), "change set"),
            ("MODIFIED", str(metric_item.get("modified") or "-"), "change set"),
            ("Validation", str(metric_item.get("validation") or metric_item.get("status") or "-"), "manifest"),
        ])
        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(False)
        for tab_name in ("Summary", "Inventory", "Manifest", "Diff"):
            page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            page_box.set_margin_top(8)
            page_box.set_margin_bottom(8)
            page_box.set_margin_start(8)
            page_box.set_margin_end(8)
            if tab_name == "Summary":
                _append_rows(Gtk, page_box, [("Candidates", str(len(visible))), ("Selected", str(metric_item.get("artifact") or metric_item.get("artifact_name") or metric_item.get("title") or "artifact"))])
            else:
                _append_text(Gtk, page_box, json.dumps(metric_item, ensure_ascii=False, indent=2) if metric_item else f"No {tab_name.lower()} data yet.")
            notebook.append_page(page_box, Gtk.Label(label=tab_name))
        list_box.append(notebook)
        for item in visible[:20]:
            title = str(item.get("artifact") or item.get("artifact_name") or item.get("name") or item.get("title") or "artifact")
            item_surface = _new_surface(Gtk, compact=True)
            _append_section_heading(Gtk, item_surface, title, str(item.get("validation") or item.get("status") or "Artifact record"))
            _append_rows(Gtk, item_surface, [
                ("Path", str(item.get("artifact_path") or item.get("path") or item.get("file") or "")),
                ("Package", str(item.get("package") or item.get("package_name") or "")),
                ("Version", str(item.get("version") or item.get("version_name") or "")),
                ("Commit", str(item.get("commit_hash") or item.get("head") or "")),
                ("SHA-256", str(item.get("sha256") or "")),
                ("Applied", str(item.get("applied_at") or item.get("created_at") or item.get("updated_at") or "")),
            ])
            artifact_path = str(item.get("artifact_path") or item.get("path") or item.get("file") or "")
            sha256 = str(item.get("sha256") or "")
            buttons = [
                ("Inspect manifest", _copy_text_callback(Gtk, item_surface, json.dumps(item, ensure_ascii=False, indent=2))),
                ("Open containing folder", _open_path_callback(artifact_path)),
                ("Copy SHA-256", _copy_text_callback(Gtk, item_surface, sha256)),
            ]
            if artifact_path:
                buttons.append(("Use in Run page", _copy_text_callback(Gtk, item_surface, artifact_path)))
            _append_button_row(Gtk, item_surface, buttons)
            list_box.append(item_surface)

    search.connect("search-changed", render_filtered)
    artifact_entry.connect("changed", render_filtered)
    repo_entry.connect("changed", render_filtered)
    render_filtered()


def _render_diagnostics(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Diagnostics")
    prefs = DesktopPreferences.load("control-center")
    diagnostics = [item for item in payload.get("diagnostics", []) if isinstance(item, dict)]
    _append_metric_strip(Gtk, box, [
        ("Errors", str(_count_severity(diagnostics, "error")), "blocking"),
        ("Warnings", str(_count_severity(diagnostics, "warning")), "review"),
        ("Info", str(_count_severity(diagnostics, "info")), "context"),
        ("Total", str(len(diagnostics)), "canonical active run"),
    ])
    filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    search = Gtk.SearchEntry()
    search.set_placeholder_text("Filter diagnostics by message, file, phase, code")
    search.set_hexpand(True)
    severity = Gtk.ComboBoxText()
    for value, label in (("all", "Severity: all"), ("error", "Errors"), ("warning", "Warnings"), ("info", "Info")):
        severity.append(value, label)
    search.set_text(prefs.filter_value("diagnostics", "query"))
    severity.set_active_id(prefs.filter_value("diagnostics", "severity", "all") or "all")
    filters.append(search)
    filters.append(severity)
    box.append(filters)
    count_label = _append_inline_status(Gtk, box, "")
    action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.append(action_row)
    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(list_box)
    log_path = ""
    active_runs = payload.get("active_runs", [])
    if active_runs and isinstance(active_runs[0], dict):
        log_path = str(active_runs[0].get("log_path") or "")
    diagnostic_actions: list[tuple[str, Any | None]] = [
        ("Copy all diagnostics", _copy_text_callback(Gtk, list_box, json.dumps(diagnostics, ensure_ascii=False, indent=2))),
    ]
    open_log_cb = _open_file_callback(log_path)
    if open_log_cb is not None:
        diagnostic_actions.append((action_text("logs.open"), open_log_cb))
    show_run_cb = _show_active_run_callback(Gtk, list_box, payload)
    if show_run_cb is not None:
        diagnostic_actions.append(("Show active run", show_run_cb))
    _append_button_row(Gtk, action_row, diagnostic_actions)
    if not diagnostics:
        count_label.set_label("0 visible")
        _append_text(Gtk, list_box, "No warnings or errors are currently reported by the active run.")
        return

    def render_filtered(*_unused: Any) -> None:
        _clear_list(Gtk, list_box)
        query = str(search.get_text() or "")
        severity_id = str(severity.get_active_id() or "all")
        prefs.set_filter_value("diagnostics", "query", query)
        prefs.set_filter_value("diagnostics", "severity", severity_id)
        prefs.save("control-center")
        visible = []
        for item in diagnostics:
            item_severity = str(item.get("severity") or "info").lower()
            if severity_id != "all" and item_severity != severity_id:
                continue
            if not _matches_search(item, query):
                continue
            visible.append(item)
        count_label.set_label(f"{len(visible)} visible")
        if not visible:
            _append_text(Gtk, list_box, "No diagnostics match the current filters.")
            return
        for item in visible[:100]:
            severity_text = str(item.get("severity") or "info").lower()
            item_surface = _new_surface(Gtk, compact=True, danger=severity_text in {"error", "failed", "failure"})
            if severity_text in {"warning", "warn"}:
                with _suppress_exception():
                    item_surface.add_css_class("devtool-diagnostic-warning")
            _append_section_heading(Gtk, item_surface, severity_text.title(), str(item.get("message") or ""))
            detail_rows = []
            for key in ("code", "phase", "file", "source"):
                value = str(item.get(key) or "")
                if value:
                    detail_rows.append((key.title(), value))
            if detail_rows:
                _append_rows(Gtk, item_surface, detail_rows)
            list_box.append(item_surface)

    search.connect("search-changed", render_filtered)
    severity.connect("changed", render_filtered)
    render_filtered()


def _render_repositories(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Repository manager")
    repo = payload.get("repository", {}) if isinstance(payload.get("repository"), dict) else {}
    repo_path = str(repo.get("path") or "")
    state_dir = str(repo.get("state_dir") or payload.get("hud", {}).get("directory") or "") if isinstance(payload.get("hud"), dict) else str(repo.get("state_dir") or "")
    _append_metric_strip(Gtk, box, [
        ("Branch", str(repo.get("branch") or "-"), str(repo.get("head") or "")[:12]),
        ("Dirty", "yes" if repo.get("dirty") else "no", "working tree"),
        ("Targets", str(len(payload.get("targets", []))), "configured"),
        ("IPC", "connected" if payload.get("ipc", {}).get("ok") else "offline", "broker"),
    ])
    _append_rows(Gtk, box, [
        ("Path", repo_path),
        ("Exists", "yes" if repo.get("exists") else "no"),
        ("Branch", str(repo.get("branch") or "")),
        ("Head", str(repo.get("head") or "")),
        ("Dirty", "yes" if repo.get("dirty") else "no"),
    ])
    _append_button_row(Gtk, box, [
        ("Add current repository", _remember_repository_callback(Gtk, box, repo_path, state_dir)),
        ("Open terminal here", _open_terminal_callback(repo_path)),
        ("Refresh git state", _refresh_git_state_callback(Gtk, box, repo_path)),
        ("Copy repo path", _copy_text_callback(Gtk, box, repo_path)),
    ])


def _render_targets(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Targets")
    prefs = DesktopPreferences.load("control-center")
    targets = [item for item in payload.get("targets", []) if isinstance(item, dict)]
    search = _append_search_bar(Gtk, box, "Filter targets by name, platform, runner, group, workflow, environment, chroot")
    search.set_text(prefs.filter_value("targets", "query"))
    count_label = _append_inline_status(Gtk, box, "")
    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(list_box)
    if not targets:
        count_label.set_label("0 visible")
        _append_text(Gtk, list_box, "No named targets were configured for this repository.")
        return

    def render_filtered(*_unused: Any) -> None:
        _clear_list(Gtk, list_box)
        query = str(search.get_text() or "")
        prefs.set_filter_value("targets", "query", query)
        prefs.save("control-center")
        visible = [target for target in targets if _matches_search(target, query)]
        count_label.set_label(f"{len(visible)} visible")
        if not visible:
            _append_text(Gtk, list_box, "No targets match the current filter.")
            return
        for target in visible:
            item_surface = _new_surface(Gtk, compact=True)
            platform = str(target.get("platform") or "-")
            runner = str(target.get("runner") or target.get("kind") or "auto")
            _append_section_heading(Gtk, item_surface, str(target.get("name") or "Target"), f"{platform} · {runner}")
            workflows = target.get("workflows") if isinstance(target.get("workflows"), list) else []
            _append_rows(Gtk, item_surface, [
                ("Platform", platform),
                ("Primary runner", runner),
                ("Workflows", ", ".join(str(item) for item in workflows) or "-"),
                ("Root", str(target.get("root") or "")),
                ("Build", str(target.get("build") or "")),
                ("Test", str(target.get("test") or "")),
                ("Run", str(target.get("run") or "")),
                ("Environment", str(target.get("environment") or "auto")),
                ("Chroot", str(target.get("chroot") or "")),
            ])
            list_box.append(item_surface)

    search.connect("search-changed", render_filtered)
    render_filtered()


def _render_run_workflow(
    Gtk: Any,
    box: Any,
    payload: dict[str, Any],
    *,
    run_launcher: DesktopRunLauncher | None = None,
    GLib: Any | None = None,
) -> None:
    """Render the Phase 4 mutable Run workflow.

    The page is intentionally initialized once so periodic Control Center
    refreshes do not erase edits while someone is building a command.
    """
    if box.get_first_child() is not None:
        return
    workflow = workflow_snapshot(payload)
    draft = dict(workflow.default_draft)
    targets = list(workflow.targets)
    operations = list(workflow.operations)

    _append_heading(Gtk, box, "Run")
    _append_text(Gtk, box, "Compose a typed Devtool command, review its exact argv, then launch and watch output without leaving the page.")
    _append_metric_strip(Gtk, box, [
        ("Stage 1", "Project", "repository, target, variant"),
        ("Stage 2", "Operation", "build, test, apply, restore"),
        ("Stage 3", "Review", "exact argv and warnings"),
        ("Stage 4", "Launch", "stream output and cancel"),
    ])

    workspace = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
    workspace.set_hexpand(True)
    workspace.set_vexpand(True)
    workspace.set_position(430)
    with _suppress_exception():
        workspace.add_css_class("devtool-command-workspace")
    left_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    right_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    with _suppress_exception():
        left_column.add_css_class("devtool-composer-column")
        left_column.add_css_class("devtool-composer-column-left")
        right_column.add_css_class("devtool-composer-column")
        right_column.add_css_class("devtool-composer-column-right")
    workspace.set_start_child(left_column)
    workspace.set_end_child(right_column)
    box.append(workspace)

    setup_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, setup_surface, "Command setup", "Common fields stay visible; less-used selectors live under Advanced.")
    form = Gtk.Grid(column_spacing=14, row_spacing=10)
    form.set_hexpand(True)
    setup_surface.append(form)
    left_column.append(setup_surface)
    row = 0
    widgets: dict[str, Any] = {}

    def use_form(next_form: Any) -> None:
        nonlocal form, row
        form = next_form
        row = 0

    def add_label(text: str, r: int) -> None:
        label = Gtk.Label(label=text, xalign=0.0)
        with _suppress_exception():
            label.add_css_class("devtool-data-key")
        form.attach(label, 0, r, 1, 1)

    def add_entry(key: str, label: str, value: str = "", *, placeholder: str = "") -> None:
        nonlocal row
        add_label(label, row)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_text(value or "")
        if placeholder:
            entry.set_placeholder_text(placeholder)
        form.attach(entry, 1, row, 1, 1)
        widgets[key] = entry
        row += 1

    def add_combo(key: str, label: str, values: list[tuple[str, str]], active: str = "") -> None:
        nonlocal row
        add_label(label, row)
        combo = Gtk.ComboBoxText()
        combo.set_hexpand(True)
        for value, text in values:
            combo.append(value, text)
        if active:
            combo.set_active_id(active)
        if combo.get_active_id() is None and values:
            combo.set_active(0)
        form.attach(combo, 1, row, 1, 1)
        widgets[key] = combo
        row += 1

    operation_values = [(str(item.get("id") or ""), str(item.get("label") or item.get("id") or "")) for item in operations]
    add_combo("operation", "Operation", operation_values, str(draft.get("operation") or "validate"))
    target_values = [("", "Automatic / repository default"), *[(item, item) for item in targets]]
    add_combo("target", "Target", target_values, str(draft.get("target") or ""))
    add_entry("repository", "Repository", str(draft.get("repository") or "."))
    add_entry("artifact", "Overlay / bundle", str(draft.get("artifact") or ""), placeholder="required for apply-overlay/apply-overlays")
    add_entry("pipeline_file", "Pipeline JSON", str(draft.get("pipeline_file") or ".devtool/actions-pipeline.json"), placeholder="repository-relative committed manifest")
    add_entry("pipeline_id", "Pipeline ID", str(draft.get("pipeline_id") or ""), placeholder="32 lowercase hex characters for status/resume")
    add_entry("test_tasks", "Native tasks", str(draft.get("test_tasks") or ""), placeholder=":app:testDebugUnitTest")
    add_entry("commit_message", "Commit message", str(draft.get("commit_message") or ""))

    advanced_form = Gtk.Grid(column_spacing=14, row_spacing=10)
    advanced_form.set_hexpand(True)
    advanced_expander = Gtk.Expander(label="Advanced project, test, environment, and HUD options")
    advanced_expander.set_expanded(False)
    advanced_expander.set_child(advanced_form)
    setup_surface.append(advanced_expander)
    use_form(advanced_form)
    add_entry("variant", "Variant", str(draft.get("variant") or ""), placeholder="optional")
    add_entry("project_type", "Project type", str(draft.get("project_type") or "auto"), placeholder="auto, python, android, dotnet…")
    add_entry("execution_environment", "Execution environment", str(draft.get("execution_environment") or "auto"), placeholder="auto, host, desktop-linux…")
    add_entry("test_profile", "Test profile", str(draft.get("test_profile") or ""), placeholder="optional saved profile")
    add_entry("selectors", "Selectors", str(draft.get("selectors") or ""), placeholder="shell-split values, e.g. tests/foo.py::test_bar")
    add_entry("test_ids", "Test IDs", str(draft.get("test_ids") or ""), placeholder="exact framework IDs")
    add_entry("tags", "Include tags", str(draft.get("tags") or ""))
    add_entry("exclude_tags", "Exclude tags", str(draft.get("exclude_tags") or ""))
    add_entry("test_path", "Test path", str(draft.get("test_path") or ""))
    add_entry("test_class", "Test class", str(draft.get("test_class") or ""))
    add_entry("test_method", "Test method", str(draft.get("test_method") or ""))
    add_entry("test_filter", "Native filter", str(draft.get("test_filter") or ""))
    add_entry("pytest_tests", "Pytest tests", str(draft.get("pytest_tests") or ""), placeholder="exact node IDs/files; shell-split")
    add_entry("pytest_markers", "Pytest markers", str(draft.get("pytest_markers") or ""), placeholder="pytest -m expressions")
    add_entry("pytest_k", "Pytest -k", str(draft.get("pytest_k") or ""), placeholder="keyword expression")
    add_entry("pytest_args", "Pytest arguments", str(draft.get("pytest_args") or ""), placeholder="e.g. --maxfail=1 -x")
    add_entry("environment", "Environment", str(draft.get("environment") or ""), placeholder="NAME=value OTHER='two words'")
    add_entry("extra_global_args", "Extra global args", str(draft.get("extra_global_args") or ""))
    add_entry("extra_operation_args", "Extra operation args", str(draft.get("extra_operation_args") or ""))
    add_combo("copy_mode", "Clipboard", [("summary", "Summary"), ("json", "JSON"), ("none", "Disabled")], str(draft.get("copy_mode") or "summary"))
    add_combo(
        "hud_mode",
        "HUD mode",
        [("control-center", "Control Center"), ("desktop-window", "Desktop window"), ("terminal", "Terminal"), ("web", "Web"), ("studio", "Studio"), ("none", "Disabled")],
        str(draft.get("hud_mode") or "control-center"),
    )
    add_entry("changed_from", "Changed from", str(draft.get("changed_from") or "HEAD"))

    options_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, options_surface, "Execution options", "Tonal switches replace a wall of outlined controls.")
    checks = Gtk.FlowBox()
    checks.set_selection_mode(Gtk.SelectionMode.NONE)
    checks.set_max_children_per_line(3)
    checks.set_column_spacing(4)
    checks.set_row_spacing(4)
    with _suppress_exception():
        checks.add_css_class("devtool-options-flow")
    options_surface.append(checks)
    left_column.append(options_surface)

    def add_check(key: str, label: str, active: bool = False) -> None:
        check = Gtk.CheckButton(label=label)
        check.set_active(bool(active))
        widgets[key] = check
        checks.append(check)

    add_check("yes", "Non-interactive --yes", bool(draft.get("yes", True)))
    add_check("auto_hud", "Auto HUD", bool(draft.get("auto_hud", True)))
    add_check("validate", "Validate after apply", bool(draft.get("validate", False)))
    add_check("commit", "Commit after apply", bool(draft.get("commit", False)))
    add_check("keep_artifact", "Keep artifact", bool(draft.get("keep_artifact", False)))
    add_check("no_manifest_check", "Skip manifest check", bool(draft.get("no_manifest_check", False)))
    add_check("list_tests", "List tests", bool(draft.get("list_tests", False)))
    add_check("pytest_include_defaults", "Pytest + defaults", bool(draft.get("pytest_include_defaults", False)))
    add_check("failed", "Failed tests", bool(draft.get("failed", False)))
    add_check("changed", "Changed tests", bool(draft.get("changed", False)))
    add_check("instrumented", "Instrumented", bool(draft.get("instrumented", False)))

    review_surface = _new_surface(Gtk, accent=True)
    _append_section_heading(Gtk, review_surface, "Review & launch", "The exact shell form and argv stay beside the editable command.")
    preview = Gtk.TextView()
    preview.set_editable(False)
    preview.set_monospace(True)
    preview.set_vexpand(True)
    preview.set_hexpand(True)
    with _suppress_exception():
        preview.add_css_class("devtool-command-preview")
    _set_accessible_label(Gtk, preview, "Command preview showing shell review text and argv")
    preview_scroll = Gtk.ScrolledWindow()
    preview_scroll.set_child(preview)
    preview_scroll.set_min_content_height(210)
    preview_scroll.set_vexpand(True)
    review_surface.append(preview_scroll)
    status = Gtk.Label(label="", xalign=0.0)
    status.set_wrap(True)
    with _suppress_exception():
        status.add_css_class("devtool-inline-status")
    review_surface.append(status)
    button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    with _suppress_exception():
        button_row.add_css_class("devtool-action-row")
    review_surface.append(button_row)
    right_column.append(review_surface)

    output_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, output_surface, "Live run output", "stdout and stderr remain distinct while the command is running.")
    run_identity = Gtk.Label(label="No desktop-launched run yet.", xalign=0.0)
    run_identity.set_wrap(True)
    with _suppress_exception():
        run_identity.add_css_class("devtool-inline-status")
    output_surface.append(run_identity)
    output_view = Gtk.TextView()
    output_view.set_editable(False)
    output_view.set_cursor_visible(False)
    output_view.set_monospace(True)
    output_view.set_vexpand(True)
    output_view.set_hexpand(True)
    with _suppress_exception():
        output_view.add_css_class("devtool-run-output")
    _set_accessible_label(Gtk, output_view, "Live stdout and stderr for the launched Devtool command")
    output_scroll = Gtk.ScrolledWindow()
    output_scroll.set_child(output_view)
    output_scroll.set_min_content_height(220)
    output_scroll.set_vexpand(True)
    with _suppress_exception():
        output_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    output_surface.append(output_scroll)
    right_column.append(output_surface)

    evidence_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, evidence_surface, "Diagnostics & evidence", "Live parsed issues and durable run identity stay beside the output.")
    evidence_tabs = Gtk.Notebook()
    diagnostics_view = Gtk.TextView()
    diagnostics_view.set_editable(False)
    diagnostics_view.set_cursor_visible(False)
    diagnostics_view.set_monospace(True)
    diagnostics_view.get_buffer().set_text("No diagnostics yet.")
    evidence_view = Gtk.TextView()
    evidence_view.set_editable(False)
    evidence_view.set_cursor_visible(False)
    evidence_view.set_monospace(True)
    evidence_view.get_buffer().set_text("Launch a reviewed command to capture run identity, argv, cwd, PID, and log path.")
    for view, accessible in ((diagnostics_view, "Live parsed diagnostics for the launched Devtool command"), (evidence_view, "Execution evidence for the launched Devtool command")):
        view.set_vexpand(True)
        view.set_hexpand(True)
        _set_accessible_label(Gtk, view, accessible)
    diagnostics_scroll = Gtk.ScrolledWindow()
    diagnostics_scroll.set_child(diagnostics_view)
    diagnostics_scroll.set_min_content_height(150)
    evidence_scroll = Gtk.ScrolledWindow()
    evidence_scroll.set_child(evidence_view)
    evidence_scroll.set_min_content_height(150)
    evidence_tabs.append_page(diagnostics_scroll, Gtk.Label(label="Diagnostics"))
    evidence_tabs.append_page(evidence_scroll, Gtk.Label(label="Evidence"))
    evidence_surface.append(evidence_tabs)
    right_column.append(evidence_surface)

    run_state: dict[str, DesktopRunHandle | None] = {"handle": None}
    launcher = run_launcher or DesktopRunLauncher()

    def entry_text(key: str) -> str:
        widget = widgets[key]
        if hasattr(widget, "get_text"):
            return str(widget.get_text())
        return ""

    def combo_id(key: str) -> str:
        value = widgets[key].get_active_id()
        return str(value or "")

    def check_value(key: str) -> bool:
        return bool(widgets[key].get_active())

    def current_mapping() -> dict[str, Any]:
        return {
            "repository": entry_text("repository"),
            "target": combo_id("target"),
            "variant": entry_text("variant"),
            "project_type": entry_text("project_type"),
            "execution_environment": entry_text("execution_environment"),
            "operation": combo_id("operation"),
            "artifact": entry_text("artifact"),
            "pipeline_file": entry_text("pipeline_file"),
            "pipeline_id": entry_text("pipeline_id"),
            "test_profile": entry_text("test_profile"),
            "selectors": entry_text("selectors"),
            "test_ids": entry_text("test_ids"),
            "tags": entry_text("tags"),
            "exclude_tags": entry_text("exclude_tags"),
            "test_path": entry_text("test_path"),
            "test_class": entry_text("test_class"),
            "test_method": entry_text("test_method"),
            "test_filter": entry_text("test_filter"),
            "pytest_tests": entry_text("pytest_tests"),
            "pytest_markers": entry_text("pytest_markers"),
            "pytest_k": entry_text("pytest_k"),
            "pytest_args": entry_text("pytest_args"),
            "pytest_include_defaults": check_value("pytest_include_defaults"),
            "test_tasks": entry_text("test_tasks"),
            "list_tests": check_value("list_tests"),
            "failed": check_value("failed"),
            "changed": check_value("changed"),
            "changed_from": entry_text("changed_from"),
            "instrumented": check_value("instrumented"),
            "validate": check_value("validate"),
            "commit": check_value("commit"),
            "commit_message": entry_text("commit_message"),
            "keep_artifact": check_value("keep_artifact"),
            "no_manifest_check": check_value("no_manifest_check"),
            "copy_mode": combo_id("copy_mode"),
            "auto_hud": check_value("auto_hud"),
            "hud_mode": combo_id("hud_mode"),
            "yes": check_value("yes"),
            "environment": entry_text("environment"),
            "extra_global_args": entry_text("extra_global_args"),
            "extra_operation_args": entry_text("extra_operation_args"),
        }

    def update_preview(*_unused: Any) -> None:
        review = review_payload(payload_from_mapping(current_mapping()))
        lines: list[str] = []
        if review.command:
            lines.append(review.command)
            lines.append("")
            lines.append("argv:")
            lines.extend(f"  {shlex.quote(part)}" for part in review.argv)
        else:
            lines.append("No runnable command yet.")
        if review.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  • {item}" for item in review.warnings)
        if review.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  • {item}" for item in review.errors)
        buffer = preview.get_buffer()
        buffer.set_text("\n".join(lines))
        status.set_text("Ready to launch." if review.runnable else "Fix the errors before launching.")

    def copy_command(_button: Any) -> None:
        review = review_payload(payload_from_mapping(current_mapping()))
        if not review.command:
            status.set_text("Nothing to copy yet.")
            return
        with _suppress_exception():
            clipboard = preview.get_display().get_clipboard()
            clipboard.set(review.command)
            status.set_text("Copied command to clipboard.")

    def update_run_evidence(handle: DesktopRunHandle | None) -> None:
        if handle is None:
            diagnostics_view.get_buffer().set_text("No diagnostics yet.")
            return
        launcher.refresh_truth(handle)
        diagnostics = handle.semantic_diagnostics
        if diagnostics:
            diagnostics_view.get_buffer().set_text("\n".join(f"{item.get('severity', 'info')}: {item.get('message', '')}" for item in diagnostics[-100:]))
        elif handle.diagnostics:
            diagnostics_view.get_buffer().set_text("Output observations (non-authoritative):\n" + "\n".join(f"{item.get('severity', 'info')}: {item.get('message', '')}" for item in handle.diagnostics[-100:]))
        else:
            diagnostics_view.get_buffer().set_text("No canonical diagnostics yet.")
        evidence_lines = [
            f"run_id: {handle.run_id}",
            f"status: {handle.semantic_status}",
            f"pid: {handle.process.pid}",
            f"cwd: {handle.cwd}",
            f"log: {handle.log_path}",
            f"elapsed_seconds: {handle.elapsed_seconds:.3f}",
            f"truth: {'canonical' if handle.runtime_state.get('desktop_truth') == 'canonical' else 'process-observation'}",
            "argv:",
            *[f"  {shlex.quote(part)}" for part in handle.argv],
        ]
        if handle.returncode is not None:
            evidence_lines.insert(2, f"exit_code: {handle.returncode}")
        evidence_view.get_buffer().set_text("\n".join(evidence_lines))

    def append_run_output(line: str) -> None:
        buffer = output_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        current = buffer.get_text(start, end, False)
        next_text = (current + "\n" if current else "") + redact_text(line)
        buffer.set_text(next_text[-60000:])
        with _suppress_exception():
            mark = buffer.create_mark(None, buffer.get_end_iter(), False)
            output_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        update_run_evidence(run_state.get("handle"))

    def idle_call(callback: Any, *args: Any) -> None:
        if GLib is not None:
            GLib.idle_add(lambda: (callback(*args), False)[1])
        else:
            callback(*args)

    action_buttons: dict[str, Any] = {}

    def on_run_exit(handle: DesktopRunHandle) -> None:
        if handle.returncode not in (None, 0):
            _write_desktop_run_hud_failure(payload, handle)

        def update() -> None:
            run_identity.set_text(f"{handle.summary()} · log {handle.log_path}")
            status.set_text(f"Run finished with exit code {handle.returncode}. Log: {handle.log_path}")
            launch_button = action_buttons.get("Launch")
            if launch_button is not None:
                launch_button.set_sensitive(True)
            update_run_evidence(handle)
        idle_call(update)

    def launch_command(_button: Any) -> None:
        active = run_state.get("handle")
        if active is not None and active.running:
            status.set_text(f"Run {active.run_id} is still running; duplicate launch blocked.")
            return
        review = review_payload(payload_from_mapping(current_mapping()))
        if not review.runnable:
            status.set_text("Command has errors; launch blocked.")
            return
        try:
            output_view.get_buffer().set_text("")
            handle = launcher.start(
                review.argv,
                cwd=Path(current_mapping()["repository"]).expanduser(),
                environment=review.environment,
                on_output=lambda channel, line: idle_call(append_run_output, f"[{channel}] {line}"),
                on_exit=on_run_exit,
            )
        except Exception as exc:  # pragma: no cover - host process/display dependent
            status.set_text(f"Launch failed: {exc}")
            return
        run_state["handle"] = handle
        update_run_evidence(handle)
        launch_button = action_buttons.get("Launch")
        if launch_button is not None:
            launch_button.set_sensitive(False)
        run_identity.set_text(f"{handle.run_id} · pid {handle.process.pid} · phase waiting · log {handle.log_path}")
        status.set_text("Launched Devtool command as direct argv. Live stdout/stderr are streaming below.")

    def cancel_running(_button: Any) -> None:
        handle = run_state.get("handle")
        if handle is None:
            status.set_text("No desktop-launched run is active.")
            return
        if launcher.cancel(handle, force=False):
            status.set_text(f"Cancel requested for {handle.run_id}.")
        else:
            status.set_text(f"Run {handle.run_id} is not running.")

    def force_cancel_running(_button: Any) -> None:
        handle = run_state.get("handle")
        if handle is None:
            status.set_text("No desktop-launched run is active.")
            return
        if launcher.cancel(handle, force=True):
            status.set_text(f"Force-cancel requested for {handle.run_id}.")
        else:
            status.set_text(f"Run {handle.run_id} is not running.")

    def dry_run(_button: Any) -> None:
        mapping = current_mapping()
        mapping["extra_operation_args"] = (str(mapping.get("extra_operation_args") or "") + " --dry-run").strip()
        review = review_payload(payload_from_mapping(mapping))
        buffer = preview.get_buffer()
        buffer.set_text(review.command or "No runnable dry-run command yet.")
        status.set_text("Dry-run command previewed; launch still uses the editable fields above.")

    for label, callback, accessible in (
        ("Refresh preview", update_preview, "Refresh the command preview"),
        ("Copy command", copy_command, "Copy the reviewed shell command"),
        ("Preview dry-run", dry_run, "Preview a dry-run form of this command"),
        ("Launch", launch_command, "Launch the reviewed command as direct argv"),
        ("Cancel", cancel_running, "Request graceful cancellation of the active desktop-launched run"),
        ("Force cancel", force_cancel_running, "Force-cancel the active desktop-launched run"),
    ):
        button = Gtk.Button(label=label)
        action_buttons[label] = button
        _style_action_button(button, primary=label == "Launch", danger=label in {"Cancel", "Force cancel"})
        button.connect("clicked", callback)
        _set_accessible_label(Gtk, button, accessible)
        button_row.append(button)

    def on_pipeline_operation_changed(_widget: Any) -> None:
        # A remote dispatch/resume requires fresh, visible user authorization.
        # The ordinary Run composer historically defaults --yes to enabled;
        # never inherit that default when switching into a GitHub mutation.
        if combo_id("operation") in ("pipeline-run", "pipeline-resume", "pipeline-cancel"):
            widgets["yes"].set_active(False)
        update_preview()

    widgets["operation"].connect("changed", on_pipeline_operation_changed)
    for widget in widgets.values():
        with _suppress_exception():
            widget.connect("changed", update_preview)
        with _suppress_exception():
            widget.connect("toggled", update_preview)
    update_preview()


def _write_desktop_run_hud_failure(payload: dict[str, Any], handle: DesktopRunHandle) -> None:
    """Publish a failed desktop-launched run to the same HUD surface users watch.

    The Control Center's run page can start ad-hoc commands that are not always
    represented by the normal IPC broker. On failure, write a small HUD state so
    the failure card, canonical diagnostic counts, and Copy diagnostics action
    show the desktop run instead of leaving the user in a dead-end log cave.
    """
    hud = payload.get("hud", {}) if isinstance(payload.get("hud"), dict) else {}
    directory = Path(str(hud.get("directory") or DEFAULT_TASKER_DIR)).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        diagnostics = handle.diagnostics or [{"severity": "error", "message": handle.failure_summary() or f"Desktop run exited {handle.returncode}", "source": "desktop-run"}]
        state = {
            "run_id": handle.run_id,
            "status": "failed",
            "event_type": "desktop-run-failed",
            "title": "Desktop-launched Devtool run failed",
            "text": handle.failure_summary() or f"Run exited {handle.returncode}. See {handle.log_path}",
            "expanded_text": f"{handle.summary()}\nLog: {handle.log_path}",
            "phase_name": handle.current_phase or "desktop run",
            "command": " ".join(handle.argv),
            "repo_name": handle.cwd.name,
            "repo_root": str(handle.cwd),
            "target": "desktop-control-center",
            "current": 1,
            "total": 1,
            "updated_at": str(int(time.time())),
            "diagnostics": diagnostics,
            "completion": {
                "headline": "Desktop-launched Devtool run failed",
                "detail": handle.failure_summary() or f"Run exited {handle.returncode}. See {handle.log_path}",
                "phase": handle.current_phase or "desktop run",
                "errors": max(1, sum(1 for item in diagnostics if item.get("severity") == "error")),
                "warnings": sum(1 for item in diagnostics if item.get("severity") == "warning"),
                "diagnostics": diagnostics,
                "duration_seconds": round(handle.elapsed_seconds, 3),
                "log_path": str(handle.log_path),
            },
        }
        _atomic_write_json(directory / "state.json", state)
        events_path = directory / "events.jsonl"
        fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(events_path, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as events:
            events.write(json.dumps({"run_id": handle.run_id, "event_type": "desktop-run-failed", "status": "failed", "message": state["text"], "diagnostics": diagnostics}, ensure_ascii=False) + "\n")
    except Exception as exc:
        append_gtk_log("control-center", "Failed to publish desktop run failure to HUD", exc)

def _render_settings(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Settings")
    _append_text(Gtk, box, "Tune the desktop surfaces directly. Changes are persisted without editing environment variables by hand.")
    project = payload.get("project", {})
    settings = payload.get("settings", {})
    hud = payload.get("hud", {})
    ipc = payload.get("ipc", {})

    paths_surface = _new_surface(Gtk)
    _append_section_heading(Gtk, paths_surface, "Runtime paths", "Repository, configuration, HUD state, and IPC discovery")
    _append_rows(Gtk, paths_surface, [
        ("Config", str(project.get("config_path") or "")),
        ("Global config", str(project.get("global_config_path") or "")),
        ("HUD state", str(hud.get("directory") or "")),
        ("IPC", "connected" if ipc.get("ok") else str(ipc.get("error") or "unavailable")),
        ("Follow active", "yes" if settings.get("follow_active") else "no"),
        ("History limit", str(settings.get("history_limit") or 0)),
        ("Recent events", str(settings.get("recent_events") or 0)),
    ])
    box.append(paths_surface)

    semantic_ui = payload.get("ui", {}) if isinstance(payload.get("ui"), dict) else {}
    presentation = semantic_ui.get("presentation", {}) if isinstance(semantic_ui.get("presentation"), dict) else {}
    if presentation:
        shared_surface = _new_surface(Gtk)
        _append_section_heading(
            Gtk,
            shared_surface,
            "Shared UI profile",
            "Resolved from global/repository [ui], profile, surface defaults, environment, and per-run CLI overrides",
        )
        responsive = semantic_ui.get("responsive", {}) if isinstance(semantic_ui.get("responsive"), dict) else {}
        capabilities = semantic_ui.get("capabilities", {}) if isinstance(semantic_ui.get("capabilities"), dict) else {}
        enabled_capabilities = [
            key for key, value in capabilities.items()
            if key not in {"surface", "schema_version"} and value is True
        ]
        desktop_ui = payload.get("desktop_ui", {}) if isinstance(payload.get("desktop_ui"), dict) else {}
        ui_actions = desktop_ui.get("actions") if isinstance(desktop_ui.get("actions"), list) else []
        ui_navigation = desktop_ui.get("navigation") if isinstance(desktop_ui.get("navigation"), list) else []
        _append_rows(Gtk, shared_surface, [
            ("Profile", str(presentation.get("profile") or "standard")),
            ("Theme", str(presentation.get("theme") or "auto")),
            ("Density", str(presentation.get("density") or "comfortable")),
            ("Icons", str(presentation.get("icons") or "auto")),
            ("Motion", str(presentation.get("motion") or "auto")),
            ("Verbosity", str(presentation.get("verbosity") or "normal")),
            ("Diagnostics", str(presentation.get("show_diagnostics") or "expanded-on-error")),
            ("Responsive layout", f"{responsive.get('mode', 'unknown')} · {responsive.get('panes', 1)} pane(s)"),
            ("UI capabilities", ", ".join(enabled_capabilities) or "semantic-only"),
            ("Semantic actions", str(len(ui_actions))),
            ("Semantic navigation", str(len(ui_navigation))),
            ("Desktop UI adapter", f"schema {desktop_ui.get('schema_version', 1)}"),
        ])
        box.append(shared_surface)

    preferences = DesktopPreferences.load("control-center")
    appearance_surface = _new_surface(Gtk, accent=True)
    _append_section_heading(Gtk, appearance_surface, "Appearance", "System-aware theme, contrast, density, and motion preferences")
    form = Gtk.Grid(column_spacing=16, row_spacing=10)
    form.set_hexpand(True)
    appearance_surface.append(form)

    def add_form_label(text: str, row: int) -> None:
        label = Gtk.Label(label=text, xalign=0.0)
        with _suppress_exception():
            label.add_css_class("devtool-data-key")
        form.attach(label, 0, row, 1, 1)

    appearance_combo = Gtk.ComboBoxText()
    for value, label in (("system", "Follow system"), ("dark", "Dark"), ("light", "Light")):
        appearance_combo.append(value, label)
    appearance_combo.set_active_id(preferences.appearance)
    add_form_label("Theme", 0)
    form.attach(appearance_combo, 1, 0, 1, 1)

    high_contrast = Gtk.CheckButton(label="Increase boundaries and text contrast")
    high_contrast.set_active(bool(preferences.high_contrast))
    add_form_label("High contrast", 1)
    form.attach(high_contrast, 1, 1, 1, 1)

    compact_density = Gtk.CheckButton(label="Reduce spacing on information-heavy screens")
    compact_density.set_active(bool(preferences.compact_density))
    add_form_label("Compact density", 2)
    form.attach(compact_density, 1, 2, 1, 1)

    reduced_motion = Gtk.CheckButton(label="Avoid non-essential motion")
    reduced_motion.set_active(bool(preferences.reduced_motion))
    add_form_label("Reduced motion", 3)
    form.attach(reduced_motion, 1, 3, 1, 1)

    nav_hint = Gtk.Label(label="Use the Compact nav button in the header to collapse or expand the sidebar immediately.", xalign=0.0)
    nav_hint.set_wrap(True)
    with _suppress_exception():
        nav_hint.add_css_class("devtool-section-subtitle")
    add_form_label("Navigation", 4)
    form.attach(nav_hint, 1, 4, 1, 1)

    save_status = Gtk.Label(label="Preferences are saved automatically.", xalign=0.0)
    save_status.set_wrap(True)
    with _suppress_exception():
        save_status.add_css_class("devtool-inline-status")
    appearance_surface.append(save_status)
    box.append(appearance_surface)

    def root_widget() -> Any:
        with _suppress_exception():
            return box.get_root()
        return None

    def save_preferences(message: str) -> None:
        preferences.save("control-center")
        save_status.set_label(message)

    def appearance_changed(combo: Any) -> None:
        preferences.appearance = str(combo.get_active_id() or "system")
        try:
            import gi  # type: ignore[import-not-found]
            gi.require_version("Adw", "1")
            from gi.repository import Adw  # type: ignore[import-not-found]
        except Exception:
            Adw = None
        _apply_appearance_preference(Adw, preferences)
        save_preferences(f"Theme set to {preferences.appearance}.")

    def high_contrast_changed(button: Any) -> None:
        preferences.high_contrast = bool(button.get_active())
        root = root_widget()
        if root is not None:
            _set_css_class(root, "devtool-high-contrast", preferences.high_contrast)
        save_preferences("High contrast updated.")

    def compact_density_changed(button: Any) -> None:
        preferences.compact_density = bool(button.get_active())
        root = root_widget()
        if root is not None:
            _set_css_class(root, "devtool-compact-density", preferences.compact_density)
        save_preferences("Compact density updated.")

    def reduced_motion_changed(button: Any) -> None:
        preferences.reduced_motion = bool(button.get_active())
        save_preferences("Reduced motion preference updated.")

    appearance_combo.connect("changed", appearance_changed)
    high_contrast.connect("toggled", high_contrast_changed)
    compact_density.connect("toggled", compact_density_changed)
    reduced_motion.connect("toggled", reduced_motion_changed)

    _append_button_row(Gtk, box, [
        ("Copy settings JSON", _copy_text_callback(Gtk, box, json.dumps({"settings": settings, "ui": semantic_ui, "desktop_ui": payload.get("desktop_ui", {})}, ensure_ascii=False, indent=2))),
        ("Open HUD", lambda _button: _launch_hud(str(hud.get("directory") or DEFAULT_TASKER_DIR), bool(settings.get("follow_active")))),
        ("Copy HUD state path", _copy_text_callback(Gtk, box, str(hud.get("directory") or ""))),
    ])
    _append_text(Gtk, box, "Clipboard monitoring is intentionally unavailable until a real supported service mode exists.")


def _count_severity(diagnostics: Any, severity: str) -> int:
    if not isinstance(diagnostics, list):
        return 0
    aliases = {
        "warning": {"warning", "warn"},
        "error": {"error", "failed", "failure"},
        "info": {"info", "notice", "debug"},
    }.get(severity, {severity})
    count = 0
    for item in diagnostics:
        if isinstance(item, dict) and str(item.get("severity") or "").lower() in aliases:
            count += 1
    return count


def _severity_summary(diagnostics: Any) -> str:
    if not isinstance(diagnostics, list) or not diagnostics:
        return "clean"
    errors = _count_severity(diagnostics, "error")
    warnings = _count_severity(diagnostics, "warning")
    if errors:
        return f"{errors} error(s)"
    if warnings:
        return f"{warnings} warning(s)"
    return "info only"


class _suppress_exception:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return True
