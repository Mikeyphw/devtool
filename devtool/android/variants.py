"""Canonical Android variant profiles and semantic Gradle task resolution."""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
    activated = deepcopy(config)
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
