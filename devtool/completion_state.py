from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .models import ProjectConfig
from .ui_core.preferences import UI_PROFILE_NAMES

COMPLETION_STATE_SCHEMA_VERSION = 2


@dataclass(slots=True)
class CompletionState:
    targets: list[str]
    groups: list[str]
    ui_profiles: list[str]
    test_profiles: list[str]
    variants: list[str]
    jobs: list[str]
    target_jobs: list[str]
    target_job_keys: list[str]
    target_job_refs: list[str]
    workflows: list[str]
    wrapper_commands: list[str]
    wrapper_aliases: list[str]
    wrapper_options: list[str]
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


def _target_jobs(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for target_name, target in config.targets.items():
        for local_name in target.jobs:
            values.add(f"{target_name}:{local_name}")
    return sorted(values)


def _target_job_keys(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for target_name, target in config.targets.items():
        for local_name, job in target.jobs.items():
            values.add(str(job.metadata.get("job_key") or f"{target_name}.{local_name}"))
    return sorted(values)


def _target_job_refs(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for target_name, target in config.targets.items():
        for local_name, job in target.jobs.items():
            key = str(job.metadata.get("job_key") or f"{target_name}.{local_name}")
            values.add(f"{target_name}:job:{local_name}")
            values.add(f"{target_name}:job:{key}")
            # These forms are valid only from inside the owning target workflow,
            # but exposing them helps editors and docs keep the scoped syntax
            # visible without implying cross-target ownership transfer.
            values.add(f"job:{local_name}")
            values.add(f"job:{key}")
    return sorted(values)


def _workflows(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for target_name, target in config.targets.items():
        for action in target.workflows:
            values.add(f"{target_name}:{action}")
            values.add(action)
    return sorted(values)



def _wrapper_commands(config: ProjectConfig) -> list[str]:
    return sorted(config.wrapper.commands)


def _wrapper_aliases(config: ProjectConfig) -> list[str]:
    values: set[str] = set()
    for command in config.wrapper.commands.values():
        values.update(command.aliases)
    return sorted(value for value in values if value)


def _wrapper_options(config: ProjectConfig) -> list[str]:
    values: set[str] = {"--help", "--version"}
    for command in config.wrapper.commands.values():
        for option in command.options.values():
            values.add(f"{command.name}:--{option.name}")
            values.add(f"--{option.name}")
            for alias in option.aliases:
                token = f"-{alias}" if len(alias) == 1 else f"--{alias}"
                values.add(f"{command.name}:{token}")
                values.add(token)
        if command.allow_passthrough:
            values.add(f"{command.name}:--")
    return sorted(value for value in values if value)

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
        target_jobs=_target_jobs(config),
        target_job_keys=_target_job_keys(config),
        target_job_refs=_target_job_refs(config),
        workflows=_workflows(config),
        wrapper_commands=_wrapper_commands(config),
        wrapper_aliases=_wrapper_aliases(config),
        wrapper_options=_wrapper_options(config),
        pytest_tests=_pytest_tests(config) if include_pytest else [],
    )


def completion_values(config: ProjectConfig, kind: str) -> list[str]:
    normalized = str(kind or "all").strip().lower().replace("_", "-")
    state = build_completion_state(config, include_pytest=normalized in {"pytest-tests", "all"})
    mapping = state.as_dict()
    key = normalized.replace("-", "_")
    if normalized == "all":
        values: list[str] = []
        for name in ("targets", "groups", "ui_profiles", "test_profiles", "variants", "jobs", "target_jobs", "target_job_keys", "target_job_refs", "workflows", "wrapper_commands", "wrapper_aliases", "wrapper_options", "pytest_tests"):
            values.extend(mapping.get(name) or [])
        return list(dict.fromkeys(str(value) for value in values if value))
    value = mapping.get(key, [])
    return [str(item) for item in value] if isinstance(value, list) else []
