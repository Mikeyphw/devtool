"""Canonical Android execution capability resolution.

AOPS02 centralizes side-effect-light host discovery.  Low-level ADB provider
probing intentionally lives in :mod:`devtool.termux_toolchains`, which is also
staged beside the standalone Android builder.  Host Devtool and staged
``build-apk`` therefore make the same ADB provider decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

from ..models import ProjectConfig
from ..termux_toolchains import (
    VALID_ADB_PROVIDERS,
    RuntimeIdentity,
    detect_runtime_identity,
    normalize_adb_provider as _normalize_adb_provider,
    resolve_adb_provider as _resolve_adb_provider,
    termux_usb_transport_probe,
)
from .sdk import discover_sdk_root_with_source, installed_cmake_versions, installed_ndk_versions

ADB_PROVIDERS = frozenset(VALID_ADB_PROVIDERS)


@dataclass(frozen=True, slots=True)
class AndroidToolResolution:
    name: str
    provider: str
    available: bool
    path: str = ""
    version: str = ""
    compatible: bool | None = None
    selection_reason: str = ""
    rejection_reason: str = ""
    provenance: str = ""
    runtime: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AndroidExecutionContext:
    runtime: RuntimeIdentity
    project_root: str
    target: str
    variant: str
    sdk_root: str
    sdk_source: str
    tools: dict[str, AndroidToolResolution]
    adb_provider_requested: str
    diagnostics: tuple[str, ...] = ()

    @property
    def adb(self) -> AndroidToolResolution:
        return self.tools["adb"]

    def tool(self, name: str) -> AndroidToolResolution:
        return self.tools.get(name, AndroidToolResolution(name, "none", False, compatible=False, rejection_reason="not resolved"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runtime": {
                "platform": self.runtime.platform,
                "environment": self.runtime.environment,
                "arch": self.runtime.arch,
                "libc": self.runtime.libc,
                "prefix": str(self.runtime.prefix or ""),
                "android_api": self.runtime.android_api,
            },
            "project_root": self.project_root,
            "target": self.target,
            "variant": self.variant,
            "sdk": {"root": self.sdk_root, "source": self.sdk_source},
            "adb_provider_requested": self.adb_provider_requested,
            "device_transport": ("termux-usb" if self.adb.provider == "termux-adb" else ("adb" if self.adb.available else "none")),
            "tools": {name: tool.as_dict() for name, tool in sorted(self.tools.items())},
            "diagnostics": list(self.diagnostics),
        }


def normalize_adb_provider(value: str | None) -> str:
    return _normalize_adb_provider(value)


def probe_termux_usb(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> dict[str, Any]:
    return termux_usb_transport_probe(env=env, runtime=runtime)


def _run(argv: Sequence[str], *, env: Mapping[str, str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = subprocess.run([str(item) for item in argv], text=True, capture_output=True, check=False, timeout=timeout, env=dict(env))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()


def _executable(path: Path | None) -> bool:
    return bool(path and path.is_file() and os.access(path, os.X_OK))


def _which(name: str, env: Mapping[str, str]) -> Path | None:
    requested_path = str(env.get("PATH", ""))
    current_path = os.environ.get("PATH", "")
    # Keep the common one-argument call shape for compatibility with existing
    # provider integrations that monkeypatch shutil.which(name).
    if requested_path == current_path:
        value = shutil.which(name)
    else:
        value = shutil.which(name, path=requested_path)
    return Path(value).resolve(strict=False) if value else None


def _highest_dir(root: Path | None) -> Path | None:
    if root is None or not root.is_dir():
        return None
    items = [path for path in root.iterdir() if path.is_dir()]
    if not items:
        return None
    def key(path: Path) -> tuple[Any, ...]:
        return tuple(int(piece) if piece.isdigit() else piece for piece in re.split(r"[._-]", path.name))
    return sorted(items, key=key)[-1]


def _simple_tool(
    name: str,
    candidates: Sequence[tuple[str, Path | None, str]],
    *,
    env: Mapping[str, str],
    args: tuple[str, ...] = ("--version",),
    pattern: str = "",
    runtime: RuntimeIdentity,
) -> AndroidToolResolution:
    rejected: list[str] = []
    for provider, candidate, provenance in candidates:
        if not _executable(candidate):
            if candidate:
                rejected.append(f"{provider}: {candidate} unavailable")
            continue
        assert candidate is not None
        rc, text = _run([str(candidate), *args], env=env)
        if rc == 0:
            version = ""
            if pattern:
                match = re.search(pattern, text, re.IGNORECASE)
                version = match.group(1) if match else ""
            if not version:
                version = next((line.strip() for line in text.splitlines() if line.strip()), "")
            return AndroidToolResolution(
                name=name, provider=provider, available=True, path=str(candidate), version=version,
                compatible=True, selection_reason=f"{provider} provider passed runtime probe",
                provenance=provenance, runtime=runtime.environment,
            )
        rejected.append(f"{provider}: {text.splitlines()[-1] if text else f'exit {rc}'}")
    return AndroidToolResolution(name, "none", False, compatible=False, rejection_reason="; ".join(rejected) or f"{name} not found", runtime=runtime.environment)


def resolve_adb(
    *,
    sdk_root: Path | None = None,
    provider: str = "auto",
    explicit_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> AndroidToolResolution:
    env_map = dict(os.environ if env is None else env)
    runtime = runtime or detect_runtime_identity(env_map)
    low = _resolve_adb_provider(
        sdk_root=sdk_root,
        provider=provider,
        explicit_path=explicit_path,
        env=env_map,
        runtime=runtime,
    )
    return AndroidToolResolution(
        name="adb", provider=low.provider, available=low.available,
        path=str(low.path or ""), version=low.version, compatible=low.compatible,
        selection_reason=low.selection_reason, rejection_reason=low.rejection_reason,
        provenance=low.provenance, runtime=runtime.environment,
        metadata=dict(low.metadata or {}),
    )



def locate_adb_path(
    *,
    sdk_root: Path | None = None,
    provider: str = "auto",
    explicit_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> Path | None:
    """Locate an ADB executable using canonical provider precedence.

    Unlike :func:`resolve_adb`, this helper does not execute the candidate. It is
    intended for command construction; health/device discovery must use the full
    resolver so ``auto`` remains capability-probed for operational decisions.
    """
    env_map = dict(os.environ if env is None else env)
    runtime = runtime or detect_runtime_identity(env_map)
    provider = normalize_adb_provider(provider)
    if provider == "none":
        return None
    explicit = Path(explicit_path).expanduser().resolve(strict=False) if explicit_path else None
    sdk_adb = Path(sdk_root) / "platform-tools" / "adb" if sdk_root else None
    prefix = runtime.prefix
    termux_adb = prefix / "bin" / "termux-adb" if prefix else None
    termux_plain = prefix / "bin" / "adb" if prefix else None
    path_adb = _which("adb", env_map)
    path_termux_adb = _which("termux-adb", env_map)

    def present(path: Path | None, *, discovered: bool = False) -> Path | None:
        if path is None:
            return None
        if discovered or _executable(path):
            return path
        return None

    fixed = {
        "path": present(explicit),
        "sdk": present(sdk_adb),
        "termux-adb": present(termux_adb) or present(path_termux_adb, discovered=path_termux_adb is not None),
        "termux": present(termux_plain) or present(path_adb, discovered=path_adb is not None),
    }
    if provider != "auto":
        return fixed[provider]
    if explicit is not None and fixed["path"] is not None:
        return fixed["path"]
    if runtime.is_termux:
        return fixed["termux-adb"] or fixed["termux"] or fixed["sdk"]
    return fixed["sdk"] or present(path_adb, discovered=path_adb is not None)

def _android_options(config: ProjectConfig | None) -> dict[str, Any]:
    if config is None:
        return {}
    value = config.runner_options.get("android", {})
    return dict(value) if isinstance(value, dict) else {}


def resolve_android_execution_context(
    config: ProjectConfig | None = None,
    *,
    env: Mapping[str, str] | None = None,
    adb_provider: str | None = None,
    adb_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> AndroidExecutionContext:
    env_map = dict(os.environ if env is None else env)
    runtime = detect_runtime_identity(env_map)
    android = _android_options(config)
    root = Path(project_root or (config.project_root if config else "") or (config.repo_root if config else Path.cwd())).expanduser().resolve(strict=False)
    explicit_sdk_text = str(android.get("sdk_root") or env_map.get("ANDROID_SDK_ROOT") or env_map.get("ANDROID_HOME") or "").strip()
    explicit_sdk = Path(explicit_sdk_text).expanduser() if explicit_sdk_text else None
    sdk_root, sdk_source = discover_sdk_root_with_source(explicit_sdk, root, env=env_map)
    sdk_source = sdk_source or ""
    build_tools = _highest_dir(sdk_root / "build-tools" if sdk_root else None)

    prefix = runtime.prefix
    java = _simple_tool("java", [("termux" if runtime.is_termux else "system", _which("java", env_map), "PATH")], env=env_map, args=("-version",), pattern=r'version\s+"([^"]+)"', runtime=runtime)
    wrapper = root / "gradlew"
    gradle = _simple_tool("gradle", [("wrapper", wrapper if _executable(wrapper) else None, "project Gradle wrapper"), ("termux" if runtime.is_termux else "system", _which("gradle", env_map), "PATH")], env=env_map, args=("--version",), pattern=r"Gradle\s+([^\s]+)", runtime=runtime)

    def build_tool(name: str, *, args: tuple[str, ...], pattern: str = "") -> AndroidToolResolution:
        sdk_candidate = build_tools / name if build_tools else None
        termux_candidate = prefix / "bin" / name if prefix else None
        candidates: list[tuple[str, Path | None, str]] = []
        if runtime.is_termux:
            candidates.extend([("termux", termux_candidate, "$PREFIX/bin"), ("sdk", sdk_candidate, str(build_tools or ""))])
        else:
            candidates.extend([("sdk", sdk_candidate, str(build_tools or "")), ("system", _which(name, env_map), "PATH")])
        return _simple_tool(name, candidates, env=env_map, args=args, pattern=pattern, runtime=runtime)

    build_tools_resolution = AndroidToolResolution(
        "build-tools", "sdk" if build_tools else "none", bool(build_tools),
        path=str(build_tools or ""), version=build_tools.name if build_tools else "",
        compatible=bool(build_tools),
        selection_reason="selected newest installed Android SDK build-tools" if build_tools else "",
        rejection_reason="no installed Android SDK build-tools discovered" if not build_tools else "",
        provenance=sdk_source, runtime=runtime.environment,
    )
    aapt2 = build_tool("aapt2", args=("version",), pattern=r"v?([0-9][^\s]*)")
    apksigner = build_tool("apksigner", args=("--version",), pattern=r"([0-9]+(?:\.[0-9A-Za-z_-]+)+)")
    zipalign = build_tool("zipalign", args=("-h",))

    requested_ndk = str(android.get("ndk_version") or "").strip()
    ndk_dir = None
    if sdk_root:
        exact = sdk_root / "ndk" / requested_ndk if requested_ndk and requested_ndk != "auto" else None
        ndk_dir = exact if exact and exact.is_dir() else _highest_dir(sdk_root / "ndk")
    ndk = AndroidToolResolution(
        "ndk", "sdk" if ndk_dir else "none", bool(ndk_dir), path=str(ndk_dir or ""),
        version=ndk_dir.name if ndk_dir else "", compatible=bool(ndk_dir),
        selection_reason="selected installed SDK NDK" if ndk_dir else "",
        rejection_reason="no installed NDK discovered" if not ndk_dir else "", provenance=sdk_source,
        runtime=runtime.environment,
        metadata={"installed_versions": installed_ndk_versions(sdk_root) if sdk_root and sdk_root.is_dir() else []},
    )
    cmake_dir = _highest_dir(sdk_root / "cmake" if sdk_root else None)
    cmake_path = cmake_dir / "bin" / "cmake" if cmake_dir else _which("cmake", env_map)
    cmake = _simple_tool("cmake", [("sdk" if cmake_dir else "system", cmake_path, str(cmake_dir or "PATH"))], env=env_map, args=("--version",), pattern=r"cmake version\s+([^\s]+)", runtime=runtime)
    if cmake_dir and sdk_root:
        cmake = AndroidToolResolution(**{**cmake.as_dict(), "metadata": {"installed_versions": installed_cmake_versions(sdk_root)}})

    adb_config = android.get("adb", {}) if isinstance(android.get("adb", {}), dict) else {}
    requested_provider = normalize_adb_provider(adb_provider or str(adb_config.get("provider") or "auto"))
    explicit_adb = adb_path or str(adb_config.get("path") or "")
    adb = resolve_adb(sdk_root=sdk_root, provider=requested_provider, explicit_path=explicit_adb or None, env=env_map, runtime=runtime)

    diagnostics: list[str] = []
    if sdk_root is None:
        diagnostics.append("Android SDK not discovered")
    if requested_provider == "termux-adb" and not runtime.is_termux:
        diagnostics.append("termux-adb provider is only meaningful in native Termux")
    tools = {item.name: item for item in (java, gradle, build_tools_resolution, aapt2, apksigner, zipalign, ndk, cmake, adb)}
    return AndroidExecutionContext(runtime, str(root), str(config.selected_target if config else ""), str(config.variant if config else ""), str(sdk_root or ""), sdk_source, tools, requested_provider, tuple(diagnostics))


def resolved_android_tool(name: str, config: ProjectConfig | None = None, *, env: Mapping[str, str] | None = None) -> str | None:
    """Resolve one Android tool without probing unrelated capabilities.

    This is the compatibility-friendly accessor used by narrow consumers such as
    APK verification.  It deliberately avoids constructing the full execution
    context (and therefore avoids probing ADB/devices just to locate apksigner).
    """
    env_map = dict(os.environ if env is None else env)
    runtime = detect_runtime_identity(env_map)
    android = _android_options(config)
    root = Path((config.project_root if config else "") or (config.repo_root if config else Path.cwd())).expanduser().resolve(strict=False)
    explicit_sdk_text = str(android.get("sdk_root") or env_map.get("ANDROID_SDK_ROOT") or env_map.get("ANDROID_HOME") or "").strip()
    explicit_sdk = Path(explicit_sdk_text).expanduser() if explicit_sdk_text else None
    sdk_root, _ = discover_sdk_root_with_source(explicit_sdk, root, env=env_map)
    if name == "adb":
        adb_config = android.get("adb", {}) if isinstance(android.get("adb", {}), dict) else {}
        resolved = resolve_adb(
            sdk_root=sdk_root,
            provider=normalize_adb_provider(str(adb_config.get("provider") or "auto")),
            explicit_path=str(adb_config.get("path") or "") or None,
            env=env_map,
            runtime=runtime,
        )
        return resolved.path if resolved.available and resolved.compatible is not False and resolved.path else None
    build_tools = _highest_dir(sdk_root / "build-tools" if sdk_root else None)
    sdk_candidate = build_tools / name if build_tools else None
    prefix_candidate = runtime.prefix / "bin" / name if runtime.prefix else None
    if runtime.is_termux and _executable(prefix_candidate):
        return str(prefix_candidate)
    if _executable(sdk_candidate):
        return str(sdk_candidate)
    path_candidate = _which(name, env_map)
    # shutil.which is itself the executable/readiness check for PATH candidates.
    return str(path_candidate) if path_candidate else None


__all__ = [
    "ADB_PROVIDERS", "AndroidExecutionContext", "AndroidToolResolution",
    "normalize_adb_provider", "probe_termux_usb", "resolve_adb", "locate_adb_path",
    "resolve_android_execution_context", "resolved_android_tool",
]
