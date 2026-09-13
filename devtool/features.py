from __future__ import annotations

"""Architecture feature flags.

The original migration plan called for ``devtool/config/features.py``. Devtool
already has a long-lived ``devtool.config`` module, so converting it into a
package would be an unrelated compatibility break. This module is the stable
feature-flag surface and is re-exported from :mod:`devtool.config`.
"""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping
import os
import tomllib

FEATURE_DEFAULTS: dict[str, bool] = {
    "new_android_pipeline": True,
    "event_bus": True,
    "structured_results": True,
    "plugin_system": True,
}

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    new_android_pipeline: bool = True
    event_bus: bool = True
    structured_results: bool = True
    plugin_system: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    def enabled(self, name: str) -> bool:
        if name not in FEATURE_DEFAULTS:
            raise KeyError(f"Unknown devtool feature flag: {name}")
        return bool(getattr(self, name))


def _coerce(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"Invalid boolean for feature {name!r}: {value!r}")


def _project_feature_values(repo_root: Path, config_path: str | Path | None = None) -> Mapping[str, Any]:
    path = Path(config_path).expanduser() if config_path is not None else repo_root / ".devtool.toml"
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve(strict=False)
    if not path.is_file():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    values = payload.get("features", {}) if isinstance(payload, dict) else {}
    return values if isinstance(values, dict) else {}


def resolve_feature_flags(
    repo_root: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> FeatureFlags:
    root = Path(repo_root or Path.cwd()).expanduser().resolve(strict=False)
    env = environ if environ is not None else os.environ
    values = dict(FEATURE_DEFAULTS)
    for name, value in _project_feature_values(root, config_path=config_path).items():
        key = str(name).strip().lower().replace("-", "_")
        if key in values:
            values[key] = _coerce(key, value)
    for name in FEATURE_DEFAULTS:
        env_name = "DEVTOOL_FEATURE_" + name.upper()
        if env_name in env:
            values[name] = _coerce(name, env[env_name])
    return FeatureFlags(**values)


__all__ = ["FEATURE_DEFAULTS", "FeatureFlags", "resolve_feature_flags"]
