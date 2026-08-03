from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"), r"\1<redacted>"),
    (re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|github[_-]?token|token|secret|password|passwd|pwd)\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1<redacted>"),
    (re.compile(r"\bgh[opsur]_[A-Za-z0-9_]{16,}\b"), "<redacted-github-token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "<redacted-api-key>"),
)


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state").expanduser()


def gtk_log_dir() -> Path:
    return state_home() / "devtool"


def gtk_log_path(app_slug: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(app_slug or "gtk")).strip("-.") or "gtk"
    return gtk_log_dir() / f"{safe}.log"


def redact_text(value: object) -> str:
    text = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def format_exception(exc: BaseException) -> str:
    return redact_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


def append_gtk_log(app_slug: str, message: str, exc: BaseException | None = None) -> Path:
    path = gtk_log_path(app_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = [f"[{now}] pid={os.getpid()} {redact_text(message).rstrip()}"]
    if exc is not None:
        parts.append(format_exception(exc).rstrip())
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(part for part in parts if part))
        handle.write("\n")
    return path


def report_cli_exception(app_slug: str, command_name: str, exc: BaseException) -> int:
    path = append_gtk_log(app_slug, f"{command_name} failed", exc)
    first_line = redact_text(str(exc)).splitlines()[0] if str(exc) else exc.__class__.__name__
    print(f"{command_name}: {first_line}", file=sys.stderr)
    print(f"{command_name}: details logged to {path}", file=sys.stderr)
    return 2


def install_exception_hooks(app_slug: str) -> None:
    previous_hook = sys.excepthook

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        append_gtk_log(app_slug, "Unhandled GTK main-thread exception", exc)
        previous_hook(exc_type, exc, tb)

    sys.excepthook = excepthook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def thread_hook(args: threading.ExceptHookArgs) -> None:
            append_gtk_log(app_slug, f"Unhandled GTK worker-thread exception in {args.thread.name}", args.exc_value)
            previous_thread_hook(args)

        threading.excepthook = thread_hook


@contextmanager
def suppress_gtk_error() -> Any:
    try:
        yield
    except Exception:
        return


def show_startup_error_window(
    *,
    Gtk: Any,
    Adw: Any,
    app: Any,
    app_slug: str,
    title: str,
    exc: BaseException,
    log_path: Path,
) -> bool:
    try:
        details = format_exception(exc)
        message = redact_text(str(exc)).splitlines()[0] if str(exc) else exc.__class__.__name__
        if Adw is not None:
            window = Adw.ApplicationWindow(application=app)
        else:
            window = Gtk.ApplicationWindow(application=app)
        window.set_title(title)
        window.set_default_size(760, 480)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)

        heading = Gtk.Label(label=title)
        heading.set_xalign(0.0)
        with suppress_gtk_error():
            heading.add_css_class("title-2")
        root.append(heading)

        body = Gtk.Label(label=message)
        body.set_xalign(0.0)
        body.set_wrap(True)
        root.append(body)

        log_label = Gtk.Label(label=f"Full details were written to {log_path}")
        log_label.set_xalign(0.0)
        log_label.set_wrap(True)
        with suppress_gtk_error():
            log_label.add_css_class("dim-label")
        root.append(log_label)

        text = Gtk.TextView()
        text.set_editable(False)
        text.set_cursor_visible(False)
        text.set_monospace(True)
        text.get_buffer().set_text(details)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(text)
        scroller.set_vexpand(True)
        with suppress_gtk_error():
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        root.append(scroller)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        copy_button = Gtk.Button(label="Copy details")
        open_button = Gtk.Button(label="Open log")
        quit_button = Gtk.Button(label="Quit")
        actions.append(copy_button)
        actions.append(open_button)
        actions.append(quit_button)
        root.append(actions)

        def copy_details(_button: Any) -> None:
            with suppress_gtk_error():
                Gtk.Widget.get_clipboard(window).set(details)

        def open_log(_button: Any) -> None:
            with suppress_gtk_error():
                subprocess.Popen(["xdg-open", str(log_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        copy_button.connect("clicked", copy_details)
        open_button.connect("clicked", open_log)
        quit_button.connect("clicked", lambda _button: app.quit())

        if Adw is not None:
            window.set_content(root)
        else:
            window.set_child(root)
        window.present()
        append_gtk_log(app_slug, f"Presented startup error window for {title}")
        return True
    except Exception as dialog_exc:
        append_gtk_log(app_slug, "Failed to present GTK startup error window", dialog_exc)
        return False


def wrap_periodic_callback(
    *,
    app_slug: str,
    callback: Callable[[], bool],
    on_error: Callable[[BaseException, Path], None] | None = None,
) -> Callable[[], bool]:
    def wrapped() -> bool:
        try:
            return bool(callback())
        except Exception as exc:
            path = append_gtk_log(app_slug, "GTK periodic callback failed", exc)
            if on_error is not None:
                with suppress_gtk_error():
                    on_error(exc, path)
            return True

    return wrapped

_GTK_THREAD_GUARD_INSTALLED = False


def gtk_thread_guard_enabled() -> bool:
    return os.environ.get("DEVTOOL_GTK_THREAD_GUARD", "").strip().lower() in {"1", "true", "yes", "on"}


def install_gtk_thread_guard(Gtk: Any, *, app_slug: str = "gtk") -> bool:
    """Install an opt-in detector for GTK mutations from worker threads.

    The guard is intentionally enabled only under ``DEVTOOL_GTK_THREAD_GUARD``
    so production desktops do not risk GI monkey-patch surprises. Startup and
    release probes can turn it on to make cross-thread UI mutations fail loudly
    instead of becoming heisenbugs with tiny hats.
    """
    global _GTK_THREAD_GUARD_INSTALLED
    if _GTK_THREAD_GUARD_INSTALLED or not gtk_thread_guard_enabled():
        return False
    main_thread = threading.get_ident()
    targets = (
        (getattr(Gtk, "Widget", None), ("set_child", "set_parent", "set_visible", "set_sensitive")),
        (getattr(Gtk, "Window", None), ("set_child", "present", "close")),
        (getattr(Gtk, "Label", None), ("set_label", "set_text")),
        (getattr(Gtk, "Box", None), ("append", "prepend", "remove")),
        (getattr(Gtk, "TextBuffer", None), ("set_text", "insert")),
    )

    def wrap(owner: Any, method_name: str) -> None:
        if owner is None or not hasattr(owner, method_name):
            return
        original = getattr(owner, method_name)
        if getattr(original, "_devtool_thread_guard", False):
            return

        def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
            if threading.get_ident() != main_thread:
                exc = RuntimeError(f"GTK mutation from non-main thread: {owner.__name__}.{method_name}")
                append_gtk_log(app_slug, "GTK thread-guard violation", exc)
                raise exc
            return original(self, *args, **kwargs)

        try:
            setattr(guarded, "_devtool_thread_guard", True)
            setattr(owner, method_name, guarded)
        except Exception:
            return

    for owner, names in targets:
        for name in names:
            wrap(owner, name)
    _GTK_THREAD_GUARD_INSTALLED = True
    append_gtk_log(app_slug, "GTK thread guard installed")
    return True
