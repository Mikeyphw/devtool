from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import OperationResult, ProjectConfig


APPLY_NOTIFICATION_ID = "7101"


@dataclass(slots=True)
class ApplyNotification:
    title: str
    body: str
    toast: str
    level: str = "success"
    should_emit: bool = True
    bell: bool = False


def _falsey(value: str) -> bool:
    return value.strip().lower() in {"0", "false", "no", "off"}


def apply_notifications_enabled(args: Any) -> bool:
    if _falsey(os.environ.get("DEVTOOL_APPLY_NOTIFICATION", "1")):
        return False
    return not bool(getattr(args, "no_apply_notification", False))


def _command_label(command: str) -> str:
    if command in {"apply-snapshot", "as"}:
        return "snapshot"
    if command in {"apply-overlays", "aos"}:
        return "overlay bundle"
    return "overlay"


def _artifact_names(args: Any, results: list[OperationResult]) -> str:
    paths: list[str] = []
    for result in results:
        metadata = result.metadata or {}
        for key in ("artifact", "artifact_path", "overlay_artifact"):
            value = str(metadata.get(key) or "").strip()
            if value:
                paths.append(value)
    raw = getattr(args, "artifact", "")
    if isinstance(raw, (list, tuple)):
        paths.extend(str(item) for item in raw)
    elif raw:
        paths.append(str(raw))
    names = []
    for value in paths:
        name = Path(value).name or value
        if name and name not in names:
            names.append(name)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{len(names)} artifacts"


def _commit_hash(results: list[OperationResult]) -> str:
    for result in reversed(results):
        if result.name not in {"git-commit", "commit"}:
            continue
        metadata = result.metadata or {}
        for key in ("commit", "commit_hash", "git_commit", "sha"):
            value = str(metadata.get(key) or "").strip()
            if 7 <= len(value) <= 40:
                return value[:12]
    return ""


def build_apply_notification(
    args: Any,
    config: ProjectConfig | None,
    results: list[OperationResult],
    totals: dict[str, int],
) -> ApplyNotification:
    command = str(getattr(args, "command", "") or "")
    label = _command_label(command)
    failed = int(totals.get("failed", 0) or 0)
    warnings = int(totals.get("warnings", 0) or 0)
    rolled_back = any(
        result.name == "apply-rollback"
        and result.returncode == 0
        and result.failure_kind != "stale_apply_rolled_back"
        for result in results
    )
    paused_result = next((
        result for result in reversed(results)
        if result.name == "validation-paused" or result.metadata.get("validation_complete") is False
    ), None)
    if rolled_back:
        title = f"Devtool {label} rolled back"
        status = "Rolled back"
    elif paused_result is not None:
        title = f"Devtool {label} validation paused"
        status = "Validation paused"
    elif failed:
        title = f"Devtool {label} failed"
        status = "Failed"
    else:
        title = f"Devtool {label} applied"
        status = "Applied"

    bits: list[str] = []
    repo = config.repo_root.name if config is not None else ""
    if repo:
        bits.append(repo)
    artifact = _artifact_names(args, results)
    if artifact:
        bits.append(artifact)
    commit = _commit_hash(results)
    if commit:
        bits.append(f"commit {commit}")
    if paused_result is not None and not rolled_back:
        metadata = paused_result.metadata or {}
        completed = int(metadata.get("completed_phases", 0) or 0)
        total = int(metadata.get("phase_total", 0) or 0)
        next_phase = str(metadata.get("next_phase") or "").strip()
        if total:
            bits.append(f"phase {completed}/{total}")
        if next_phase:
            bits.append(f"next {next_phase}")
    elif failed and not rolled_back:
        bits.append(f"{failed} failed step{'s' if failed != 1 else ''}")
    elif warnings:
        bits.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    seconds = sum(max(0.0, float(result.duration_seconds or 0.0)) for result in results)
    bits.append(f"{seconds:.1f}s")

    body = " · ".join(bits) if bits else status
    toast = f"{status}: {artifact or label}"
    level = "critical" if failed or rolled_back else "important" if warnings or paused_result is not None else "success"
    return ApplyNotification(title=title, body=body, toast=toast, level=level)


def notify_apply_finished(
    args: Any,
    config: ProjectConfig | None,
    results: list[OperationResult],
    totals: dict[str, int],
) -> None:
    if not apply_notifications_enabled(args):
        return
    notification = build_apply_notification(args, config, results, totals)
    from .ui_core.notifications import notification_for_event
    from .ui_core.preferences import resolve_ui_preferences, resolve_user_ui_preferences
    preferences = resolve_ui_preferences(config.ui_config, surface="android" if _prefer_android_side() else "desktop") if config is not None else resolve_user_ui_preferences(surface="android" if _prefer_android_side() else "desktop")
    event = "failed" if notification.level == "critical" else "warning" if notification.level == "important" else "success"
    semantic = notification_for_event(
        event,
        status="failed" if event == "failed" else "warning" if event == "warning" else "ok",
        severity="error" if event == "failed" else "warning" if event == "warning" else "",
        title=notification.title,
        body=notification.body,
        requires_action=bool(event == "failed" and any(result.name == "validation-paused" for result in results)),
        policy=preferences.notification_policy,
        actions=("logs.open", "operation.retry") if event == "failed" else ("logs.open",),
        notification_id=f"apply:{_artifact_names(args, results) or _command_label(str(getattr(args, 'command', '') or ''))}",
    )
    notification.level = semantic.level
    notification.should_emit = semantic.should_emit
    notification.bell = semantic.bell
    if notification.should_emit:
        post_apply_notification(notification)


def post_apply_notification(notification: ApplyNotification) -> None:
    if not notification.should_emit:
        return
    if _prefer_android_side():
        _post_android(notification)
        return
    from .notification_controls import os_output_enabled
    if not os_output_enabled("notifications", android=False):
        return
    if notification.bell and sys.stderr.isatty():
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
        except OSError:
            pass
    _post_desktop(notification)


def _prefer_android_side() -> bool:
    prefix = os.environ.get("PREFIX", "")
    if os.environ.get("TERMUX_VERSION") or "/com.termux/" in prefix or prefix.startswith("/data/data/com.termux/"):
        return True
    if os.environ.get("ANDROID_ROOT") and os.environ.get("ANDROID_DATA"):
        return True
    if os.environ.get("DEVTOOL_AUTO_CHROOT_ACTIVE") or os.environ.get("DEVTOOL_INSIDE_TERMUX_CHROOT"):
        return True
    if os.environ.get("DEVTOOL_HUD_BRIDGE_DIR"):
        return True
    termux_home = os.environ.get("DEVTOOL_TERMUX_HOME", "").strip()
    return bool(termux_home and Path(termux_home).expanduser().exists())


def _run_quiet(argv: list[str], *, timeout: float = 3.0) -> bool:
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _post_desktop(notification: ApplyNotification) -> None:
    if shutil.which("notify-send") is None:
        return
    _run_quiet([
        "notify-send",
        "--app-name=devtool",
        f"--urgency={'critical' if notification.level == 'critical' else 'normal'}",
        "--expire-time=5000",
        "--hint=string:x-canonical-private-synchronous:devtool-apply",
        notification.title,
        notification.body,
    ])


def _post_android(notification: ApplyNotification) -> None:
    from .notification_controls import os_output_enabled, output_mode, companion_installed

    # One positive check, shared across notification and toast decisions.
    # Explicit "off" is never overridden. "on" opts back in despite the app.
    modes = (output_mode("notifications"), output_mode("toasts"))
    app_detected = companion_installed() if "auto" in modes else False
    if os_output_enabled("notifications", android=True, app_detected=app_detected):
        if notification.bell and sys.stderr.isatty():
            try:
                sys.stderr.write("\a")
                sys.stderr.flush()
            except OSError:
                pass
        if modes[0] == "on" or not _android_app_run_notification_active():
            _post_termux_notification(notification)
    if os_output_enabled("toasts", android=True, app_detected=app_detected):
        toasted = _post_termux_toast(notification.toast)
        if not toasted:
            _post_android_bridge_toast(notification)


def _android_app_run_notification_active() -> bool:
    return bool(
        os.environ.get("DEVTOOL_HUD_BRIDGE_DIR", "").strip()
        and os.environ.get("DEVTOOL_RUN_ID", "").strip()
    )


def _post_termux_notification(notification: ApplyNotification) -> bool:
    if shutil.which("termux-notification") is None:
        return False
    return _run_quiet([
        "termux-notification",
        "--id",
        APPLY_NOTIFICATION_ID,
        "--title",
        notification.title,
        "--content",
        notification.body,
    ])


def _post_termux_toast(text: str) -> bool:
    if shutil.which("termux-toast") is None:
        return False
    return _run_quiet(["termux-toast", "-s", text])


def _post_android_bridge_toast(notification: ApplyNotification) -> bool:
    try:
        from .android_live_hud import AndroidLiveHudBridge
    except Exception:
        return False
    payload: dict[str, Any] = {
        "run_id": os.environ.get("DEVTOOL_RUN_ID", "") or APPLY_NOTIFICATION_ID,
        "toast": notification.toast,
    }
    return AndroidLiveHudBridge().send("toast", payload)
