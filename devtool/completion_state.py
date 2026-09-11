from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .models import ProjectConfig
from .ui_core.preferences import UI_PROFILE_NAMES

COMPLETION_STATE_SCHEMA_VERSION = 1


@dataclass(slots=True)
class CompletionState:
    targets: list[str]
    groups: list[str]
    ui_profiles: list[str]
    test_profiles: list[str]
    variants: list[str]
    jobs: list[str]
    workflows: list[str]
    pytest_tests: list[str]
    schema_version: int = COMPLETION_STATE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _test_profiles(config: ProjectConfig) -> list[str]:
    names: set[str] = set()
    raw = config.runner_options.get("test_profiles") if isinstance(config.runner_options, dict) else None
    if isinstance(raw, dict):
        names.update(str(key) for key in raw)
    for target in config.targets.values():
        raw_target = target.options.get("test_profiles") if isinstance(target.options, dict) else None
        if isinstance(raw_target, dict):
            names.update(str(key) for key in raw_target)
    return sorted(name for name in names if name)


def _variants(config: ProjectConfig) -> list[str]:
    names: set[str] = set()
    for target in config.targets.values():
        names.update(str(key) for key in target.variants)
    top = config.runner_options.get("variants") if isinstance(config.runner_options, dict) else None
    if isinstance(top, dict):
        names.update(str(key) for key in top)
    if config.variant:
        names.add(config.variant)
    return sorted(name for name in names if name)


def _workflows(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for target_name, target in config.targets.items():
        for action in target.workflows:
            values.add(f"{target_name}:{action}")
            values.add(action)
    return sorted(values)


def _ui_profiles(config: ProjectConfig) -> list[str]:
    names = {"auto", *UI_PROFILE_NAMES}
    raw = config.ui_config.get("profiles") if isinstance(config.ui_config, dict) else None
    if isinstance(raw, dict):
        names.update(str(key) for key in raw)
    return sorted(name for name in names if name)


def _pytest_tests(config: ProjectConfig) -> list[str]:
    if config.kind != "python":
        return []
    try:
        from .test_selection import discover_tests
        return sorted({item.id for item in discover_tests(config) if item.framework == "pytest"})
    except Exception:
        return []


def build_completion_state(config: ProjectConfig, *, include_pytest: bool = False) -> CompletionState:
    return CompletionState(
        targets=sorted(config.targets),
        groups=sorted(config.workspace.groups),
        ui_profiles=_ui_profiles(config),
        test_profiles=_test_profiles(config),
        variants=_variants(config),
        jobs=sorted(config.jobs),
        workflows=_workflows(config),
        pytest_tests=_pytest_tests(config) if include_pytest else [],
    )


def completion_values(config: ProjectConfig, kind: str) -> list[str]:
    normalized = str(kind or "all").strip().lower().replace("_", "-")
    state = build_completion_state(config, include_pytest=normalized in {"pytest-tests", "all"})
    mapping = state.as_dict()
    key = normalized.replace("-", "_")
    if normalized == "all":
        values: list[str] = []
        for name in ("targets", "groups", "ui_profiles", "test_profiles", "variants", "jobs", "workflows", "pytest_tests"):
            values.extend(mapping.get(name) or [])
        return list(dict.fromkeys(str(value) for value in values if value))
    value = mapping.get(key, [])
    return [str(item) for item in value] if isinstance(value, list) else []
