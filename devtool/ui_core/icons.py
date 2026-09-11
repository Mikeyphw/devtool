from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping

ICON_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class IconSpec:
    id: str
    unicode: str
    ascii: str
    nerd: str = ""
    native: str = ""


_DEF = (
    IconSpec("status.success", "✓", "[OK]", "󰄬", "emblem-ok-symbolic"),
    IconSpec("status.running", "◆", "[*]", "󰔟", "media-playback-start-symbolic"),
    IconSpec("status.pending", "○", "[ ]", "󰄱", "content-loading-symbolic"),
    IconSpec("status.failed", "✗", "[ERR]", "󰅙", "dialog-error-symbolic"),
    IconSpec("status.warning", "!", "[WARN]", "󰀪", "dialog-warning-symbolic"),
    IconSpec("status.skipped", "–", "[-]", "󰒭", "media-skip-forward-symbolic"),
    IconSpec("status.cancelled", "×", "[X]", "󰜺", "process-stop-symbolic"),
    IconSpec("status.blocked", "⊘", "[BLOCK]", "󰂭", "action-unavailable-symbolic"),
    IconSpec("severity.info", "i", "[INFO]", "󰋼", "dialog-information-symbolic"),
    IconSpec("severity.warning", "⚠", "[WARN]", "󰀪", "dialog-warning-symbolic"),
    IconSpec("severity.error", "❌", "[ERR]", "󰅙", "dialog-error-symbolic"),
    IconSpec("action.stop", "■", "[STOP]", "󰓛", "process-stop-symbolic"),
    IconSpec("action.retry", "↻", "[RETRY]", "󰑓", "view-refresh-symbolic"),
    IconSpec("action.rerun", "↻", "[RUN]", "󰑓", "view-refresh-symbolic"),
    IconSpec("action.logs", "≡", "[LOG]", "󰌱", "text-x-generic-symbolic"),
    IconSpec("action.copy", "⧉", "[COPY]", "󰆏", "edit-copy-symbolic"),
    IconSpec("action.copy-diagnostics", "⧉", "[COPY]", "󰆏", "edit-copy-symbolic"),
    IconSpec("action.apply", "↓", "[APPLY]", "󰁪", "document-save-symbolic"),
    IconSpec("action.rollback", "↶", "[UNDO]", "󰕌", "edit-undo-symbolic"),
    IconSpec("action.retry-failed", "↻", "[RETRY]", "󰑓", "view-refresh-symbolic"),
    IconSpec("action.resume", "▶", "[GO]", "󰐊", "media-playback-start-symbolic"),
    IconSpec("action.build", "▣", "[BUILD]", "󰣪", "system-run-symbolic"),
    IconSpec("action.test", "✓", "[TEST]", "󰙨", "applications-science-symbolic"),
    IconSpec("action.validate", "✓", "[CHECK]", "󰄬", "emblem-ok-symbolic"),
    IconSpec("action.package", "◇", "[PKG]", "󰏗", "package-x-generic-symbolic"),
    IconSpec("action.inspect", "⌕", "[VIEW]", "󰍉", "system-search-symbolic"),
    IconSpec("action.retry-node", "↻", "[RETRY]", "󰑓", "view-refresh-symbolic"),
    IconSpec("action.tests", "☑", "[TESTS]", "󰙨", "applications-science-symbolic"),
    IconSpec("action.clear", "×", "[CLEAR]", "󰆴", "edit-clear-symbolic"),
    IconSpec("action.refresh", "↻", "[REFRESH]", "󰑓", "view-refresh-symbolic"),
    IconSpec("nav.dashboard", "⌂", "[HOME]", "󰋜", "go-home-symbolic"),
    IconSpec("nav.operations", "▶", "[RUN]", "󰐊", "media-playback-start-symbolic"),
    IconSpec("nav.history", "↺", "[HIST]", "󰋚", "document-open-recent-symbolic"),
    IconSpec("nav.artifacts", "◇", "[ART]", "󰏗", "package-x-generic-symbolic"),
    IconSpec("nav.diagnostics", "!", "[ISSUE]", "󰀪", "dialog-warning-symbolic"),
    IconSpec("nav.projects", "⌘", "[REPO]", "󰳏", "folder-symbolic"),
    IconSpec("nav.targets", "◎", "[TARGET]", "󰓾", "mark-location-symbolic"),
    IconSpec("nav.validation", "+", "[RUN]", "󰐊", "system-run-symbolic"),
    IconSpec("nav.workflows", "⇢", "[FLOW]", "󰘬", "network-workgroup-symbolic"),
    IconSpec("nav.logs", "≡", "[LOG]", "󰌱", "text-x-generic-symbolic"),
    IconSpec("nav.environment", "◫", "[ENV]", "󰒋", "computer-symbolic"),
    IconSpec("nav.settings", "⚙", "[SET]", "󰒓", "emblem-system-symbolic"),
    IconSpec("nav.build", "▶", "[BUILD]", "󰣪", "system-run-symbolic"),
    IconSpec("nav.overlays", "◇", "[OVL]", "󰏗", "package-x-generic-symbolic"),
)
ICON_REGISTRY = {item.id: item for item in _DEF}


def icon_spec(icon_id: str) -> IconSpec:
    return ICON_REGISTRY.get(icon_id) or IconSpec(icon_id, "•", "[*]", "", "")


def _auto_mode(surface: str, env: Mapping[str, str]) -> str:
    if str(env.get("DEVTOOL_ASCII", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return "ascii"
    if surface == "desktop" or surface == "android":
        return "unicode"
    nerd = str(env.get("DEVTOOL_NERD_FONT", "") or "").strip().lower()
    disabled = str(env.get("DEVTOOL_NO_NERD_FONT", "") or "").strip().lower()
    if nerd in {"1", "true", "yes", "on"} and disabled not in {"1", "true", "yes", "on"}:
        return "nerd"
    return "unicode"


def resolve_icon(icon_id: str, *, mode: str = "auto", surface: str = "machine", env: Mapping[str, str] | None = None) -> str:
    environment = env or os.environ
    requested = str(mode or "auto").strip().lower()
    resolved = _auto_mode(surface, environment) if requested == "auto" else requested
    spec = icon_spec(icon_id)
    if resolved == "ascii":
        return spec.ascii
    if resolved == "nerd":
        return spec.nerd or spec.unicode or spec.ascii
    if resolved == "native":
        return spec.native or spec.unicode or spec.ascii
    return spec.unicode or spec.ascii


def icon_catalog() -> list[dict[str, Any]]:
    return [asdict(item) for item in ICON_REGISTRY.values()]
