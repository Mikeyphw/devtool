from __future__ import annotations

"""Canonical language/tool environment composition for the ENV program.

This module deliberately owns *composition*, not configuration precedence or
execution scheduling.  Configuration resolves policy; ENV turns that policy
into typed, explainable layers and a secret-safe semantic identity.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Mapping, Sequence

from .environment_qualification import assert_environment_overlay_safe
from .models import CommandSpec

_SECRET_NAME_PARTS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE", "CREDENTIAL", "API_KEY", "AUTH", "COOKIE")
_MISE_MARKERS = ("mise.toml", ".mise.toml", ".tool-versions")
_ASDF_MARKERS = (".tool-versions",)


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled", "allow", "allowed"}


def _secret_name(name: str) -> bool:
    upper = str(name or "").upper().replace("-", "_")
    return any(part in upper for part in _SECRET_NAME_PARTS)


def _safe_env_identity(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Return stable environment identity without persisting secret values."""
    items: list[tuple[str, str]] = []
    for key, value in sorted((str(k), str(v)) for k, v in values.items()):
        if _secret_name(key):
            items.append((key, "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()))
        else:
            items.append((key, value))
    return tuple(items)


@dataclass(frozen=True, slots=True)
class ProviderExecutableIdentity:
    name: str
    path: str
    size: int = 0
    mtime_ns: int = 0
    mode: int = 0
    content_sha256: str = ""

    @classmethod
    def resolve(cls, name: str, *, env: Mapping[str, str] | None = None) -> "ProviderExecutableIdentity":
        environment = os.environ if env is None else env
        path = shutil.which(name, path=str(environment.get("PATH", ""))) or ""
        if not path:
            return cls(name=name, path="")
        try:
            st = Path(path).stat()
        except OSError:
            return cls(name=name, path=path)
        try:
            content_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            content_sha256 = ""
        return cls(name=name, path=path, size=st.st_size, mtime_ns=st.st_mtime_ns, mode=stat.S_IMODE(st.st_mode), content_sha256=content_sha256)

    @property
    def digest(self) -> str:
        return _digest({"name": self.name, "path": self.path, "size": self.size, "mtime_ns": self.mtime_ns, "mode": self.mode, "content_sha256": self.content_sha256})

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "path": self.path, "size": self.size, "mtime_ns": self.mtime_ns, "mode": self.mode, "content_sha256": self.content_sha256, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class EnvironmentLayer:
    role: str
    provider: str
    active: bool
    source: str = ""
    portable: bool = True
    secret_dependent: bool = False
    identity: str = ""
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "provider": self.provider,
            "active": self.active,
            "source": self.source,
            "portable": self.portable,
            "secret_dependent": self.secret_dependent,
            "identity": self.identity,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class MiseQualification:
    selected: bool
    executable: ProviderExecutableIdentity
    version: str
    config_files: tuple[tuple[str, str], ...]
    lock_file: tuple[str, str] | None
    selected_tools: tuple[tuple[str, str], ...]
    resolved_tools: tuple[tuple[str, str], ...]
    resolved_versions: tuple[tuple[str, str], ...]
    resolved_backends: tuple[tuple[str, str], ...]
    environment_name: str
    trust_state: str = "unknown"
    trust_mode: str = "paranoid"
    auto_install: bool = False

    @property
    def ready(self) -> bool:
        return (not self.selected) or bool(self.executable.path)

    @property
    def identity(self) -> str:
        return _digest({
            "selected": self.selected,
            "executable": self.executable.to_dict(),
            "version": self.version,
            "config_files": self.config_files,
            "lock_file": self.lock_file,
            "selected_tools": self.selected_tools,
            "resolved_tools": self.resolved_tools,
            "resolved_versions": self.resolved_versions,
            "resolved_backends": self.resolved_backends,
            "environment_name": self.environment_name,
            "trust_state": self.trust_state,
            "trust_mode": self.trust_mode,
            "auto_install": self.auto_install,
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected,
            "ready": self.ready,
            "executable": self.executable.to_dict(),
            "version": self.version,
            "config_files": [{"path": p, "sha256": d} for p, d in self.config_files],
            "lock_file": ({"path": self.lock_file[0], "sha256": self.lock_file[1]} if self.lock_file else None),
            "selected_tools": dict(self.selected_tools),
            "resolved_tools": dict(self.resolved_tools),
            "resolved_versions": dict(self.resolved_versions),
            "resolved_backends": dict(self.resolved_backends),
            "environment_name": self.environment_name,
            "trust_state": self.trust_state,
            "trust_mode": self.trust_mode,
            "auto_install": self.auto_install,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class AsdfQualification:
    selected: bool
    executable: ProviderExecutableIdentity
    version: str
    tool_versions: tuple[tuple[str, str], ...]
    tool_versions_filename: str
    selected_tools: tuple[tuple[str, str], ...]
    resolved_tools: tuple[tuple[str, str], ...]
    resolved_versions: tuple[tuple[str, str], ...]
    plugins: tuple[tuple[str, str], ...]
    version_overrides: tuple[tuple[str, str], ...]
    config_file: tuple[str, str] | None
    config_error: str
    legacy_version_file: bool
    asdf_dir: str
    data_dir: str
    data_dir_identity: str

    @property
    def ready(self) -> bool:
        return (not self.selected) or bool(self.executable.path)

    @property
    def identity(self) -> str:
        return _digest({
            "selected": self.selected, "executable": self.executable.to_dict(), "version": self.version,
            "tool_versions": self.tool_versions, "tool_versions_filename": self.tool_versions_filename, "selected_tools": self.selected_tools,
            "resolved_tools": self.resolved_tools, "resolved_versions": self.resolved_versions,
            "plugins": self.plugins, "version_overrides": self.version_overrides, "config_file": self.config_file, "config_error": self.config_error,
            "legacy_version_file": self.legacy_version_file, "asdf_dir": self.asdf_dir,
            "data_dir": self.data_dir, "data_dir_identity": self.data_dir_identity,
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected, "ready": self.ready, "executable": self.executable.to_dict(),
            "version": self.version,
            "tool_versions": [{"path": p, "sha256": d} for p, d in self.tool_versions],
            "tool_versions_filename": self.tool_versions_filename,
            "selected_tools": dict(self.selected_tools), "resolved_tools": dict(self.resolved_tools),
            "resolved_versions": dict(self.resolved_versions), "plugins": dict(self.plugins), "version_overrides": dict(self.version_overrides),
            "config_file": ({"path": self.config_file[0], "sha256": self.config_file[1]} if self.config_file else None),
            "config_error": self.config_error,
            "legacy_version_file": self.legacy_version_file, "asdf_dir": self.asdf_dir,
            "data_dir": self.data_dir, "data_dir_identity": self.data_dir_identity,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class PyenvQualification:
    selected: bool
    executable: ProviderExecutableIdentity
    version: str
    version_files: tuple[tuple[str, str], ...]
    requested_versions: tuple[str, ...]
    active_versions: tuple[str, ...]
    resolved_tools: tuple[tuple[str, str], ...]
    version_source: str
    version_source_digest: str
    version_override: str
    pyenv_dir: str
    root_identity: str

    @property
    def ready(self) -> bool:
        return (not self.selected) or bool(self.executable.path)

    @property
    def identity(self) -> str:
        return _digest({
            "selected": self.selected, "executable": self.executable.to_dict(), "version": self.version,
            "version_files": self.version_files, "requested_versions": self.requested_versions,
            "active_versions": self.active_versions, "resolved_tools": self.resolved_tools,
            "version_source": self.version_source, "version_source_digest": self.version_source_digest,
            "version_override": self.version_override, "pyenv_dir": self.pyenv_dir,
            "root_identity": self.root_identity,
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected, "ready": self.ready, "executable": self.executable.to_dict(),
            "version": self.version,
            "version_files": [{"path": p, "sha256": d} for p, d in self.version_files],
            "requested_versions": list(self.requested_versions), "active_versions": list(self.active_versions),
            "resolved_tools": dict(self.resolved_tools), "version_source": self.version_source,
            "version_source_digest": self.version_source_digest, "version_override": self.version_override,
            "pyenv_dir": self.pyenv_dir, "root_identity": self.root_identity,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class DirenvQualification:
    present: bool
    requested: bool
    authorized: bool
    executable: ProviderExecutableIdentity
    version: str = ""
    status: str = "absent"
    envrc_path: str = ""
    envrc_digest: str = ""
    watch_identity: str = ""
    global_config_identity: str = ""
    changed_names: tuple[str, ...] = ()
    effective_tools: tuple[tuple[str, str], ...] = ()
    overlay_identity: str = ""
    secret_dependent: bool = False
    portable: bool = True

    @property
    def active(self) -> bool:
        return self.present and self.requested and self.authorized and bool(self.executable.path)

    def to_dict(self) -> dict[str, object]:
        return {
            "present": self.present,
            "requested": self.requested,
            "authorized": self.authorized,
            "active": self.active,
            "executable": self.executable.to_dict(),
            "version": self.version, "status": self.status,
            "envrc_path": self.envrc_path,
            "envrc_digest": self.envrc_digest,
            "watch_identity": self.watch_identity, "global_config_identity": self.global_config_identity,
            "changed_names": list(self.changed_names), "effective_tools": dict(self.effective_tools),
            "overlay_identity": self.overlay_identity,
            "secret_dependent": self.secret_dependent, "portable": self.portable,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentCompositionPlan:
    root: str
    layers: tuple[EnvironmentLayer, ...]
    mise: MiseQualification
    asdf: AsdfQualification
    pyenv: PyenvQualification
    direnv: DirenvQualification
    toolchain_provider: str
    language_provider: str
    conflicts: tuple[str, ...] = ()

    @property
    def portable(self) -> bool:
        return all(layer.portable for layer in self.layers if layer.active)

    @property
    def identity(self) -> str:
        return _digest({
            "root": self.root,
            "layers": [layer.to_dict() for layer in self.layers],
            "mise": self.mise.to_dict(), "asdf": self.asdf.to_dict(), "pyenv": self.pyenv.to_dict(),
            "direnv": self.direnv.to_dict(), "toolchain_provider": self.toolchain_provider,
            "language_provider": self.language_provider,
            "conflicts": self.conflicts,
        })

    def declared_tools(self) -> tuple[str, ...]:
        if self.mise.selected:
            return tuple(dict.fromkeys(_provider_executable_name(name) for name, _version in self.mise.selected_tools))
        if self.asdf.selected:
            return tuple(dict.fromkeys(_provider_executable_name(name) for name, _version in self.asdf.selected_tools))
        if self.pyenv.selected:
            return ("python", "python3")
        return ()

    def resolved_tool(self, name: str) -> str:
        canonical = _provider_executable_name(name)
        if self.mise.selected:
            tools = dict(self.mise.resolved_tools)
        elif self.asdf.selected:
            tools = dict(self.asdf.resolved_tools)
        elif self.pyenv.selected:
            tools = dict(self.pyenv.resolved_tools)
        else:
            return ""
        return str(tools.get(canonical) or tools.get(name) or "")

    def provider_owns_tool(self, name: str, *, destination: bool = False) -> bool:
        canonical = _provider_executable_name(name)
        declared = canonical in self.declared_tools()
        if self.pyenv.selected and canonical in {"python", "python3"}:
            declared = True
        return declared and (destination or bool(self.resolved_tool(canonical)))

    def provider_passes_through_tool(self, name: str) -> bool:
        canonical = _provider_executable_name(name)
        if self.asdf.selected:
            current = dict(self.asdf.resolved_versions)
            for plugin, requested in self.asdf.selected_tools:
                if _provider_executable_name(plugin) != canonical:
                    continue
                active = str(current.get(plugin) or requested or "").split()
                return bool(active and active[0].lower() == "system")
        if self.pyenv.selected and canonical in {"python", "python3"}:
            active = tuple(str(item).strip().lower() for item in self.pyenv.active_versions if str(item).strip())
            requested = tuple(str(item).strip().lower() for item in self.pyenv.requested_versions if str(item).strip())
            chosen = active or requested
            return bool(chosen and chosen[0] == "system")
        return False

    def require_runtime_tool(self, name: str, *, destination: bool = False) -> str:
        canonical = _provider_executable_name(name)
        declared = canonical in self.declared_tools()
        if self.pyenv.selected and canonical in {"python", "python3"}:
            declared = True
        if not declared or self.provider_passes_through_tool(canonical):
            return ""
        if self.pyenv.selected and canonical in {"python", "python3"} and not destination:
            if self.pyenv.requested_versions and not self.pyenv.active_versions:
                raise ValueError("pyenv could not prove the active Python version")
            if self.pyenv.requested_versions and self.pyenv.active_versions and self.pyenv.requested_versions != self.pyenv.active_versions:
                raise ValueError(
                    "pyenv requested versions do not match the active versions: "
                    + f"requested={self.pyenv.requested_versions!r}, active={self.pyenv.active_versions!r}"
                )
        if destination:
            return canonical
        resolved = self.resolved_tool(canonical)
        if not resolved:
            provider = self.toolchain_provider or "provider"
            raise ValueError(f"{provider} declares ownership of {canonical} but could not resolve its executable")
        return resolved

    def require_valid(self) -> None:
        if self.conflicts:
            raise ValueError("Environment provider ownership conflict: " + "; ".join(self.conflicts))
        if self.mise.selected and not self.mise.ready:
            raise ValueError("mise is selected for this execution but the mise executable is unavailable")
        if self.asdf.selected and not self.asdf.ready:
            raise ValueError("asdf is selected for this execution but the asdf executable is unavailable")
        if self.pyenv.selected and not self.pyenv.ready:
            raise ValueError("pyenv is selected for this execution but the pyenv executable is unavailable")
        selected = [name for name, active in (("mise", self.mise.selected), ("asdf", self.asdf.selected), ("pyenv", self.pyenv.selected)) if active]
        if len(selected) > 1:
            raise ValueError("multiple toolchain providers selected: " + ", ".join(selected))
        if self.direnv.requested and self.direnv.present and not self.direnv.authorized:
            raise ValueError("direnv overlay was requested but .envrc is not explicitly authorized")
        if self.direnv.requested and self.direnv.present and self.direnv.authorized and not self.direnv.executable.path:
            raise ValueError("direnv overlay is authorized/requested but the direnv executable is unavailable")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "root": self.root,
            "layers": [layer.to_dict() for layer in self.layers],
            "mise": self.mise.to_dict(), "asdf": self.asdf.to_dict(), "pyenv": self.pyenv.to_dict(),
            "direnv": self.direnv.to_dict(), "toolchain_provider": self.toolchain_provider,
            "language_provider": self.language_provider,
            "conflicts": list(self.conflicts),
            "portable": self.portable,
            "identity": self.identity,
        }


def _mise_configs(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for base in [root, *root.parents]:
        for marker in _MISE_MARKERS:
            candidate = base / marker
            if candidate.is_file():
                found.append(candidate)
        # Never let a project discover configuration above its Git root.
        if (base / ".git").exists():
            break
    return tuple(dict.fromkeys(path.resolve() for path in reversed(found)))


def _parse_mise_tools(paths: Sequence[Path]) -> tuple[tuple[str, str], ...]:
    tools: dict[str, str] = {}
    for path in paths:
        if path.name == ".tool-versions":
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                text = line.split("#", 1)[0].strip()
                if not text:
                    continue
                parts = text.split()
                if len(parts) >= 2:
                    tools[parts[0]] = " ".join(parts[1:])
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        table = data.get("tools") if isinstance(data, dict) else None
        if isinstance(table, dict):
            for key, value in table.items():
                if isinstance(value, (str, int, float)):
                    tools[str(key)] = str(value)
                elif isinstance(value, dict) and "version" in value:
                    tools[str(key)] = str(value.get("version") or "")
    return tuple(sorted(tools.items()))




def _mise_resolved_tools(
    executable: ProviderExecutableIdentity,
    tools: Sequence[tuple[str, str]],
    *,
    root: Path,
    env: Mapping[str, str],
    destination: bool,
) -> tuple[tuple[str, str], ...]:
    if destination or not executable.path:
        return ()
    probe_env = {str(k): str(v) for k, v in env.items()}
    probe_env.update({"MISE_PARANOID": "1", "MISE_AUTO_INSTALL": "0", "MISE_EXEC_AUTO_INSTALL": "0"})
    resolved: list[tuple[str, str]] = []
    for name, _version in tools:
        try:
            proc = __import__("subprocess").run(
                [executable.path, "which", name], cwd=root, env=probe_env,
                stdout=__import__("subprocess").PIPE, stderr=__import__("subprocess").STDOUT,
                text=True, check=False, timeout=10,
            )
        except (OSError, __import__("subprocess").TimeoutExpired):
            continue
        if proc.returncode == 0 and (proc.stdout or "").strip():
            candidate = (proc.stdout or "").strip().splitlines()[-1]
            if Path(candidate).is_absolute():
                resolved.append((name, candidate))
    return tuple(sorted(resolved))


def _run_readonly(argv: Sequence[str], *, root: Path, env: Mapping[str, str], timeout: float = 10.0) -> str:
    try:
        proc = subprocess.run(list(argv), cwd=root, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def _mise_version(executable: ProviderExecutableIdentity, *, root: Path, env: Mapping[str, str], destination: bool) -> str:
    if destination or not executable.path:
        return ""
    text = _run_readonly([executable.path, "version", "--json"], root=root, env=env)
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        for key in ("version", "mise_version"):
            if payload.get(key):
                return str(payload[key])
    text = _run_readonly([executable.path, "--version"], root=root, env=env) if executable.path else ""
    return text.splitlines()[0] if text.splitlines() else ""


def _mise_trust_state(executable: ProviderExecutableIdentity, configs: Sequence[Path], *, root: Path, env: Mapping[str, str], destination: bool) -> str:
    if not configs:
        return "not-required"
    if destination or not executable.path:
        return "destination-required"
    probe_env = {str(k): str(v) for k, v in env.items()}
    probe_env["MISE_PARANOID"] = "1"
    output = _run_readonly([executable.path, "trust", "--show"], root=root, env=probe_env)
    low = output.lower()
    config_names = [str(path).lower() for path in configs]
    if output and all((name in low or Path(name).name in low) for name in config_names):
        if "untrusted" in low or "not trusted" in low:
            return "untrusted"
        if "trusted" in low:
            return "trusted"
    # Read-only current-tool resolution under paranoid mode is an independent
    # proof that the active project configuration is accepted without granting trust.
    current = _run_readonly([executable.path, "ls", "--current", "--json"], root=root, env=probe_env)
    return "trusted" if current else "untrusted-or-unknown"


def _mise_current(executable: ProviderExecutableIdentity, *, root: Path, env: Mapping[str, str], destination: bool) -> tuple[tuple[tuple[str,str],...], tuple[tuple[str,str],...]]:
    if destination or not executable.path:
        return (), ()
    probe_env = {str(k): str(v) for k, v in env.items()}
    probe_env.update({"MISE_PARANOID":"1", "MISE_AUTO_INSTALL":"0", "MISE_EXEC_AUTO_INSTALL":"0"})
    text = _run_readonly([executable.path, "ls", "--current", "--json"], root=root, env=probe_env)
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return (), ()
    versions: dict[str,str] = {}
    backends: dict[str,str] = {}
    if isinstance(payload, dict):
        for tool, records in payload.items():
            seq = records if isinstance(records, list) else [records]
            for record in seq:
                if not isinstance(record, dict): continue
                active = record.get("active", True)
                if active is False: continue
                version = record.get("version") or record.get("installed_version") or record.get("requested_version")
                backend = record.get("backend") or record.get("source")
                if version and tool not in versions: versions[str(tool)] = str(version)
                if backend and tool not in backends: backends[str(tool)] = str(backend)
    return tuple(sorted(versions.items())), tuple(sorted(backends.items()))


def _asdf_tool_version_files(root: Path, filename: str = ".tool-versions") -> tuple[Path, ...]:
    found: list[Path] = []
    for base in [root, *root.parents]:
        candidate = base / filename
        if candidate.is_file(): found.append(candidate)
        if (base / ".git").exists(): break
    return tuple(dict.fromkeys(path.resolve() for path in reversed(found)))


def _parse_tool_versions(paths: Sequence[Path]) -> tuple[tuple[str,str], ...]:
    tools: dict[str,str] = {}
    for path in paths:
        try: lines = path.read_text(encoding="utf-8").splitlines()
        except OSError: continue
        for raw in lines:
            text = raw.split("#",1)[0].strip()
            if not text: continue
            parts = text.split()
            if len(parts) >= 2: tools[parts[0]] = " ".join(parts[1:])
    return tuple(sorted(tools.items()))


def _provider_executable_name(plugin: str) -> str:
    name = str(plugin or "").strip().lower()
    # asdf plugin IDs are not guaranteed to match the executable they expose.
    # ENV-X02 canonicalizes aliases needed by the Python/Go environment boundary.
    return {"golang": "go", "nodejs": "node"}.get(name, name)


def _sanitize_plugin_remote(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def qualify_asdf(root: Path, *, selected: bool, env: Mapping[str,str] | None = None, destination: bool = False) -> AsdfQualification:
    environment = os.environ if env is None else env
    root = root.resolve()
    executable = ProviderExecutableIdentity.resolve("asdf", env=environment)
    if destination and selected and not executable.path:
        executable = ProviderExecutableIdentity(name="asdf", path="asdf")
    versions_filename = str(environment.get("ASDF_TOOL_VERSIONS_FILENAME", ".tool-versions") or ".tool-versions").strip()
    if versions_filename in {"", ".", ".."} or "/" in versions_filename or "\\" in versions_filename:
        versions_filename = ".tool-versions"
        filename_error = "ASDF_TOOL_VERSIONS_FILENAME must be a filename, not a path"
    else:
        filename_error = ""
    files = _asdf_tool_version_files(root, versions_filename)
    file_selected_tools = dict(_parse_tool_versions(files))
    overrides: dict[str, str] = {}
    for key, value in environment.items():
        name = str(key)
        if not (name.startswith("ASDF_") and name.endswith("_VERSION") and str(value).strip()):
            continue
        overrides[name] = str(value).strip()
        token = name[len("ASDF_") : -len("_VERSION")].lower()
        plugin = next(
            (known for known in file_selected_tools if known.upper().replace("-", "_") == token.upper()),
            token,
        )
        file_selected_tools[plugin] = str(value).strip()
    selected_tools = tuple(sorted(file_selected_tools.items()))
    version_text = "" if destination or not executable.path else _run_readonly([executable.path, "version"], root=root, env=environment)
    version = version_text.splitlines()[0] if version_text.splitlines() else ""
    plugins: dict[str,str] = {}
    resolved: dict[str,str] = {}
    resolved_versions: dict[str,str] = {}
    if not destination and executable.path:
        out = _run_readonly([executable.path, "plugin", "list", "--urls"], root=root, env=environment)
        for line in out.splitlines():
            parts = line.split()
            if parts: plugins[parts[0]] = _sanitize_plugin_remote(parts[-1] if len(parts)>1 else "")
        for tool, requested in selected_tools:
            executable_name = _provider_executable_name(tool)
            path = _run_readonly([executable.path, "which", executable_name], root=root, env=environment)
            if path and Path(path.splitlines()[-1]).is_absolute():
                resolved[executable_name] = path.splitlines()[-1]
            cur = _run_readonly([executable.path, "current", tool], root=root, env=environment)
            if cur:
                cols = cur.splitlines()[0].split()
                if cols:
                    # `asdf current <tool>` has had both version-first and
                    # plugin+version output shapes across releases. Preserve
                    # the plugin identity separately and extract the actual
                    # version from either shape.
                    resolved_versions[tool] = cols[1] if len(cols) >= 2 and cols[0] == tool else cols[0]
    raw_config = str(environment.get("ASDF_CONFIG_FILE", "") or "").strip()
    home_text = str(environment.get("HOME", "") or "").strip()
    config_error = filename_error
    config_path: Path | None
    if raw_config:
        config_path = Path(raw_config).expanduser()
        if not config_path.is_absolute():
            config_error = (config_error + "; " if config_error else "") + "ASDF_CONFIG_FILE must be absolute"
    elif home_text:
        config_path = Path(home_text).expanduser() / ".asdfrc"
    else:
        config_path = None
    config = (str(config_path), _file_digest(config_path)) if config_path is not None else None
    legacy = False
    if config_path is not None and config_path.is_file():
        try:
            legacy = any(line.strip().lower().replace(" ","").startswith("legacy_version_file=yes") for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError: pass
    asdf_dir = str(environment.get("ASDF_DIR", "") or "").strip()
    if asdf_dir and not Path(asdf_dir).expanduser().is_absolute():
        config_error = (config_error + "; " if config_error else "") + "ASDF_DIR must be absolute"
    elif asdf_dir:
        asdf_dir = str(Path(asdf_dir).expanduser().resolve(strict=False))
    data_dir = str(environment.get("ASDF_DATA_DIR", "") or "").strip()
    if data_dir and not Path(data_dir).expanduser().is_absolute():
        config_error = (config_error + "; " if config_error else "") + "ASDF_DATA_DIR must be absolute"
    elif data_dir:
        data_dir = str(Path(data_dir).expanduser().resolve(strict=False))
    elif home_text:
        home_asdf = (Path(home_text).expanduser() / ".asdf").resolve(strict=False)
        data_dir = str(home_asdf if home_asdf.exists() or not asdf_dir else Path(asdf_dir))
    elif asdf_dir:
        data_dir = asdf_dir
    data_identity = _digest({"asdf_dir": asdf_dir, "data_dir": data_dir}) if (asdf_dir or data_dir) else ""
    return AsdfQualification(selected=selected, executable=executable, version=version, tool_versions=tuple((str(p),_file_digest(p)) for p in files), tool_versions_filename=versions_filename, selected_tools=selected_tools, resolved_tools=tuple(sorted(resolved.items())), resolved_versions=tuple(sorted(resolved_versions.items())), plugins=tuple(sorted(plugins.items())), version_overrides=tuple(sorted(overrides.items())), config_file=config, config_error=config_error, legacy_version_file=legacy, asdf_dir=asdf_dir, data_dir=data_dir, data_dir_identity=data_identity)



def _pyenv_version_files(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for base in [root.resolve(), *root.resolve().parents]:
        modern = base / ".python-version"
        legacy = base / ".pyenv-version"
        if modern.is_file():
            found.append(modern.resolve())
            break
        if legacy.is_file():
            found.append(legacy.resolve())
            break
        if (base / ".git").exists():
            break
    return tuple(found)

def _parse_python_versions(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    values: list[str] = []
    for raw in lines:
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        values.extend(part for part in text.replace(":", " ").split() if part)
    return tuple(values)


def _split_pyenv_versions(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in str(text or "").splitlines():
        values.extend(part for part in line.replace(":", " ").split() if part)
    return tuple(values)


def qualify_pyenv(
    root: Path, *, selected: bool, env: Mapping[str, str] | None = None,
    destination: bool = False, tool_names: Sequence[str] = (),
) -> PyenvQualification:
    environment = os.environ if env is None else env
    root = root.resolve()
    executable = ProviderExecutableIdentity.resolve("pyenv", env=environment)
    if destination and selected and not executable.path:
        executable = ProviderExecutableIdentity(name="pyenv", path="pyenv")
    raw_pyenv_dir = str(environment.get("PYENV_DIR", "") or "").strip()
    if raw_pyenv_dir:
        pyenv_dir_path = Path(raw_pyenv_dir).expanduser()
        if not pyenv_dir_path.is_absolute():
            pyenv_dir_path = root / pyenv_dir_path
        search_root = pyenv_dir_path.resolve(strict=False)
    else:
        search_root = root
    files = _pyenv_version_files(search_root)
    override = str(environment.get("PYENV_VERSION", "") or "").strip()
    requested = _split_pyenv_versions(override) if override else _parse_python_versions(files[0] if files else None)
    version = ""
    active: tuple[str, ...] = ()
    source = str(files[0]) if files else ("environment:PYENV_VERSION" if override else "")
    resolved: dict[str, str] = {}
    if not destination and executable.path:
        version_text = _run_readonly([executable.path, "--version"], root=root, env=environment)
        version = version_text.splitlines()[0] if version_text.splitlines() else ""
        active = _split_pyenv_versions(_run_readonly([executable.path, "version-name"], root=root, env=environment))
        detail = _run_readonly([executable.path, "version"], root=root, env=environment)
        if "set by " in detail:
            source = detail.split("set by ", 1)[1].rstrip(")").strip()
        names = ["python", "python3"]
        names.extend(str(name) for name in tool_names if str(name).strip() and str(name).strip() not in names)
        for name in dict.fromkeys(names):
            path = _run_readonly([executable.path, "which", name], root=root, env=environment)
            candidate = path.splitlines()[-1].strip() if path.splitlines() else ""
            if candidate and Path(candidate).is_absolute():
                resolved[name] = candidate
    source_digest = ""
    if source and not source.startswith("environment:"):
        try:
            source_path = Path(source).expanduser()
            if source_path.is_file():
                source_digest = _file_digest(source_path)
        except OSError:
            source_digest = ""
    pyenv_root = str(environment.get("PYENV_ROOT", "") or "")
    root_identity = _digest({"pyenv_root": pyenv_root}) if pyenv_root else ""
    return PyenvQualification(
        selected=selected, executable=executable, version=version,
        version_files=tuple((str(path), _file_digest(path)) for path in files),
        requested_versions=requested, active_versions=active, resolved_tools=tuple(sorted(resolved.items())),
        version_source=source, version_source_digest=source_digest, version_override=override,
        pyenv_dir=str(search_root) if raw_pyenv_dir else "", root_identity=root_identity,
    )

def _direnv_probe(root: Path, executable: ProviderExecutableIdentity, env: Mapping[str,str], *, requested: bool, tool_names: Sequence[str] = ()) -> tuple[bool,str,str,str,str,tuple[str,...],tuple[tuple[str,str],...],str,bool]:
    if not executable.path or not (root/".envrc").is_file():
        return False, "missing" if requested else "absent", "", "", "", (), (), "", False
    version = _run_readonly([executable.path, "version"], root=root, env=env)
    status_out = _run_readonly([executable.path, "status", "--json"], root=root, env=env)
    authorized = False
    status = "unknown"
    watch_identity = ""
    global_identity = ""
    if status_out:
        try:
            payload = json.loads(status_out)
        except json.JSONDecodeError:
            payload = {}
        state = payload.get("state", {}) if isinstance(payload, dict) else {}
        found = state.get("foundRC", {}) if isinstance(state, dict) else {}
        st = found.get("status", {}) if isinstance(found, dict) else {}
        text = str(st.get("text", "") if isinstance(st, dict) else "").upper()
        code = st.get("code") if isinstance(st, dict) else None
        authorized = text == "ALLOWED" or (not text and code == 0)
        status = "allowed" if authorized else (text.lower() if text else "blocked")
        watches = found.get("watch", found.get("watches", [])) if isinstance(found, dict) else []
        watch_identity = _digest(watches) if watches else ""
        config = payload.get("config", {}) if isinstance(payload, dict) else {}
        if config: global_identity = _digest(config)
    else:
        text = _run_readonly([executable.path, "status"], root=root, env=env)
        low = text.lower()
        if "found rc allowed true" in low or "found rc allowed 0" in low:
            authorized, status = True, "allowed"
        elif "found rc" in low:
            status = "blocked"
    changed: tuple[str,...] = ()
    effective_tools: tuple[tuple[str,str], ...] = ()
    overlay_identity = ""
    secret_dep = False
    if authorized and requested:
        # Values exist only in this local variable and are immediately reduced to
        # an opaque digest; they are never placed in receipts or logs.
        exported = _run_readonly([executable.path, "export", "json"], root=root, env=env)
        try: overlay = json.loads(exported) if exported else {}
        except json.JSONDecodeError: overlay = {}
        if isinstance(overlay, dict):
            changed = tuple(sorted(str(k) for k in overlay))
            secret_dep = any(_secret_name(k) for k in changed)
            overlay_identity = _digest([(str(k), hashlib.sha256(str(v).encode()).hexdigest()) for k,v in sorted(overlay.items())])
            overlay_path = str(overlay.get("PATH", "") or "")
            if overlay_path:
                found: dict[str,str] = {}
                for tool in tool_names:
                    path = shutil.which(str(tool), path=overlay_path)
                    if path: found[str(tool)] = path
                effective_tools = tuple(sorted(found.items()))
    return authorized, status, version, watch_identity, global_identity, changed, effective_tools, overlay_identity, secret_dep


def qualify_mise(
    root: Path, *, selected: bool, env: Mapping[str, str] | None = None, destination: bool = False,
) -> MiseQualification:
    environment = os.environ if env is None else env
    root = root.resolve()
    configs = _mise_configs(root)
    lock = root / "mise.lock"
    environment_name = str(environment.get("MISE_ENV", "") or "")
    executable = ProviderExecutableIdentity.resolve("mise", env=environment)
    if destination and selected and not executable.path:
        executable = ProviderExecutableIdentity(name="mise", path="mise")
    selected_tools = _parse_mise_tools(configs)
    resolved_tools = _mise_resolved_tools(executable, selected_tools, root=root, env=environment, destination=destination)
    resolved_versions, resolved_backends = _mise_current(executable, root=root, env=environment, destination=destination)
    return MiseQualification(
        selected=bool(selected), executable=executable, version=_mise_version(executable, root=root, env=environment, destination=destination),
        config_files=tuple((str(path), _file_digest(path)) for path in configs),
        lock_file=(str(lock), _file_digest(lock)) if lock.is_file() else None,
        selected_tools=selected_tools, resolved_tools=resolved_tools, resolved_versions=resolved_versions, resolved_backends=resolved_backends,
        environment_name=environment_name, trust_state=_mise_trust_state(executable, configs, root=root, env=environment, destination=destination),
        trust_mode="paranoid", auto_install=False,
    )


def qualify_direnv(root: Path, *, env: Mapping[str, str] | None = None, destination: bool = False, tool_names: Sequence[str] = ()) -> DirenvQualification:
    environment = os.environ if env is None else env
    root = root.resolve()
    envrc = root / ".envrc"
    present = envrc.is_file()
    mode = str(environment.get("DEVTOOL_DIRENV", "auto") or "auto").strip().lower()
    disabled = mode in {"0", "off", "false", "disabled", "disable"}
    required = mode in {"1", "on", "true", "enabled", "required"}
    executable = ProviderExecutableIdentity.resolve("direnv", env=environment)
    explicit = _truthy(environment.get("DEVTOOL_DIRENV_AUTHORIZED"))
    probe_requested = present and not disabled and bool(executable.path)
    if destination:
        requested = present and not disabled and (required or explicit)
        if requested and not executable.path:
            executable = ProviderExecutableIdentity(name="direnv", path="direnv")
        authorized = explicit
        status = "destination-required" if requested and not explicit else ("allowed" if explicit else "absent")
        version = ""; watch_identity=""; status_global_identity=""; changed=(); effective_tools=(); overlay_identity=""; secret_dep=False
    else:
        detected, status, version, watch_identity, status_global_identity, changed, effective_tools, overlay_identity, secret_dep = _direnv_probe(root, executable, environment, requested=probe_requested, tool_names=tool_names)
        authorized = explicit or detected
        # auto is optional when direnv is absent; when present+installed it becomes
        # an explicit qualification surface so blocked state is ACTION_REQUIRED.
        requested = present and not disabled and (required or bool(executable.path))
    marker_env = {name: str(environment[name]) for name in ("DIRENV_DIR","DIRENV_FILE","DIRENV_WATCHES","DIRENV_DIFF","VIRTUAL_ENV") if name in environment}
    if not overlay_identity and marker_env:
        overlay_identity = _digest(_safe_env_identity(marker_env))
    secret_dep = secret_dep or any(_secret_name(name) for name in marker_env)
    raw_global_config = str(environment.get("DIRENV_CONFIG", "") or "").strip()
    explicit_global_identity = ""
    if raw_global_config:
        global_config = Path(raw_global_config).expanduser()
        explicit_global_identity = _digest({"path": str(global_config), "direnvrc": _file_digest(global_config/"direnvrc")})
    global_identity = _digest({"status": status_global_identity, "explicit": explicit_global_identity}) if (status_global_identity or explicit_global_identity) else ""
    return DirenvQualification(
        present=present, requested=requested, authorized=authorized, executable=executable, version=version, status=status,
        envrc_path=str(envrc) if present else "", envrc_digest=_file_digest(envrc) if present else "",
        watch_identity=watch_identity, global_config_identity=global_identity, changed_names=changed, effective_tools=effective_tools, overlay_identity=overlay_identity,
        secret_dependent=secret_dep, portable=not (requested and authorized),
    )


def build_environment_composition_plan(
    root: Path,
    *,
    use_mise: bool,
    use_asdf: bool = False,
    use_pyenv: bool = False,
    language_provider: str = "",
    env: Mapping[str, str] | None = None,
    declared_overlay: Mapping[str, object] | None = None,
    destination: bool = False,
) -> EnvironmentCompositionPlan:
    environment = os.environ if env is None else env
    root = root.resolve()
    if declared_overlay:
        assert_environment_overlay_safe(declared_overlay, source="environment composition overlay")
    language = str(language_provider or "").strip().lower()
    mise = qualify_mise(root, selected=use_mise, env=environment, destination=destination)
    asdf = qualify_asdf(root, selected=use_asdf, env=environment, destination=destination)
    pyenv_tools = (language,) if language in {"poetry", "pdm", "pipenv", "hatch", "uv"} else ()
    pyenv = qualify_pyenv(root, selected=use_pyenv, env=environment, destination=destination, tool_names=pyenv_tools)
    if mise.selected:
        provider_tools = tuple(dict(mise.selected_tools))
    elif asdf.selected:
        provider_tools = tuple(dict(asdf.resolved_tools)) or tuple(_provider_executable_name(name) for name, _ in asdf.selected_tools)
    elif pyenv.selected:
        provider_tools = tuple(dict(pyenv.resolved_tools)) or ("python",)
    else:
        provider_tools = ()
    direnv = qualify_direnv(root, env=environment, destination=destination, tool_names=provider_tools)

    conflicts: list[str] = []
    selected_provider_names = [name for name, active in (("mise", mise.selected), ("asdf", asdf.selected), ("pyenv", pyenv.selected)) if active]
    if len(selected_provider_names) > 1:
        conflicts.append("multiple toolchain providers selected: " + ", ".join(selected_provider_names))
    if asdf.selected and asdf.config_error:
        conflicts.append(asdf.config_error)
    if asdf.selected and not destination:
        resolved_versions = dict(asdf.resolved_versions)
        missing_current = [tool for tool, _requested in asdf.selected_tools if tool not in resolved_versions]
        if missing_current:
            conflicts.append("asdf could not resolve current versions for: " + ", ".join(sorted(missing_current)))
    overlay_virtualenv = "VIRTUAL_ENV" in direnv.changed_names or (direnv.active and str(environment.get("VIRTUAL_ENV", "")).strip())
    if overlay_virtualenv and language in {"poetry", "pdm", "pipenv", "hatch", "venv", "uv"}:
        conflicts.append(f"direnv owns VIRTUAL_ENV while language provider {language} also owns Python environment selection")
    if direnv.active and mise.selected and "MISE_ENV" in direnv.changed_names:
        conflicts.append("direnv attempts to select MISE_ENV while mise is the toolchain provider")
    if direnv.active and asdf.selected and any(name.startswith("ASDF_") and name.endswith("_VERSION") for name in direnv.changed_names):
        conflicts.append("direnv attempts to override ASDF_*_VERSION while asdf is the toolchain provider")
    if direnv.active and pyenv.selected and "PYENV_VERSION" in direnv.changed_names:
        conflicts.append("direnv attempts to override PYENV_VERSION while pyenv is the Python runtime provider")
    provider_paths = dict(
        mise.resolved_tools if mise.selected else asdf.resolved_tools if asdf.selected else pyenv.resolved_tools if pyenv.selected else ()
    )
    provider_name = selected_provider_names[0] if len(selected_provider_names) == 1 else "provider"
    for tool, overlay_path in direnv.effective_tools:
        expected = provider_paths.get(tool, "")
        if expected and Path(overlay_path).resolve(strict=False) != Path(expected).resolve(strict=False):
            conflicts.append(f"direnv PATH selects {tool} at {overlay_path} while {provider_name} owns {tool} at {expected}")

    base_layer = EnvironmentLayer("host-base", "host", True, source=str(root), identity=_digest({"root": str(root)}))
    direnv_layer = EnvironmentLayer(
        "environment-overlay", "direnv", direnv.active, source=direnv.envrc_path,
        portable=direnv.portable, secret_dependent=direnv.secret_dependent,
        identity=direnv.overlay_identity or direnv.envrc_digest,
        details={"authorized": direnv.authorized, "requested": direnv.requested, "status": direnv.status, "changed_names": list(direnv.changed_names)},
    )
    provider = selected_provider_names[0] if len(selected_provider_names) == 1 else "none"
    if mise.selected:
        provider_identity = mise.identity
        provider_source = ",".join(path for path, _ in mise.config_files)
        provider_details = {"trust_mode": mise.trust_mode, "trust_state": mise.trust_state, "auto_install": mise.auto_install, "tools": dict(mise.selected_tools)}
    elif asdf.selected:
        provider_identity = asdf.identity
        provider_source = ",".join(path for path, _ in asdf.tool_versions)
        provider_details = {"tools": dict(asdf.selected_tools), "legacy_version_file": asdf.legacy_version_file}
    elif pyenv.selected:
        provider_identity = pyenv.identity
        provider_source = pyenv.version_source or ",".join(path for path, _ in pyenv.version_files)
        provider_details = {"requested_versions": list(pyenv.requested_versions), "active_versions": list(pyenv.active_versions)}
    else:
        provider_identity = ""
        provider_source = ""
        provider_details = {}
    toolchain_layer = EnvironmentLayer(
        "toolchain-provider", provider, provider != "none", source=provider_source, portable=True, identity=provider_identity, details=provider_details,
    )
    language_layer = EnvironmentLayer(
        "language-environment", language or "none", bool(language), source=str(root),
        portable=True, identity=_digest({"provider": language or "none", "root": str(root)}),
    )
    return EnvironmentCompositionPlan(
        root=str(root),
        layers=(base_layer, direnv_layer, toolchain_layer, language_layer),
        mise=mise, asdf=asdf, pyenv=pyenv, direnv=direnv, toolchain_provider=provider, language_provider=language,
        conflicts=tuple(conflicts),
    )


def _verify_provider_identity(expected: ProviderExecutableIdentity, *, env: Mapping[str, str], label: str) -> None:
    if not expected.path or not Path(expected.path).is_absolute():
        return
    current = ProviderExecutableIdentity.resolve(expected.name, env=env)
    if current.path != expected.path or current.digest != expected.digest:
        effective = current.path or "MISSING"
        raise ValueError(f"{label} provider changed after environment composition: planned {expected.path} ({expected.digest[:12]}), effective {effective} ({current.digest[:12] if current.path else 'missing'})")


def apply_composition_to_spec(
    spec: CommandSpec,
    plan: EnvironmentCompositionPlan,
    *,
    verification_env: Mapping[str, str] | None = None,
) -> CommandSpec:
    """Render the composition plan around one already-materialized invocation.

    ``verification_env`` is the authoritative environment used to qualify the
    provider before command materialization.  CommandSpec.env is only an
    overlay in normal Devtool execution, so using it alone would incorrectly
    drop the parent/config PATH and could report a correctly qualified provider
    as missing.
    """
    plan.require_valid()
    argv = list(spec.argv)
    env = dict(spec.env)
    effective_env = dict(os.environ if verification_env is None else verification_env)
    effective_env.update(env)
    metadata = dict(spec.metadata)
    # Language manager prefixes are expected to already be part of argv. mise
    # sits outside them, and authorized direnv is outermost.
    if plan.mise.selected:
        metadata.setdefault("progress_argv", list(spec.argv))
        if spec.cwd:
            metadata.setdefault("progress_cwd", spec.cwd)
        metadata["toolchain_manager"] = "mise"
        metadata["toolchain_wrapper"] = "mise exec"
        _verify_provider_identity(plan.mise.executable, env=effective_env, label="mise")
        env.setdefault("MISE_PARANOID", "1")
        env.setdefault("MISE_EXEC_AUTO_INSTALL", "0")
        if plan.mise.environment_name:
            env.setdefault("MISE_ENV", plan.mise.environment_name)
        if not (argv and Path(argv[0]).name == "mise"):
            argv = [plan.mise.executable.path or "mise", "exec", "--", *argv]
    if plan.asdf.selected:
        metadata.setdefault("progress_argv", list(spec.argv))
        if spec.cwd:
            metadata.setdefault("progress_cwd", spec.cwd)
        metadata["toolchain_manager"] = "asdf"
        metadata["toolchain_wrapper"] = "asdf exec"
        _verify_provider_identity(plan.asdf.executable, env=effective_env, label="asdf")
        if not (argv and Path(argv[0]).name == "asdf"):
            argv = [plan.asdf.executable.path or "asdf", "exec", *argv]
    if plan.pyenv.selected:
        metadata.setdefault("progress_argv", list(spec.argv))
        if spec.cwd:
            metadata.setdefault("progress_cwd", spec.cwd)
        metadata["toolchain_manager"] = "pyenv"
        metadata["toolchain_wrapper"] = "pyenv exec"
        _verify_provider_identity(plan.pyenv.executable, env=effective_env, label="pyenv")
        if not (argv and Path(argv[0]).name == "pyenv"):
            argv = [plan.pyenv.executable.path or "pyenv", "exec", *argv]
    if plan.direnv.active:
        _verify_provider_identity(plan.direnv.executable, env=effective_env, label="direnv")
        if not (argv and Path(argv[0]).name == "direnv"):
            argv = [plan.direnv.executable.path or "direnv", "exec", plan.root, *argv]

    metadata["environment_composition"] = plan.to_dict()
    metadata["environment_identity"] = plan.identity
    domains = dict(metadata.get("environment_domains") or {})
    domains.update({
        "composition": plan.identity,
        "mise": plan.mise.identity if plan.mise.selected else "",
        "asdf": plan.asdf.identity if plan.asdf.selected else "",
        "pyenv": plan.pyenv.identity if plan.pyenv.selected else "",
        "toolchain": (
            plan.mise.identity if plan.mise.selected else
            plan.asdf.identity if plan.asdf.selected else
            plan.pyenv.identity if plan.pyenv.selected else ""
        ),
        "direnv": plan.direnv.overlay_identity if plan.direnv.active else "",
        "language": next((layer.identity for layer in plan.layers if layer.role == "language-environment"), ""),
    })
    metadata["environment_domains"] = domains
    return CommandSpec(spec.name, argv, env=env, cwd=spec.cwd, destructive=spec.destructive, metadata=metadata)
