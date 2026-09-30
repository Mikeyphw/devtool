from __future__ import annotations

"""Canonical Android environment request, qualification, and resolution.

ANDROID-X01 introduces the semantic Android environment authority without
replacing Host/ENV, Gradle, EXEC, RUN, or signing authorities.  The records in
this module deliberately carry references to those authorities while owning
only Android-specific SDK/package/helper/native and variant-lane truth.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig
from ..termux_toolchains import RuntimeIdentity, detect_runtime_identity
from ._standalone import (
    build_tools_is_ready,
    cmake_is_ready,
    discover_sdk_root_with_source,
    inspect_project,
    installed_cmake_versions,
    installed_ndk_versions,
    installed_ready_build_tools_versions,
    ndk_is_ready,
    resolve_build_tools_requirement,
    resolve_cmake_requirement,
    resolve_ndk_requirement,
)
from .variants import AndroidVariantLane, resolve_android_variant_lane


ANDROID_ENVIRONMENT_SCHEMA_VERSION = 1
ANDROID_HELPERS = ("aapt2", "aidl", "zipalign", "split-select")
_DEVICE_ACTIONS = {"device", "devices", "instrumentation", "install", "release-device", "smoke"}


def _stable(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _android_options(config: ProjectConfig) -> dict[str, Any]:
    raw = config.runner_options.get("android", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def _path_identity(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": "", "exists": False, "realpath": ""}
    resolved = path.expanduser().resolve(strict=False)
    return {"path": str(path), "exists": path.exists(), "realpath": str(resolved)}


@dataclass(frozen=True, slots=True)
class EnvironmentView:
    """Explicit environment snapshot consumed by Android resolution.

    Once created, Android resolution never consults process-global SDK/PATH
    variables behind the caller's back.  ``env=None`` intentionally snapshots
    the ambient process for compatibility; passing an explicit mapping, even an
    empty mapping, creates a hermetic view.
    """

    values: dict[str, str]
    source: str
    identity: str

    @classmethod
    def from_config(
        cls,
        config: ProjectConfig,
        *,
        env: Mapping[str, str] | None = None,
    ) -> "EnvironmentView":
        base = dict(os.environ if env is None else env)
        # Project execution env is part of the explicit runtime envelope and is
        # therefore applied after the caller-provided base view.
        base.update({str(k): str(v) for k, v in config.env.items()})
        normalized = {str(k): str(v) for k, v in base.items()}
        source = "ambient-snapshot+config" if env is None else "explicit+config"
        identity = _stable({"source": source, "values": sorted(normalized.items())})
        return cls(normalized, source, identity)

    def get(self, key: str, default: str = "") -> str:
        return str(self.values.get(key, default))

    def which(self, command: str) -> Path | None:
        value = shutil.which(command, path=self.get("PATH"))
        return Path(value).resolve(strict=False) if value else None

    def as_dict(self, *, include_values: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "identity": self.identity,
            "keys": sorted(self.values),
        }
        if include_values:
            payload["values"] = dict(self.values)
        return payload


@dataclass(frozen=True, slots=True)
class AndroidPackageRequirement:
    capability: str
    requested: str = ""
    source: str = ""
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AndroidEnvironmentRequest:
    request_id: str
    target: str
    action: str
    project_root: str
    primary_lane: AndroidVariantLane
    release_lane: AndroidVariantLane
    instrumentation_lane: AndroidVariantLane
    compile_sdk: int | None
    platform: AndroidPackageRequirement
    build_tools: AndroidPackageRequirement
    helpers: tuple[str, ...]
    ndk: AndroidPackageRequirement
    cmake: AndroidPackageRequirement
    device_required: bool
    environment_view_id: str
    gradle_context_id: str = ""
    gradle_plan_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_ENVIRONMENT_SCHEMA_VERSION,
            "request_id": self.request_id,
            "target": self.target,
            "action": self.action,
            "project_root": self.project_root,
            "lanes": {
                "primary": self.primary_lane.as_dict(),
                "release": self.release_lane.as_dict(),
                "instrumentation": self.instrumentation_lane.as_dict(),
            },
            "compile_sdk": self.compile_sdk,
            "platform": self.platform.as_dict(),
            "build_tools": self.build_tools.as_dict(),
            "helpers": list(self.helpers),
            "ndk": self.ndk.as_dict(),
            "cmake": self.cmake.as_dict(),
            "device_required": self.device_required,
            "environment_view_id": self.environment_view_id,
            "gradle_context_id": self.gradle_context_id,
            "gradle_plan_id": self.gradle_plan_id,
        }


@dataclass(frozen=True, slots=True)
class AndroidCapabilityQualification:
    capability: str
    requested: str
    selected: str
    ready: bool
    source: str = ""
    provider: str = ""
    path: str = ""
    rejection_reason: str = ""
    candidates: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AndroidEnvironmentQualification:
    qualification_id: str
    request_id: str
    status: str
    sdk_root: str
    sdk_source: str
    capabilities: dict[str, AndroidCapabilityQualification]
    diagnostics: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "qualified"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_ENVIRONMENT_SCHEMA_VERSION,
            "qualification_id": self.qualification_id,
            "request_id": self.request_id,
            "status": self.status,
            "sdk": {"root": self.sdk_root, "source": self.sdk_source},
            "capabilities": {k: v.as_dict() for k, v in sorted(self.capabilities.items())},
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class ResolvedAndroidEnvironment:
    environment_id: str
    request_id: str
    qualification_id: str
    target: str
    action: str
    sdk_root: str
    sdk_source: str
    compile_sdk: int | None
    platform_package: str
    build_tools_package: str
    helpers: dict[str, AndroidCapabilityQualification]
    ndk: AndroidCapabilityQualification
    cmake: AndroidCapabilityQualification
    lanes: dict[str, AndroidVariantLane]
    gradle_context_id: str
    gradle_plan_id: str
    environment_view_id: str
    component_identities: dict[str, str]
    path_provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_ENVIRONMENT_SCHEMA_VERSION,
            "environment_id": self.environment_id,
            "request_id": self.request_id,
            "qualification_id": self.qualification_id,
            "target": self.target,
            "action": self.action,
            "sdk": {"root": self.sdk_root, "source": self.sdk_source},
            "compile_sdk": self.compile_sdk,
            "platform_package": self.platform_package,
            "build_tools_package": self.build_tools_package,
            "helpers": {k: v.as_dict() for k, v in sorted(self.helpers.items())},
            "ndk": self.ndk.as_dict(),
            "cmake": self.cmake.as_dict(),
            "lanes": {k: v.as_dict() for k, v in sorted(self.lanes.items())},
            "gradle_context_id": self.gradle_context_id,
            "gradle_plan_id": self.gradle_plan_id,
            "environment_view_id": self.environment_view_id,
            "component_identities": dict(self.component_identities),
            "path_provenance": dict(self.path_provenance),
        }


def _gradle_refs(gradle_metadata: Mapping[str, Any] | None) -> tuple[str, str]:
    metadata = dict(gradle_metadata or {})
    plan = metadata.get("gradle_execution_plan")
    if isinstance(plan, Mapping):
        context = plan.get("context")
        context_id = str((context or {}).get("context_digest") if isinstance(context, Mapping) else "")
        plan_id = str(plan.get("plan_digest") or metadata.get("gradle_plan_digest") or "")
        return context_id or str(metadata.get("gradle_context_digest") or ""), plan_id
    return str(metadata.get("gradle_context_digest") or ""), str(metadata.get("gradle_plan_digest") or "")


def build_android_environment_request(
    config: ProjectConfig,
    *,
    action: str,
    env_view: EnvironmentView,
    gradle_metadata: Mapping[str, Any] | None = None,
    device_required: bool | None = None,
) -> AndroidEnvironmentRequest:
    root = Path(config.project_root or config.repo_root).expanduser().resolve(strict=False)
    project = inspect_project(root)
    android = _android_options(config)
    build_tools_raw = android.get("build_tools", {})
    build_tools_options = dict(build_tools_raw) if isinstance(build_tools_raw, Mapping) else {}
    build_tools_override = str(build_tools_options.get("version") or (build_tools_raw if isinstance(build_tools_raw, str) else "auto") or "auto")
    ndk_override = str(android.get("ndk_version") or "auto")
    cmake_override = str(android.get("cmake_version") or "auto")

    # Requirement resolution needs an SDK root only to inspect installed choices
    # in prefer-installed mode. X01 does not use that mode for semantic request
    # construction, so a non-existing sentinel is sufficient and cannot mutate.
    sentinel = root / ".devtool" / "nonexistent-sdk-sentinel"
    build_tools, build_tools_source = resolve_build_tools_requirement(project, sentinel, build_tools_override, prefer_installed=False)
    ndk, ndk_source = resolve_ndk_requirement(project, sentinel, ndk_override, prefer_installed=False)
    cmake, cmake_source = resolve_cmake_requirement(project, sentinel, cmake_override, prefer_installed=False)
    compile_sdk = project.compile_sdk
    explicit_platform = str(android.get("platform") or android.get("android_platform") or "").strip()
    if explicit_platform and explicit_platform != "auto":
        normalized = explicit_platform.removeprefix("android-")
        try:
            compile_sdk = int(normalized)
        except ValueError:
            pass
    platform_name = f"android-{compile_sdk}" if compile_sdk else ""
    context_id, plan_id = _gradle_refs(gradle_metadata)
    action_name = str(action or "build").strip().lower()
    required_device = bool(device_required if device_required is not None else action_name in _DEVICE_ACTIONS)
    lanes = {
        "primary": resolve_android_variant_lane(config, "primary"),
        "release": resolve_android_variant_lane(config, "release"),
        "instrumentation": resolve_android_variant_lane(config, "instrumentation"),
    }
    base_payload = {
        "target": config.selected_target,
        "action": action_name,
        "project_root": str(root),
        "lanes": {k: v.as_dict() for k, v in lanes.items()},
        "compile_sdk": compile_sdk,
        "platform": platform_name,
        "build_tools": [build_tools or "", build_tools_source],
        "helpers": ANDROID_HELPERS,
        "ndk": [ndk or "", ndk_source],
        "cmake": [cmake or "", cmake_source],
        "device_required": required_device,
        "environment_view_id": env_view.identity,
        "gradle_context_id": context_id,
        "gradle_plan_id": plan_id,
    }
    request_id = _stable(base_payload)
    return AndroidEnvironmentRequest(
        request_id=request_id,
        target=config.selected_target,
        action=action_name,
        project_root=str(root),
        primary_lane=lanes["primary"],
        release_lane=lanes["release"],
        instrumentation_lane=lanes["instrumentation"],
        compile_sdk=compile_sdk,
        platform=AndroidPackageRequirement("platform", platform_name, "project/override", bool(platform_name)),
        build_tools=AndroidPackageRequirement("build-tools", build_tools or "", build_tools_source, True),
        helpers=ANDROID_HELPERS,
        ndk=AndroidPackageRequirement("ndk", ndk or "", ndk_source, bool(project.native_build or ndk_override not in {"", "auto", "none"})),
        cmake=AndroidPackageRequirement("cmake", cmake or "", cmake_source, bool(project.cmake_version or cmake_override not in {"", "auto", "none"})),
        device_required=required_device,
        environment_view_id=env_view.identity,
        gradle_context_id=context_id,
        gradle_plan_id=plan_id,
    )


def _sdk_root_for_request(config: ProjectConfig, request: AndroidEnvironmentRequest, view: EnvironmentView) -> tuple[Path | None, str]:
    android = _android_options(config)
    explicit_text = str(android.get("sdk_root") or view.get("ANDROID_SDK_ROOT") or view.get("ANDROID_HOME") or "").strip()
    explicit = Path(explicit_text).expanduser() if explicit_text else None
    # The patched standalone helper accepts an explicit environment mapping so
    # it cannot consult ambient ANDROID_* state behind this EnvironmentView.
    root, source = discover_sdk_root_with_source(explicit, Path(request.project_root), env=view.values)
    return root, str(source or "")


def _helper_qualification(
    name: str,
    *,
    sdk_root: Path,
    build_tools_version: str,
    view: EnvironmentView,
    runtime: RuntimeIdentity,
) -> AndroidCapabilityQualification:
    sdk_candidate = sdk_root / "build-tools" / build_tools_version / name
    prefix_candidate = runtime.prefix / "bin" / name if runtime.prefix else None
    candidates: list[tuple[str, Path | None]] = []
    if runtime.is_termux:
        candidates.extend((("termux", prefix_candidate), ("sdk", sdk_candidate)))
    else:
        candidates.extend((("sdk", sdk_candidate), ("path", view.which(name))))
    rendered: list[str] = []
    for provider, candidate in candidates:
        if candidate is None:
            continue
        rendered.append(f"{provider}:{candidate}")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return AndroidCapabilityQualification(
                name, build_tools_version, name, True,
                source="build-tools-helper", provider=provider, path=str(candidate.resolve(strict=False)),
                candidates=tuple(rendered), metadata={"runtime": runtime.environment},
            )
    return AndroidCapabilityQualification(
        name, build_tools_version, "", False,
        source="build-tools-helper", rejection_reason=f"required helper {name} is unavailable for Build Tools {build_tools_version}",
        candidates=tuple(rendered), metadata={"runtime": runtime.environment},
    )


def qualify_android_environment(
    config: ProjectConfig,
    request: AndroidEnvironmentRequest,
    *,
    env_view: EnvironmentView,
) -> AndroidEnvironmentQualification:
    sdk_root, sdk_source = _sdk_root_for_request(config, request, env_view)
    diagnostics: list[str] = []
    capabilities: dict[str, AndroidCapabilityQualification] = {}
    runtime = detect_runtime_identity(env_view.values)

    if sdk_root is None or not sdk_root.is_dir():
        diagnostics.append("Android SDK not discovered in the explicit EnvironmentView")
        sdk_root = sdk_root or Path("")
    else:
        platform = request.platform.requested
        if platform:
            platform_dir = sdk_root / "platforms" / platform
            ready = (platform_dir / "android.jar").is_file()
            capabilities["platform"] = AndroidCapabilityQualification(
                "platform", platform, platform if ready else "", ready,
                source=request.platform.source, provider="sdk", path=str(platform_dir),
                rejection_reason="" if ready else f"required SDK platform {platform} is incomplete or missing",
                candidates=(platform,),
            )
            if not ready:
                diagnostics.append(capabilities["platform"].rejection_reason)

        ready_build_tools = installed_ready_build_tools_versions(sdk_root)
        requested_bt = request.build_tools.requested
        selected_bt = requested_bt if requested_bt and build_tools_is_ready(sdk_root, requested_bt) else ""
        bt_ready = bool(selected_bt)
        capabilities["build-tools"] = AndroidCapabilityQualification(
            "build-tools", requested_bt, selected_bt, bt_ready,
            source=request.build_tools.source, provider="sdk",
            path=str(sdk_root / "build-tools" / requested_bt) if requested_bt else "",
            rejection_reason="" if bt_ready else f"required Build Tools {requested_bt or '<unresolved>'} are not package-ready",
            candidates=tuple(ready_build_tools),
        )
        if not bt_ready:
            diagnostics.append(capabilities["build-tools"].rejection_reason)
        else:
            for helper in request.helpers:
                qualified = _helper_qualification(helper, sdk_root=sdk_root, build_tools_version=selected_bt, view=env_view, runtime=runtime)
                capabilities[helper] = qualified
                if not qualified.ready:
                    diagnostics.append(qualified.rejection_reason)

        requested_ndk = request.ndk.requested
        ndk_candidates = installed_ndk_versions(sdk_root)
        ndk_ready = bool(requested_ndk and ndk_is_ready(sdk_root, requested_ndk))
        if request.ndk.required or requested_ndk:
            capabilities["ndk"] = AndroidCapabilityQualification(
                "ndk", requested_ndk, requested_ndk if ndk_ready else "", ndk_ready,
                source=request.ndk.source, provider="sdk", path=str(sdk_root / "ndk" / requested_ndk) if requested_ndk else "",
                rejection_reason="" if ndk_ready or not request.ndk.required else f"required NDK {requested_ndk or '<unresolved>'} is not ready",
                candidates=tuple(ndk_candidates), metadata={"host_mode": "sdk-prebuilt" if ndk_ready else ""},
            )
            if request.ndk.required and not ndk_ready:
                diagnostics.append(capabilities["ndk"].rejection_reason)
        else:
            capabilities["ndk"] = AndroidCapabilityQualification("ndk", "", "", True, source="not-required", provider="none", candidates=tuple(ndk_candidates))

        requested_cmake = request.cmake.requested
        cmake_candidates = installed_cmake_versions(sdk_root)
        cmake_ready = bool(requested_cmake and cmake_is_ready(sdk_root, requested_cmake))
        if request.cmake.required or requested_cmake:
            capabilities["cmake"] = AndroidCapabilityQualification(
                "cmake", requested_cmake, requested_cmake if cmake_ready else "", cmake_ready,
                source=request.cmake.source, provider="sdk", path=str(sdk_root / "cmake" / requested_cmake) if requested_cmake else "",
                rejection_reason="" if cmake_ready or not request.cmake.required else f"required CMake {requested_cmake or '<unresolved>'} (including Ninja) is not ready",
                candidates=tuple(cmake_candidates), metadata={"ninja_required": True},
            )
            if request.cmake.required and not cmake_ready:
                diagnostics.append(capabilities["cmake"].rejection_reason)
        else:
            capabilities["cmake"] = AndroidCapabilityQualification("cmake", "", "", True, source="not-required", provider="none", candidates=tuple(cmake_candidates))

    required_names = ["platform", "build-tools", *request.helpers]
    if request.ndk.required:
        required_names.append("ndk")
    if request.cmake.required:
        required_names.append("cmake")
    missing = [name for name in required_names if name not in capabilities or not capabilities[name].ready]
    status = "qualified" if not missing and sdk_root and sdk_root.is_dir() else "blocked"
    payload = {
        "request_id": request.request_id,
        "status": status,
        "sdk_root": str(sdk_root),
        "sdk_source": sdk_source,
        "capabilities": {k: v.as_dict() for k, v in sorted(capabilities.items())},
        "diagnostics": diagnostics,
    }
    return AndroidEnvironmentQualification(
        qualification_id=_stable(payload), request_id=request.request_id, status=status,
        sdk_root=str(sdk_root) if sdk_root else "", sdk_source=sdk_source,
        capabilities=capabilities, diagnostics=tuple(diagnostics),
    )


def resolve_android_environment(
    config: ProjectConfig,
    *,
    action: str = "build",
    env: Mapping[str, str] | None = None,
    gradle_metadata: Mapping[str, Any] | None = None,
    device_required: bool | None = None,
    fail_closed: bool = True,
) -> tuple[AndroidEnvironmentRequest, AndroidEnvironmentQualification, ResolvedAndroidEnvironment | None]:
    view = EnvironmentView.from_config(config, env=env)
    request = build_android_environment_request(
        config, action=action, env_view=view, gradle_metadata=gradle_metadata, device_required=device_required,
    )
    qualification = qualify_android_environment(config, request, env_view=view)
    if not qualification.ok:
        if fail_closed:
            detail = "; ".join(qualification.diagnostics) or "Android environment qualification failed"
            raise RuntimeError(detail)
        return request, qualification, None

    caps = qualification.capabilities
    helper_caps = {name: caps[name] for name in request.helpers}
    ndk = caps.get("ndk") or AndroidCapabilityQualification("ndk", "", "", True, provider="none")
    cmake = caps.get("cmake") or AndroidCapabilityQualification("cmake", "", "", True, provider="none")
    def capability_semantic(cap: AndroidCapabilityQualification) -> dict[str, Any]:
        # Paths and candidate lists are provenance, not semantic identity. This
        # keeps equivalent Android environments stable across relocations while
        # retaining provider/runtime/host-mode truth in the component hashes.
        return {
            "capability": cap.capability,
            "requested": cap.requested,
            "selected": cap.selected,
            "ready": cap.ready,
            "source": cap.source,
            "provider": cap.provider,
            "runtime": cap.metadata.get("runtime", ""),
            "host_mode": cap.metadata.get("host_mode", ""),
            "ninja_required": bool(cap.metadata.get("ninja_required", False)),
        }

    component_identities = {
        "android.platform": _stable({"compile_sdk": request.compile_sdk, "package": request.platform.requested}),
        "android.build-tools": _stable({"package": caps["build-tools"].selected, "helpers": {k: capability_semantic(v) for k, v in helper_caps.items()}}),
        "android.native": _stable({"ndk": capability_semantic(ndk), "cmake": capability_semantic(cmake)}),
        "android.gradle": _stable({"context": request.gradle_context_id, "plan": request.gradle_plan_id}),
        "android.lanes": _stable({"primary": request.primary_lane.identity, "release": request.release_lane.identity, "instrumentation": request.instrumentation_lane.identity}),
    }
    semantic = {
        "request_id": request.request_id,
        "qualification_id": qualification.qualification_id,
        "target": request.target,
        "action": request.action,
        "compile_sdk": request.compile_sdk,
        "platform": request.platform.requested,
        "build_tools": caps["build-tools"].selected,
        "helpers": {k: {"provider": v.provider, "selected": v.selected} for k, v in helper_caps.items()},
        "ndk": {"selected": ndk.selected, "provider": ndk.provider, "host_mode": ndk.metadata.get("host_mode", "")},
        "cmake": {"selected": cmake.selected, "provider": cmake.provider},
        "lanes": {"primary": request.primary_lane.identity, "release": request.release_lane.identity, "instrumentation": request.instrumentation_lane.identity},
        "gradle_context_id": request.gradle_context_id,
        "gradle_plan_id": request.gradle_plan_id,
        # The environment-view fingerprint is provenance. It may contain PATH
        # differences and therefore must not fragment semantic environment IDs.
        "components": component_identities,
    }
    resolved = ResolvedAndroidEnvironment(
        environment_id=_stable(semantic), request_id=request.request_id, qualification_id=qualification.qualification_id,
        target=request.target, action=request.action,
        sdk_root=qualification.sdk_root, sdk_source=qualification.sdk_source,
        compile_sdk=request.compile_sdk, platform_package=request.platform.requested,
        build_tools_package=caps["build-tools"].selected, helpers=helper_caps, ndk=ndk, cmake=cmake,
        lanes={"primary": request.primary_lane, "release": request.release_lane, "instrumentation": request.instrumentation_lane},
        gradle_context_id=request.gradle_context_id, gradle_plan_id=request.gradle_plan_id,
        environment_view_id=request.environment_view_id, component_identities=component_identities,
        path_provenance={
            "sdk": _path_identity(Path(qualification.sdk_root) if qualification.sdk_root else None),
            "helpers": {k: _path_identity(Path(v.path) if v.path else None) for k, v in helper_caps.items()},
            "ndk": _path_identity(Path(ndk.path) if ndk.path else None),
            "cmake": _path_identity(Path(cmake.path) if cmake.path else None),
        },
    )
    return request, qualification, resolved


__all__ = [
    "ANDROID_ENVIRONMENT_SCHEMA_VERSION", "ANDROID_HELPERS",
    "EnvironmentView", "AndroidPackageRequirement", "AndroidEnvironmentRequest",
    "AndroidCapabilityQualification", "AndroidEnvironmentQualification", "ResolvedAndroidEnvironment",
    "build_android_environment_request", "qualify_android_environment", "resolve_android_environment",
]
