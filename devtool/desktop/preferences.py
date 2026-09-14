from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(slots=True)
class DesktopPreferences:
    window_width: int = 1120
    window_height: int = 720
    selected_page: str = "overview"
    appearance: str = "system"
    reduced_motion: bool = False
    window_maximized: bool = False
    hud_details_expanded: bool = False
    high_contrast: bool = False
    compact_density: bool = False
    compact_navigation: bool = False
    hud_size_mode: str = "auto"
    filters: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, app_slug: str) -> "DesktopPreferences":
        path = preferences_path(app_slug)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DesktopPreferences":
        def integer(key: str, default: int) -> int:
            try:
                number = float(data.get(key, default))
                if not math.isfinite(number):
                    return default
                return int(number)
            except (TypeError, ValueError, OverflowError):
                return default

        def boolean(key: str, env_key: str = "") -> bool:
            value = data.get(key)
            if value is None and env_key:
                value = os.environ.get(env_key, "")
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        appearance = str(data.get("appearance") or os.environ.get("DEVTOOL_DESKTOP_APPEARANCE") or "system").lower()
        if appearance not in {"system", "light", "dark"}:
            appearance = "system"
        hud_size_mode = str(data.get("hud_size_mode") or "auto").lower()
        if hud_size_mode not in {"compact", "auto", "expanded"}:
            hud_size_mode = "auto"
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        clean_filters = {str(key): str(value) for key, value in filters.items() if str(key)}
        return cls(
            window_width=max(720, min(2400, integer("window_width", 1120))),
            window_height=max(520, min(1800, integer("window_height", 720))),
            selected_page=str(data.get("selected_page") or "overview"),
            appearance=appearance,
            reduced_motion=boolean("reduced_motion", "DEVTOOL_REDUCED_MOTION"),
            window_maximized=boolean("window_maximized"),
            hud_details_expanded=boolean("hud_details_expanded"),
            high_contrast=boolean("high_contrast", "DEVTOOL_HIGH_CONTRAST"),
            compact_density=boolean("compact_density", "DEVTOOL_COMPACT_DENSITY"),
            compact_navigation=boolean("compact_navigation"),
            hud_size_mode=hud_size_mode,
            filters=clean_filters,
        )


    def resolved_ui_preferences(self):
        """Resolve shared UI03 preferences while preserving legacy desktop overrides.

        Window geometry/navigation remain desktop-local state. Appearance, density,
        and motion are now semantic UI Core preferences; legacy stored values act
        only as explicit desktop-surface overrides for compatibility.
        """
        from ..ui_core.preferences import resolve_user_ui_preferences

        overrides: dict[str, Any] = {}
        if self.appearance in {"light", "dark"}:
            overrides["theme"] = self.appearance
        if self.compact_density:
            overrides["density"] = "compact"
        if self.reduced_motion:
            overrides["motion"] = "reduced"
        return resolve_user_ui_preferences(surface="desktop", overrides=overrides)

    def filter_value(self, page: str, name: str, default: str = "") -> str:
        return str(self.filters.get(f"{page}.{name}", default))

    def set_filter_value(self, page: str, name: str, value: object) -> None:
        key = f"{page}.{name}"
        text = str(value or "")
        if text:
            self.filters[key] = text
        else:
            self.filters.pop(key, None)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, app_slug: str) -> Path:
        path = preferences_path(app_slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                tmp_name = handle.name
                os.chmod(tmp_name, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
            _fsync_directory(path.parent)
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except Exception:
                    pass
        return path


def preferences_path(app_slug: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in app_slug).strip("-.") or "devtool"
    return config_home() / "devtool" / f"{safe}.json"
