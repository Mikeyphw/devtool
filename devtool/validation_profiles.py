from __future__ import annotations

"""Named validation workflow profiles.

Workflow profiles are the primary validation behavior contract. Resource profiles
remain a separate, explicit resource-policy layer selected only with
``--resource-profile``.  Built-ins are intentionally conservative and may be
extended/overridden from ``.devtool/validation.toml``.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any
import tomllib

from .models import ProjectConfig

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "minimal": {
        "skip_restore": True,
        "skip_test": True,
        "validation": {"workflow_profile": "minimal"},
    },
    "default": {
        "validation": {"workflow_profile": "default"},
    },
    "android-full": {
        "validation": {"workflow_profile": "android-full", "android_safe": True},
        "gradle": {"build_cache": True},
        "android": {"instrumentation": "never"},
    },
    "release": {
        "validation": {"workflow_profile": "release", "android_safe": True},
        "gradle": {"build_cache": True},
        "android": {"instrumentation": "when-device-present"},
        "artifacts": {"required": True, "external_verification": "required"},
    },
    "termux": {
        "validation": {
            "workflow_profile": "termux",
            "android_safe": True,
            "split_gradle": True,
            "parallel": True,
            "low_priority": True,
        },
        "gradle": {"parallel": True, "build_cache": True},
        "android": {"instrumentation": "never"},
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def profile_file(repo_root: Path) -> Path:
    return repo_root / ".devtool" / "validation.toml"


def load_profiles(repo_root: Path) -> dict[str, dict[str, Any]]:
    profiles = deepcopy(BUILTIN_PROFILES)
    path = profile_file(repo_root)
    if not path.is_file():
        return profiles
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Invalid validation profile TOML {path}: {exc}") from exc
    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise SystemExit(f"Invalid validation profile TOML {path}: [profiles] must be a table.")
    for name, value in raw_profiles.items():
        if not isinstance(value, dict):
            raise SystemExit(f"Invalid validation profile {name!r}: profile must be a TOML table.")
        key = str(name).strip().lower()
        profiles[key] = _merge(profiles.get(key, {}), value)
    return profiles


def available_profiles(repo_root: Path) -> tuple[str, ...]:
    return tuple(sorted(load_profiles(repo_root)))


def apply_profile(config: ProjectConfig, args: Any) -> str:
    name = str(getattr(args, "validation_workflow_profile", "default") or "default").strip().lower()
    profiles = load_profiles(config.repo_root)
    if name not in profiles:
        choices = ", ".join(sorted(profiles))
        raise SystemExit(f"Unknown validation workflow profile {name!r}. Available: {choices}")
    profile = profiles[name]
    for attr in ("skip_restore", "skip_build", "skip_test"):
        if attr in profile and not bool(getattr(args, attr, False)):
            setattr(args, attr, bool(profile[attr]))
    resource = str(profile.get("resource_profile") or "").strip()
    if resource and str(getattr(args, "validation_resource_profile", "automatic") or "automatic") == "automatic":
        args.validation_resource_profile = resource
    for section in ("validation", "gradle", "android", "artifacts"):
        incoming = profile.get(section)
        if not isinstance(incoming, dict):
            continue
        current = config.runner_options.get(section, {})
        current = dict(current) if isinstance(current, dict) else {}
        config.runner_options[section] = _merge(current, incoming)
    return name
