"""Canonical Android variant profiles and semantic Gradle task resolution."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig

SEMANTIC_TASKS = {
    "assemble": "assemble{variant}",
    "build": "assemble{variant}",
    "unittest": "test{variant}UnitTest",
    "unit-test": "test{variant}UnitTest",
    "lint": "lint{variant}",
    "bundle": "bundle{variant}",
    "connectedandroidtest": "connected{variant}AndroidTest",
    "instrumentation": "connected{variant}AndroidTest",
}


@dataclass(frozen=True, slots=True)
class AndroidVariantLane:
    lane: str
    name: str
    module: str
    build_type: str
    flavor: str = ""
    source: str = ""
    identity: str = ""

    @property
    def title(self) -> str:
        return self.name[:1].upper() + self.name[1:]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lane_identity(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _configured_task_values(config: ProjectConfig, key: str) -> list[str]:
    raw = config.runner_options.get("tasks", {})
    tasks = dict(raw) if isinstance(raw, Mapping) else {}
    value = tasks.get(key, [])
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _infer_lane_from_tasks(tasks: Sequence[str]) -> tuple[str, str]:
    for raw in tasks:
        text = str(raw).strip()
        leaf = text.rsplit(":", 1)[-1]
        match = re.fullmatch(r"(?:connected|assemble)(.+)AndroidTest", leaf)
        if match:
            title = match.group(1)
            parts = [part for part in text.split(":") if part]
            return title[:1].lower() + title[1:], (parts[-2] if len(parts) >= 2 else "")
        match = re.fullmatch(r"(?:assemble|bundle|lint|test)([A-Z][A-Za-z0-9]*?)(?:UnitTest)?", leaf)
        if match:
            title = match.group(1)
            parts = [part for part in text.split(":") if part]
            return title[:1].lower() + title[1:], (parts[-2] if len(parts) >= 2 else "")
    return "", ""


def resolve_android_variant_lane(config: ProjectConfig, lane: str) -> AndroidVariantLane:
    """Resolve one canonical Android module/variant lane across all frontends."""
    lane_name = str(lane or "primary").strip().lower()
    if lane_name not in {"primary", "release", "instrumentation"}:
        raise ValueError(f"Unknown Android variant lane: {lane}")
    android_raw = config.runner_options.get("android", {})
    android = dict(android_raw) if isinstance(android_raw, Mapping) else {}
    source = "active-variant"
    module_hint = ""
    if lane_name == "primary":
        variant = str(config.variant or android.get("default_variant") or android.get("variant") or "debug").strip() or "debug"
    elif lane_name == "release":
        release_raw = android.get("release", {})
        release = dict(release_raw) if isinstance(release_raw, Mapping) else {}
        module_hint = str(release.get("module") or "").strip().strip(":")
        variant = str(release.get("variant") or "release").strip() or "release"
        source = "android.release.variant" if release.get("variant") else "release-default"
        task_variant, task_module = _infer_lane_from_tasks(_configured_task_values(config, "release_build"))
        if task_variant and not release.get("variant"):
            variant, module_hint, source = task_variant, task_module, "tasks.release_build"
    else:
        ins_raw = android.get("instrumentation", {})
        ins = dict(ins_raw) if isinstance(ins_raw, Mapping) else {}
        module_hint = str(ins.get("module") or "").strip().strip(":")
        variant = str(ins.get("variant") or "").strip()
        source = "android.instrumentation.variant" if variant else ""
        task_variant, task_module = _infer_lane_from_tasks(_configured_task_values(config, "instrumentation"))
        if not variant and task_variant:
            variant, module_hint, source = task_variant, task_module, "tasks.instrumentation"
        if not variant:
            variant = str(config.variant or android.get("default_variant") or android.get("variant") or "debug").strip() or "debug"
            source = "active-variant-fallback"
    profile = resolve_android_variant(config, variant)
    module = module_hint or profile.module
    payload = {
        "lane": lane_name, "name": profile.name, "module": module,
        "build_type": profile.build_type, "flavor": profile.flavor, "source": source,
    }
    return AndroidVariantLane(identity=_lane_identity(payload), **payload)


def validate_variant_bound_tasks(tasks: Sequence[str], lane: AndroidVariantLane) -> tuple[str, ...]:
    """Fail closed when a variant-bound Gradle task contradicts its lane."""
    accepted: list[str] = []
    expected = lane.title.lower()
    module = lane.module.strip(":").lower()
    for raw in tasks:
        text = str(raw).strip()
        if not text:
            continue
        parts = [part for part in text.split(":") if part]
        leaf = parts[-1] if parts else text
        task_module = parts[-2].lower() if len(parts) >= 2 else ""
        # Only enforce tasks that encode an Android variant in their identity.
        match = re.search(r"(Debug|Release|[A-Z][A-Za-z0-9]*?(?:Debug|Release))(?=(?:AndroidTest|UnitTest)?$)", leaf)
        if match:
            encoded = match.group(1).lower()
            if expected not in encoded and encoded not in expected:
                raise ValueError(
                    f"Android {lane.lane} lane {lane.module}:{lane.name} conflicts with variant-bound task {text!r}"
                )
            if task_module and module and task_module != module:
                raise ValueError(
                    f"Android {lane.lane} lane module {lane.module!r} conflicts with task module {task_module!r}: {text!r}"
                )
        accepted.append(text)
    return tuple(accepted)

@dataclass(frozen=True, slots=True)
class AndroidVariantProfile:
    name: str
    build_type: str
    flavor: str = ""
    signing: str = "debug"
    signing_profile: str = ""
    deployable: bool = True
    release_gates: bool = False
    module: str = "app"

    @property
    def title(self) -> str:
        return self.name[:1].upper() + self.name[1:]

    @property
    def requires_release_signing(self) -> bool:
        return self.signing.lower() in {"release", "production", "required"}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

def _bool(value: Any, default: bool) -> bool:
    if value is None: return default
    if isinstance(value, bool): return value
    if isinstance(value, str):
        low=value.strip().lower()
        if low in {"1","true","yes","on","enabled"}: return True
        if low in {"0","false","no","off","disabled"}: return False
    return bool(value)

def resolve_android_variant(config: ProjectConfig | None, variant: str = "") -> AndroidVariantProfile:
    android: dict[str, Any] = {}
    if config is not None:
        raw=config.runner_options.get("android", {})
        android=dict(raw) if isinstance(raw, dict) else {}
    name=str(variant or (config.variant if config else "") or android.get("default_variant") or android.get("variant") or "debug").strip() or "debug"
    profiles=android.get("variants", android.get("variant_profiles", {}))
    raw_profile=profiles.get(name, {}) if isinstance(profiles, dict) else {}
    raw_profile=dict(raw_profile) if isinstance(raw_profile, dict) else {}
    # Variant-specific [targets.<name>.variants.<variant>.android] values have
    # already been merged into the android table by config loading.
    for key in ("build_type","flavor","signing","signing_profile","deployable","release_gates","module"):
        if key in android and key not in raw_profile:
            raw_profile[key]=android[key]
    build_type=str(raw_profile.get("build_type") or ("release" if name.lower().endswith("release") else "debug")).strip()
    default_signing="release" if build_type.lower()=="release" else "debug"
    signing_raw = raw_profile.get("signing")
    if isinstance(signing_raw, Mapping):
        signing = str(signing_raw.get("policy") or signing_raw.get("mode") or default_signing).strip().lower()
        signing_profile = str(signing_raw.get("profile") or raw_profile.get("signing_profile") or ("release" if signing in {"release","production","required"} else "")).strip()
    else:
        signing = str(signing_raw or default_signing).strip().lower()
        signing_profile = str(raw_profile.get("signing_profile") or ("release" if signing in {"release","production","required"} else "")).strip()
    return AndroidVariantProfile(
        name=name, build_type=build_type, flavor=str(raw_profile.get("flavor") or "").strip(),
        signing=signing, signing_profile=signing_profile,
        deployable=_bool(raw_profile.get("deployable"), True), release_gates=_bool(raw_profile.get("release_gates"), build_type.lower()=="release"),
        module=str(raw_profile.get("module") or android.get("module") or "app").strip().strip(":") or "app",
    )

def resolve_semantic_tasks(config: ProjectConfig, tasks: Sequence[str]) -> list[str]:
    profile=resolve_android_variant(config)
    result=[]
    for task in tasks:
        raw=str(task).strip(); key=raw.lower().replace("_", "-")
        template=SEMANTIC_TASKS.get(key) or SEMANTIC_TASKS.get(key.replace("-", ""))
        resolved = template.format(variant=profile.title) if template else raw
        if template and resolved and not resolved.startswith(":"):
            resolved = f":{profile.module}:{resolved}"
        result.append(resolved)
    return result

def has_explicit_signing_policy(config: ProjectConfig | None) -> bool:
    """Whether the active variant explicitly owns its signing policy.

    Explicit profile policy is authoritative. Task-name inference remains only
    as a compatibility fallback for legacy configurations without one.
    """
    if config is None:
        return False
    android_raw = config.runner_options.get("android", {})
    android = dict(android_raw) if isinstance(android_raw, dict) else {}
    name = str(config.variant or android.get("default_variant") or android.get("variant") or "debug").strip() or "debug"
    profiles = android.get("variants", android.get("variant_profiles", {}))
    raw_profile = profiles.get(name, {}) if isinstance(profiles, dict) else {}
    if isinstance(raw_profile, Mapping) and ("signing" in raw_profile or "signing_profile" in raw_profile):
        return True
    return "signing" in android or "signing_profile" in android

def variant_requires_release_signing(config: ProjectConfig | None) -> bool:
    return resolve_android_variant(config).requires_release_signing

def list_android_variant_profiles(config: ProjectConfig) -> list[AndroidVariantProfile]:
    """Return configured Android variants without mutating the active selection."""
    android_raw = config.runner_options.get("android", {})
    android = dict(android_raw) if isinstance(android_raw, dict) else {}
    names: list[str] = []
    profiles = android.get("variants", android.get("variant_profiles", {}))
    if isinstance(profiles, dict):
        names.extend(str(name) for name in profiles if str(name).strip())
    if config.selected_target and config.selected_target in config.targets:
        names.extend(str(name) for name in config.targets[config.selected_target].variants if str(name).strip())
    default_name = str(android.get("default_variant") or android.get("variant") or config.variant or "debug").strip() or "debug"
    names.append(default_name)
    # Keep the conventional release profile discoverable even when it relies on
    # inferred defaults rather than an explicit TOML table.
    names.append("release")
    seen: set[str] = set()
    result: list[AndroidVariantProfile] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(resolve_android_variant(config, name))
    return result


def activate_android_variant(config: ProjectConfig, variant: str) -> ProjectConfig:
    """Select a variant after config load and merge its target/top-level options.

    Release orchestration can choose `release` after initial configuration has
    been loaded without an explicit --variant. This helper preserves the same
    merge semantics the config loader would have applied for an explicit
    selection, so signing/instrumentation/tasks all see one execution profile.
    """
    from copy import deepcopy
    from dataclasses import replace
    from ..test_model import TestSpec

    # TestSpec is an immutable inventory record containing MappingProxyType
    # fields. Python's deepcopy() tries to pickle those proxies and fails when
    # test inventory has been loaded (as it is in actual Devtool workspaces).
    # Detach the registry container, preserve frozen TestSpec objects, and
    # deepcopy any non-TestSpec legacy entries; all other config fields still
    # receive the original deep-copy isolation guarantees.
    inventory = {
        key: value if isinstance(value, TestSpec) else deepcopy(value)
        for key, value in config.tests.items()
    }
    activated = deepcopy(replace(config, tests={}))
    activated.tests = inventory
    name = str(variant or "").strip() or "debug"
    activated.variant = name
    options = None
    if activated.selected_target and activated.selected_target in activated.targets:
        options = activated.targets[activated.selected_target].variants.get(name)
    if isinstance(options, dict):
        for key, value in options.items():
            if key == "env" and isinstance(value, dict):
                activated.env.update({str(k): str(v) for k, v in value.items()})
            elif isinstance(value, dict):
                current = activated.runner_options.get(key, {})
                merged = dict(current) if isinstance(current, dict) else {}
                def deep(dst, src):
                    for k, v in src.items():
                        if isinstance(v, dict) and isinstance(dst.get(k), dict):
                            child = dict(dst[k]); deep(child, v); dst[k] = child
                        else:
                            dst[k] = v
                deep(merged, value)
                activated.runner_options[key] = merged
    return activated


__all__ = [
    "AndroidVariantProfile", "AndroidVariantLane", "SEMANTIC_TASKS",
    "resolve_android_variant", "resolve_android_variant_lane", "validate_variant_bound_tasks",
    "resolve_semantic_tasks", "has_explicit_signing_policy", "variant_requires_release_signing",
    "list_android_variant_profiles", "activate_android_variant",
]
