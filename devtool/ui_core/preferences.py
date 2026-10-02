from __future__ import annotations

import math
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .confirmations import DEFAULT_CONFIRMATION_POLICY, normalize_confirmation_policy
from .notifications import DEFAULT_NOTIFICATION_POLICY, normalize_notification_policy


UI_PROFILE_NAMES = ("minimal", "standard", "developer", "debug")
UI_SURFACES = ("machine", "desktop", "tui", "hud", "android")

_DEFAULTS: dict[str, Any] = {
    "theme": "auto",
    "density": "comfortable",
    "icons": "auto",
    "motion": "auto",
    "verbosity": "normal",
    "timestamps": "relative",
    "duration_format": "human",
    "confirm_destructive": True,
    "confirm_cancel": False,
    "show_completed_steps": True,
    "show_environment": "when-relevant",
    "show_commands": "on-error",
    "show_diagnostics": "expanded-on-error",
    "sidebar": "auto",
    "mouse": True,
    "max_steps": 0,
    "notification_detail": "normal",
}

_PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "minimal": {
        "density": "dense",
        "verbosity": "compact",
        "show_completed_steps": False,
        "show_environment": "never",
        "show_commands": "never",
        "show_diagnostics": "collapsed",
        "max_steps": 4,
    },
    "standard": {},
    "developer": {
        "verbosity": "detailed",
        "show_environment": "always",
        "show_commands": "on-error",
        "show_diagnostics": "expanded-on-error",
    },
    "debug": {
        "verbosity": "debug",
        "timestamps": "absolute",
        "duration_format": "precise",
        "show_environment": "always",
        "show_commands": "always",
        "show_diagnostics": "all",
    },
}

_SURFACE_DEFAULTS: dict[str, dict[str, Any]] = {
    "machine": {"density": "detailed"},
    "desktop": {"density": "comfortable", "sidebar": "expanded"},
    "tui": {"density": "compact", "sidebar": "auto", "mouse": True},
    "hud": {"density": "dense", "max_steps": 6, "show_environment": "when-relevant"},
    "android": {"density": "compact", "sidebar": "collapsed", "notification_detail": "normal"},
}

_CHOICES: dict[str, set[str]] = {
    "theme": {"auto", "light", "dark"},
    "density": {"dense", "compact", "comfortable", "detailed"},
    "icons": {"auto", "nerd", "unicode", "ascii", "native"},
    "motion": {"auto", "full", "reduced"},
    "verbosity": {"compact", "normal", "detailed", "debug"},
    "timestamps": {"relative", "absolute", "off"},
    "duration_format": {"human", "precise"},
    "show_environment": {"never", "when-relevant", "always"},
    "show_commands": {"never", "on-error", "always"},
    "show_diagnostics": {"collapsed", "expanded-on-error", "all"},
    "sidebar": {"auto", "collapsed", "expanded"},
    "notification_detail": {"minimal", "normal", "detailed"},
}
_BOOL_KEYS = {"confirm_destructive", "confirm_cancel", "show_completed_steps", "mouse"}
_INT_KEYS = {"max_steps"}
_META_KEYS = {"profile", "profiles", "confirmations", "notifications", *UI_SURFACES}


class UIPreferencesError(ValueError):
    pass


def normalize_surface(value: str) -> str:
    surface = str(value or "machine").strip().lower().replace("_", "-")
    aliases = {
        "gtk": "desktop",
        "gtk4": "desktop",
        "terminal": "tui",
        "console": "tui",
        "notification": "android",
        "live-hud": "hud",
    }
    surface = aliases.get(surface, surface)
    return surface if surface in UI_SURFACES else "machine"


def _bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise UIPreferencesError(f"Invalid UI boolean for {key}: {value!r}")


def _normalize_value(key: str, value: Any) -> Any:
    if key in _CHOICES:
        normalized = str(value or "").strip().lower()
        if normalized not in _CHOICES[key]:
            choices = ", ".join(sorted(_CHOICES[key]))
            raise UIPreferencesError(f"Invalid UI {key}={value!r}; expected one of: {choices}")
        return normalized
    if key in _BOOL_KEYS:
        return _bool(value, key=key)
    if key in _INT_KEYS:
        try:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("non-finite integer")
            result = int(number)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UIPreferencesError(f"Invalid UI integer for {key}: {value!r}") from exc
        if result < 0:
            raise UIPreferencesError(f"Invalid UI {key}={value!r}; expected >= 0")
        return result
    raise UIPreferencesError(f"Unknown UI preference: {key}")


def validate_ui_config(config: Mapping[str, Any] | None) -> None:
    raw = config or {}
    if not isinstance(raw, Mapping):
        raise UIPreferencesError("[ui] must be a TOML table")
    for key, value in raw.items():
        if key == "profile":
            if not str(value or "").strip():
                raise UIPreferencesError("[ui].profile must not be empty")
            continue
        if key == "confirmations":
            if not isinstance(value, Mapping):
                raise UIPreferencesError("[ui.confirmations] must be a TOML table")
            try:
                normalize_confirmation_policy(value)
            except ValueError as exc:
                raise UIPreferencesError(str(exc)) from exc
            continue
        if key == "notifications":
            if not isinstance(value, Mapping):
                raise UIPreferencesError("[ui.notifications] must be a TOML table")
            try:
                normalize_notification_policy(value)
            except ValueError as exc:
                raise UIPreferencesError(str(exc)) from exc
            continue
        if key == "profiles":
            if not isinstance(value, Mapping):
                raise UIPreferencesError("[ui.profiles] must be a TOML table")
            for profile_name, profile_values in value.items():
                if not isinstance(profile_values, Mapping):
                    raise UIPreferencesError(f"[ui.profiles.{profile_name}] must be a TOML table")
                for profile_key, profile_value in profile_values.items():
                    if profile_key == "extends":
                        continue
                    _normalize_value(str(profile_key), profile_value)
            continue
        if key in UI_SURFACES:
            if not isinstance(value, Mapping):
                raise UIPreferencesError(f"[ui.{key}] must be a TOML table")
            for surface_key, surface_value in value.items():
                _normalize_value(str(surface_key), surface_value)
            continue
        _normalize_value(str(key), value)


def _custom_profile(config: Mapping[str, Any], name: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
    profiles = config.get("profiles", {}) if isinstance(config.get("profiles", {}), Mapping) else {}
    custom = profiles.get(name)
    if custom is None:
        if name in _PROFILE_OVERRIDES:
            return dict(_PROFILE_OVERRIDES[name])
        raise UIPreferencesError(f"Unknown UI profile: {name}")
    if not isinstance(custom, Mapping):
        raise UIPreferencesError(f"[ui.profiles.{name}] must be a TOML table")
    if name in stack:
        cycle = " -> ".join((*stack, name))
        raise UIPreferencesError(f"UI profile inheritance cycle: {cycle}")
    parent = str(custom.get("extends") or "standard").strip().lower()
    if parent == name:
        raise UIPreferencesError(f"UI profile {name!r} cannot extend itself")
    result = _custom_profile(config, parent, (*stack, name)) if parent else {}
    for key, value in custom.items():
        if key != "extends":
            result[str(key)] = _normalize_value(str(key), value)
    return result


@dataclass(slots=True)
class UIPreferences:
    surface: str = "machine"
    profile: str = "standard"
    theme: str = "auto"
    density: str = "comfortable"
    icons: str = "auto"
    motion: str = "auto"
    verbosity: str = "normal"
    timestamps: str = "relative"
    duration_format: str = "human"
    confirm_destructive: bool = True
    confirm_cancel: bool = False
    show_completed_steps: bool = True
    show_environment: str = "when-relevant"
    show_commands: str = "on-error"
    show_diagnostics: str = "expanded-on-error"
    sidebar: str = "auto"
    mouse: bool = True
    max_steps: int = 0
    notification_detail: str = "normal"
    confirmation_policy: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CONFIRMATION_POLICY))
    notification_policy: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_NOTIFICATION_POLICY))
    provenance: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _apply(values: dict[str, Any], provenance: dict[str, str], layer: Mapping[str, Any], source: str) -> None:
    for key, value in layer.items():
        key = str(key)
        if key in _META_KEYS or key == "extends":
            continue
        values[key] = _normalize_value(key, value)
        provenance[key] = source


def _environment_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mapping = {
        "theme": "DEVTOOL_UI_THEME",
        "density": "DEVTOOL_UI_DENSITY",
        "icons": "DEVTOOL_UI_ICONS",
        "motion": "DEVTOOL_UI_MOTION",
        "verbosity": "DEVTOOL_UI_VERBOSITY",
        "timestamps": "DEVTOOL_UI_TIMESTAMPS",
        "duration_format": "DEVTOOL_UI_DURATION_FORMAT",
        "show_environment": "DEVTOOL_UI_SHOW_ENVIRONMENT",
        "show_commands": "DEVTOOL_UI_SHOW_COMMANDS",
        "show_diagnostics": "DEVTOOL_UI_SHOW_DIAGNOSTICS",
        "sidebar": "DEVTOOL_UI_SIDEBAR",
        "notification_detail": "DEVTOOL_UI_NOTIFICATION_DETAIL",
        "max_steps": "DEVTOOL_UI_MAX_STEPS",
        "confirm_destructive": "DEVTOOL_UI_CONFIRM_DESTRUCTIVE",
        "confirm_cancel": "DEVTOOL_UI_CONFIRM_CANCEL",
        "show_completed_steps": "DEVTOOL_UI_SHOW_COMPLETED_STEPS",
        "mouse": "DEVTOOL_UI_MOUSE",
    }
    for key, env_key in mapping.items():
        raw = str(env.get(env_key, "") or "").strip()
        if raw:
            result[key] = raw
    return result


def resolve_ui_preferences(
    ui_config: Mapping[str, Any] | None = None,
    *,
    surface: str = "machine",
    overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> UIPreferences:
    config = dict(ui_config or {})
    validate_ui_config(config)
    environment = os.environ if env is None else env
    resolved_surface = normalize_surface(surface)
    cli_overrides = dict(overrides or {})
    requested_profile = str(
        cli_overrides.pop("profile", "")
        or environment.get("DEVTOOL_UI_PROFILE", "")
        or config.get("profile", "auto")
        or "auto"
    ).strip().lower()
    profile = "standard" if requested_profile == "auto" else requested_profile

    values = dict(_DEFAULTS)
    provenance = {key: "default" for key in values}
    profile_values = _custom_profile(config, profile)
    _apply(values, provenance, profile_values, f"profile:{profile}")
    _apply(values, provenance, _SURFACE_DEFAULTS.get(resolved_surface, {}), f"surface:{resolved_surface}")
    _apply(values, provenance, config, "ui")
    surface_config = config.get(resolved_surface, {})
    if isinstance(surface_config, Mapping):
        _apply(values, provenance, surface_config, f"ui.{resolved_surface}")
    _apply(values, provenance, _environment_overrides(environment), "environment")
    _apply(values, provenance, cli_overrides, "cli")

    confirmation_policy = normalize_confirmation_policy(config.get("confirmations") if isinstance(config.get("confirmations"), Mapping) else None)
    notification_policy = normalize_notification_policy(config.get("notifications") if isinstance(config.get("notifications"), Mapping) else None)
    # Legacy booleans remain authoritative compatibility shims.
    if not values.get("confirm_destructive", True):
        confirmation_policy["rollback"] = "never"
        confirmation_policy["delete_artifact"] = "never"
        confirmation_policy["snapshot_overwrite"] = "never"
    if values.get("confirm_cancel", False):
        confirmation_policy["cancel"] = "always"

    return UIPreferences(surface=resolved_surface, profile=profile, confirmation_policy=confirmation_policy, notification_policy=notification_policy, provenance=provenance, **values)



def user_ui_config_path(env: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    explicit = str(environment.get("DEVTOOL_GLOBAL_CONFIG", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = str(environment.get("XDG_CONFIG_HOME", "") or "").strip()
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "devtool" / "config.toml"


@lru_cache(maxsize=16)
def _load_user_ui_config_cached(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns, size
    from ..config import load_global_config
    try:
        parsed, _ = load_global_config(Path(path_text))
    except SystemExit:
        return {}
    value = parsed.get("ui", {}) if isinstance(parsed, dict) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def load_user_ui_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = user_ui_config_path(env)
    try:
        stat = path.stat()
    except OSError:
        return {}
    return dict(_load_user_ui_config_cached(str(path), int(stat.st_mtime_ns), int(stat.st_size)))


def resolve_user_ui_preferences(*, surface: str = "machine", overrides: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None) -> UIPreferences:
    environment = os.environ if env is None else env
    return resolve_ui_preferences(load_user_ui_config(environment), surface=surface, overrides=overrides, env=environment)

def profile_catalog(ui_config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    config = dict(ui_config or {})
    validate_ui_config(config)
    profiles = list(UI_PROFILE_NAMES)
    custom = config.get("profiles", {}) if isinstance(config.get("profiles", {}), Mapping) else {}
    for name in custom:
        if str(name) not in profiles:
            profiles.append(str(name))
    result: list[dict[str, Any]] = []
    for name in profiles:
        values = _custom_profile(config, name)
        result.append({"name": name, "built_in": name in UI_PROFILE_NAMES, "values": values})
    return result
