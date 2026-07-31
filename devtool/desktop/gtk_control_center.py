from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from ..tasker import DEFAULT_TASKER_DIR
from .control_center_model import DesktopControlCenterModel
from .run_workflow import payload_from_mapping, review_payload, workflow_snapshot


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class Gtk4ControlCenter:
    """GTK4/libadwaita read-only Control Center shell.

    Phase 3 intentionally ports the dashboard, history, diagnostics,
    repositories, targets, and settings surfaces before the mutable Run workflow.
    Commands are built through the shared argv-first desktop command model.
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
            cls._require_gtk4()
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

        app_id = "io.github.mikeceead.DevtoolControlCenter"
        application = Adw.Application(application_id=app_id) if Adw is not None else Gtk.Application(application_id=app_id)
        poll_ms = max(250, int(max(0.25, interval_s) * 1000))
        selected_page = page if page in _PAGE_KEYS else "overview"

        def activate(app: Any) -> None:
            if Adw is not None:
                window = Adw.ApplicationWindow(application=app)
            else:
                window = Gtk.ApplicationWindow(application=app)
            window.set_title("Devtool Control Center")
            window.set_default_size(1120, 720)
            with _suppress_exception():
                window.set_icon_name("io.github.mikeceead.DevtoolControlCenter")

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            header = Gtk.HeaderBar()
            title = Gtk.Label(label="Devtool Control Center")
            with _suppress_exception():
                title.add_css_class("title-2")
            header.set_title_widget(title)
            refresh_button = Gtk.Button(label="Refresh")
            header.pack_end(refresh_button)
            root.append(header)

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            body.set_vexpand(True)
            nav = Gtk.ListBox()
            nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
            nav.set_size_request(210, -1)
            stack = Gtk.Stack()
            stack.set_hexpand(True)
            stack.set_vexpand(True)
            pages: dict[str, Any] = {}
            nav_rows: dict[str, Any] = {}
            for key, label in _PAGE_KEYS.items():
                row = Gtk.ListBoxRow()
                row.set_child(Gtk.Label(label=label, xalign=0.0))
                row.set_name(key)
                nav.append(row)
                nav_rows[key] = row
                page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                page_box.set_margin_top(18)
                page_box.set_margin_bottom(18)
                page_box.set_margin_start(18)
                page_box.set_margin_end(18)
                stack.add_titled(page_box, key, label)
                pages[key] = page_box
            body.append(nav)
            body.append(stack)
            root.append(body)
            window.set_child(root)

            def current_snapshot() -> dict[str, Any]:
                return self.snapshot(
                    repository=repository,
                    config_path=config_path,
                    target=target,
                    variant=variant,
                    state_dir=state_dir,
                    follow_active=follow_active,
                )

            def render() -> bool:
                payload = current_snapshot()
                _render_overview(Gtk, pages["overview"], payload)
                _render_runs(Gtk, pages["runs"], payload)
                _render_history(Gtk, pages["history"], payload)
                _render_diagnostics(Gtk, pages["diagnostics"], payload)
                _render_repositories(Gtk, pages["repositories"], payload)
                _render_targets(Gtk, pages["targets"], payload)
                _render_run_workflow(Gtk, pages["run"], payload)
                _render_settings(Gtk, pages["settings"], payload)
                return True

            def on_row_selected(_box: Any, row: Any) -> None:
                if row is None:
                    return
                name = row.get_name() or "overview"
                stack.set_visible_child_name(name)

            nav.connect("row-selected", on_row_selected)
            refresh_button.connect("clicked", lambda _button: render())
            render()
            if selected_page in nav_rows:
                nav.select_row(nav_rows[selected_page])
            stack.set_visible_child_name(selected_page)
            GLib.timeout_add(poll_ms, render)
            window.present()

        application.connect("activate", activate)
        return int(application.run([]))



_PAGE_KEYS = {
    "overview": "Overview",
    "runs": "Active runs",
    "history": "History",
    "diagnostics": "Diagnostics",
    "repositories": "Repository",
    "targets": "Targets",
    "run": "Run",
    "settings": "Settings",
}


def _clear(Gtk: Any, box: Any) -> None:
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()


def _append_heading(Gtk: Any, box: Any, text: str) -> None:
    label = Gtk.Label(label=text, xalign=0.0)
    with _suppress_exception():
        label.add_css_class("title-3")
    box.append(label)


def _append_text(Gtk: Any, box: Any, text: str) -> None:
    label = Gtk.Label(label=text, xalign=0.0)
    label.set_wrap(True)
    label.set_selectable(True)
    box.append(label)


def _append_rows(Gtk: Any, box: Any, rows: list[tuple[str, str]]) -> None:
    grid = Gtk.Grid(column_spacing=16, row_spacing=8)
    row_index = 0
    for key, value in rows:
        key_label = Gtk.Label(label=key, xalign=0.0)
        with _suppress_exception():
            key_label.add_css_class("dim-label")
        value_label = Gtk.Label(label=value or "-", xalign=0.0)
        value_label.set_selectable(True)
        value_label.set_wrap(True)
        grid.attach(key_label, 0, row_index, 1, 1)
        grid.attach(value_label, 1, row_index, 1, 1)
        row_index += 1
    box.append(grid)


def _render_overview(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    repo = payload.get("repository", {})
    project = payload.get("project", {})
    runs = payload.get("active_runs", [])
    diagnostics = payload.get("diagnostics", [])
    _append_heading(Gtk, box, "Overview")
    _append_rows(Gtk, box, [
        ("Repository", str(repo.get("path") or "")),
        ("Branch", str(repo.get("branch") or "")),
        ("Head", str(repo.get("head") or "")),
        ("Dirty", "yes" if repo.get("dirty") else "no"),
        ("Project", str(project.get("kind") or "auto")),
        ("Target", str(project.get("selected_target") or "-")),
        ("Variant", str(project.get("variant") or "-")),
        ("Active runs", str(len(runs))),
        ("Diagnostics", str(len(diagnostics))),
    ])


def _render_runs(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Active runs")
    runs = payload.get("active_runs", [])
    if not runs:
        _append_text(Gtk, box, "No active run is currently published by the HUD state or IPC broker.")
        return
    for run in runs:
        if not isinstance(run, dict):
            continue
        _append_text(Gtk, box, f"{run.get('status', 'idle')} · {run.get('title', 'devtool')} · {run.get('percent', '-')}%")
        _append_rows(Gtk, box, [
            ("Run ID", str(run.get("run_id") or "")),
            ("Target", str(run.get("target") or "")),
            ("Phase", str(run.get("phase") or "")),
            ("Updated", str(run.get("updated_at") or "")),
            ("Log", str(run.get("log_path") or "")),
        ])


def _render_history(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "History")
    history = payload.get("history", [])
    if not history:
        _append_text(Gtk, box, "No artifact history entries were found in this repository.")
        return
    for item in history[-15:]:
        if not isinstance(item, dict):
            continue
        title = item.get("artifact") or item.get("artifact_name") or item.get("name") or item.get("history_id") or "artifact"
        _append_text(Gtk, box, f"{title}")
        _append_rows(Gtk, box, [
            ("Applied", str(item.get("applied_at") or item.get("created_at") or "")),
            ("Commit", str(item.get("commit_hash") or "")),
            ("SHA-256", str(item.get("sha256") or "")),
        ])


def _render_diagnostics(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Diagnostics")
    diagnostics = payload.get("diagnostics", [])
    if not diagnostics:
        _append_text(Gtk, box, "No warnings or errors are currently reported by the active run.")
        return
    for item in diagnostics[:50]:
        if not isinstance(item, dict):
            continue
        _append_text(Gtk, box, f"{item.get('severity', 'info')}: {item.get('message', '')}")
        detail_rows = []
        for key in ("code", "phase", "file"):
            value = str(item.get(key) or "")
            if value:
                detail_rows.append((key.title(), value))
        if detail_rows:
            _append_rows(Gtk, box, detail_rows)


def _render_repositories(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Repository")
    repo = payload.get("repository", {})
    _append_rows(Gtk, box, [
        ("Path", str(repo.get("path") or "")),
        ("Exists", "yes" if repo.get("exists") else "no"),
        ("Branch", str(repo.get("branch") or "")),
        ("Head", str(repo.get("head") or "")),
        ("Dirty", "yes" if repo.get("dirty") else "no"),
    ])


def _render_targets(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Targets")
    targets = payload.get("targets", [])
    if not targets:
        _append_text(Gtk, box, "No named targets were configured for this repository.")
        return
    for target in targets:
        if not isinstance(target, dict):
            continue
        _append_text(Gtk, box, f"{target.get('name', '')} · {target.get('kind', 'auto')}")
        _append_rows(Gtk, box, [
            ("Root", str(target.get("root") or "")),
            ("Build", str(target.get("build") or "")),
            ("Test", str(target.get("test") or "")),
            ("Run", str(target.get("run") or "")),
            ("Environment", str(target.get("environment") or "auto")),
            ("Chroot", str(target.get("chroot") or "")),
        ])


def _render_run_workflow(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
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
    _append_text(
        Gtk,
        box,
        "Builds a typed Devtool command from fields, renders a shell review, and launches only after the exact command is visible.",
    )

    form = Gtk.Grid(column_spacing=14, row_spacing=10)
    form.set_hexpand(True)
    box.append(form)
    row = 0
    widgets: dict[str, Any] = {}

    def add_label(text: str, r: int) -> None:
        label = Gtk.Label(label=text, xalign=0.0)
        with _suppress_exception():
            label.add_css_class("dim-label")
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
    add_entry("variant", "Variant", str(draft.get("variant") or ""), placeholder="optional")
    add_entry("project_type", "Project type", str(draft.get("project_type") or "auto"), placeholder="auto, python, android, dotnet…")
    add_entry("execution_environment", "Execution environment", str(draft.get("execution_environment") or "auto"), placeholder="auto, host, desktop-linux…")
    add_entry("artifact", "Overlay / bundle", str(draft.get("artifact") or ""), placeholder="required for apply-overlay/apply-overlays")
    add_entry("test_profile", "Test profile", str(draft.get("test_profile") or ""), placeholder="optional saved profile")
    add_entry("selectors", "Selectors", str(draft.get("selectors") or ""), placeholder="shell-split values, e.g. tests/foo.py::test_bar")
    add_entry("test_ids", "Test IDs", str(draft.get("test_ids") or ""), placeholder="exact framework IDs")
    add_entry("tags", "Include tags", str(draft.get("tags") or ""))
    add_entry("exclude_tags", "Exclude tags", str(draft.get("exclude_tags") or ""))
    add_entry("test_path", "Test path", str(draft.get("test_path") or ""))
    add_entry("test_class", "Test class", str(draft.get("test_class") or ""))
    add_entry("test_method", "Test method", str(draft.get("test_method") or ""))
    add_entry("test_filter", "Native filter", str(draft.get("test_filter") or ""))
    add_entry("test_tasks", "Native tasks", str(draft.get("test_tasks") or ""), placeholder=":app:testDebugUnitTest")
    add_entry("commit_message", "Commit message", str(draft.get("commit_message") or ""))
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

    checks = Gtk.FlowBox()
    checks.set_selection_mode(Gtk.SelectionMode.NONE)
    checks.set_max_children_per_line(3)
    checks.set_column_spacing(8)
    checks.set_row_spacing(8)
    box.append(checks)

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
    add_check("failed", "Failed tests", bool(draft.get("failed", False)))
    add_check("changed", "Changed tests", bool(draft.get("changed", False)))
    add_check("instrumented", "Instrumented", bool(draft.get("instrumented", False)))

    add_entry("changed_from", "Changed from", str(draft.get("changed_from") or "HEAD"))

    button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.append(button_row)
    preview = Gtk.TextView()
    preview.set_editable(False)
    preview.set_monospace(True)
    preview.set_vexpand(True)
    preview.set_hexpand(True)
    box.append(preview)
    status = Gtk.Label(label="", xalign=0.0)
    status.set_wrap(True)
    box.append(status)

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
            "test_profile": entry_text("test_profile"),
            "selectors": entry_text("selectors"),
            "test_ids": entry_text("test_ids"),
            "tags": entry_text("tags"),
            "exclude_tags": entry_text("exclude_tags"),
            "test_path": entry_text("test_path"),
            "test_class": entry_text("test_class"),
            "test_method": entry_text("test_method"),
            "test_filter": entry_text("test_filter"),
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

    def launch_command(_button: Any) -> None:
        review = review_payload(payload_from_mapping(current_mapping()))
        if not review.runnable:
            status.set_text("Command has errors; launch blocked.")
            return
        try:
            subprocess.Popen(["sh", "-lc", review.command], cwd=str(Path(current_mapping()["repository"]).expanduser()))
        except Exception as exc:  # pragma: no cover - host process/display dependent
            status.set_text(f"Launch failed: {exc}")
            return
        status.set_text("Launched Devtool command.")

    def dry_run(_button: Any) -> None:
        mapping = current_mapping()
        mapping["extra_operation_args"] = (str(mapping.get("extra_operation_args") or "") + " --dry-run").strip()
        review = review_payload(payload_from_mapping(mapping))
        buffer = preview.get_buffer()
        buffer.set_text(review.command or "No runnable dry-run command yet.")
        status.set_text("Dry-run command previewed; launch still uses the editable fields above.")

    for label, callback in (("Refresh preview", update_preview), ("Copy command", copy_command), ("Preview dry-run", dry_run), ("Launch", launch_command)):
        button = Gtk.Button(label=label)
        button.connect("clicked", callback)
        button_row.append(button)

    for widget in widgets.values():
        with _suppress_exception():
            widget.connect("changed", update_preview)
        with _suppress_exception():
            widget.connect("toggled", update_preview)
    update_preview()

def _render_settings(Gtk: Any, box: Any, payload: dict[str, Any]) -> None:
    _clear(Gtk, box)
    _append_heading(Gtk, box, "Settings")
    project = payload.get("project", {})
    settings = payload.get("settings", {})
    hud = payload.get("hud", {})
    ipc = payload.get("ipc", {})
    _append_rows(Gtk, box, [
        ("Config", str(project.get("config_path") or "")),
        ("Global config", str(project.get("global_config_path") or "")),
        ("HUD state", str(hud.get("directory") or "")),
        ("IPC", "connected" if ipc.get("ok") else str(ipc.get("error") or "unavailable")),
        ("Follow active", "yes" if settings.get("follow_active") else "no"),
        ("History limit", str(settings.get("history_limit") or 0)),
        ("Recent events", str(settings.get("recent_events") or 0)),
    ])
    _append_text(Gtk, box, "The Run workflow is handled by the GTK4 typed command page. It builds argv first and renders shell text only for review/copy.")


class _suppress_exception:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: Any) -> bool:
        return True
