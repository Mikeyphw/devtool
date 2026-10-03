from __future__ import annotations

"""Canonical Go environment request/resolution/snapshot model (ENV-X02)."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping, Sequence

from .environment_composition import EnvironmentCompositionPlan

_REMOTE_ENVIRONMENTS = {"ssh", "oci", "ssh-oci", "ssh-proot-oci", "chroot", "proot", "proot-distro"}
_SENSITIVE_GO_VARS = {"GOAUTH"}
_NETWORK_VARS = ("GOPROXY", "GOSUMDB", "GOPRIVATE", "GONOPROXY", "GONOSUMDB", "GOINSECURE", "GOVCS")


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_execution(value: str) -> str:
    text = str(value or "host").strip().lower().replace("_", "-")
    return "host" if text in {"", "auto", "current", "native"} else text


def _read_directives(path: Path) -> tuple[str, str]:
    go_version = ""
    toolchain = ""
    if not path.is_file():
        return go_version, toolchain
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return go_version, toolchain
    for raw in lines:
        line = raw.split("//", 1)[0].strip()
        if line.startswith("go ") and not go_version:
            go_version = line[3:].strip().split()[0] if line[3:].strip() else ""
        elif line.startswith("toolchain ") and not toolchain:
            toolchain = line[len("toolchain "):].strip().split()[0] if line[len("toolchain "):].strip() else ""
    return go_version, toolchain


def _find_parent_work(root: Path) -> Path | None:
    for base in [root.resolve(), *root.resolve().parents]:
        candidate = base / "go.work"
        if candidate.is_file():
            return candidate
        if (base / ".git").exists():
            break
    return None


def _safe_env_value(name: str, value: str) -> str:
    if name in _SENSITIVE_GO_VARS and value:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def _probe_go_env(go: str, *, root: Path, env: Mapping[str, str], timeout: float = 15.0) -> dict[str, str]:
    names = ["GOTOOLCHAIN", "GOWORK", "GOVERSION", "GOENV", "GOROOT", "GOMOD", "GOMODCACHE", "GOCACHE", *_NETWORK_VARS, "GOAUTH"]
    try:
        proc = subprocess.run(
            [go, "env", "-json", *names],
            cwd=root,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode:
        return {}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in payload.items() if v is not None}


def _go_version(go: str, *, root: Path, env: Mapping[str, str]) -> str:
    try:
        proc = subprocess.run([go, "version"], cwd=root, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


@dataclass(frozen=True, slots=True)
class GoEnvironmentRequest:
    root: str
    action: str
    execution_environment: str = "host"
    launcher: str = "go"
    toolchain_provider: str = "system"
    provider_identity: str = ""
    composition_identity: str = ""
    gotoolchain: str = ""
    workspace: str = "auto"
    target_goos: str = ""
    target_goarch: str = ""
    dependency_mode: str = "module"
    offline: bool = False
    packages: tuple[str, ...] = ()

    @property
    def destination(self) -> bool:
        return _normalize_execution(self.execution_environment) in _REMOTE_ENVIRONMENTS

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResolvedGoEnvironment:
    request: GoEnvironmentRequest
    launcher: str
    launcher_version: str
    toolchain_provider: str
    provider_identity: str
    composition_identity: str
    effective_toolchain: str
    requested_toolchain: str
    go_directive: str
    toolchain_directive: str
    workspace: str
    environment: tuple[tuple[str, str], ...]
    destination_owned: bool
    qualified: bool
    detail: str = ""

    @property
    def identity(self) -> str:
        return _digest(self.to_dict(include_identity=False))

    def to_dict(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "request": self.request.to_dict(),
            "launcher": self.launcher,
            "launcher_version": self.launcher_version,
            "toolchain_provider": self.toolchain_provider,
            "provider_identity": self.provider_identity,
            "composition_identity": self.composition_identity,
            "effective_toolchain": self.effective_toolchain,
            "requested_toolchain": self.requested_toolchain,
            "go_directive": self.go_directive,
            "toolchain_directive": self.toolchain_directive,
            "workspace": self.workspace,
            "environment": dict(self.environment),
            "destination_owned": self.destination_owned,
            "qualified": self.qualified,
            "detail": self.detail,
        }
        if include_identity:
            payload["identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class GoExecutionSnapshot:
    resolved_identity: str
    launcher: str
    version: str
    effective_toolchain: str
    workspace: str
    environment: tuple[tuple[str, str], ...]
    execution_environment: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_go_environment(
    request: GoEnvironmentRequest, *, env: Mapping[str, str] | None = None,
    composition: EnvironmentCompositionPlan | None = None,
) -> ResolvedGoEnvironment:
    environment = dict(os.environ if env is None else env)
    root = Path(request.root).expanduser().resolve()
    go_mod_version, go_mod_toolchain = _read_directives(root / "go.mod")
    work = _find_parent_work(root)
    work_version, work_toolchain = _read_directives(work) if work else ("", "")
    go_directive = work_version or go_mod_version
    toolchain_directive = work_toolchain or go_mod_toolchain
    requested_toolchain = str(request.gotoolchain or environment.get("GOTOOLCHAIN", "") or "").strip()

    execution = _normalize_execution(request.execution_environment)
    destination = execution in _REMOTE_ENVIRONMENTS
    provider = str(request.toolchain_provider or "system").strip().lower() or "system"
    provider_identity = request.provider_identity
    composition_identity = request.composition_identity
    launcher = request.launcher or "go"
    if composition is not None:
        composition_identity = composition.identity
        selected_provider = composition.toolchain_provider
        if selected_provider == "pyenv":
            # pyenv is Python-specific; a Go request routed through it is a
            # configuration error rather than a silently ignored wrapper.
            return ResolvedGoEnvironment(
                request=request, launcher=launcher, launcher_version="", toolchain_provider="pyenv",
                provider_identity=composition.pyenv.identity, composition_identity=composition_identity,
                effective_toolchain="unknown", requested_toolchain=requested_toolchain,
                go_directive=go_directive, toolchain_directive=toolchain_directive, workspace="",
                environment=(), destination_owned=destination, qualified=False,
                detail="pyenv cannot provide a Go toolchain",
            )
        # Composition-provider identity and Go-runtime-provider identity are
        # intentionally separate. A mise/asdf layer that manages unrelated
        # tools must not make system Go look provider-owned.
        provider = str(request.toolchain_provider or "system").strip().lower() or "system"
        if provider in {"mise", "asdf"}:
            expected = composition.mise.identity if provider == "mise" else composition.asdf.identity
            provider_identity = request.provider_identity or expected
            if not destination:
                provider_go = composition.resolved_tool("go")
                if not provider_go:
                    return ResolvedGoEnvironment(
                        request=request, launcher=launcher, launcher_version="", toolchain_provider=provider,
                        provider_identity=provider_identity, composition_identity=composition_identity,
                        effective_toolchain="unknown", requested_toolchain=requested_toolchain,
                        go_directive=go_directive, toolchain_directive=toolchain_directive, workspace="",
                        environment=(), destination_owned=False, qualified=False,
                        detail=f"{provider} owns Go but could not resolve the Go executable",
                    )
                launcher = provider_go
        else:
            provider = "system"
            provider_identity = ""
    if destination:
        # Host executable paths are not evidence about destination Go.
        if Path(launcher).is_absolute():
            launcher = Path(launcher).name or "go"
        probe: dict[str, str] = {}
        launcher_version = ""
        qualified = True
        detail = "effective Go toolchain probe deferred to execution destination"
    else:
        resolved = shutil.which(launcher, path=str(environment.get("PATH", ""))) if not Path(launcher).is_absolute() else launcher
        launcher = str(resolved or launcher)
        qualified = bool(resolved and Path(resolved).exists()) if Path(launcher).is_absolute() else bool(resolved)
        probe = _probe_go_env(launcher, root=root, env=environment) if qualified else {}
        launcher_version = _go_version(launcher, root=root, env=environment) if qualified else ""
        detail = "qualified locally" if qualified else "Go launcher is unavailable"

    effective_work = str(probe.get("GOWORK") or "").strip()
    if effective_work and effective_work.lower() != "off":
        effective_path = Path(effective_work).expanduser()
        if effective_path.is_file():
            effective_go, effective_tool = _read_directives(effective_path)
            if effective_go:
                go_directive = effective_go
            if effective_tool:
                toolchain_directive = effective_tool
    effective_toolchain = probe.get("GOVERSION") or toolchain_directive or requested_toolchain or go_directive or "unknown"
    if requested_toolchain in {"auto", "local", "path"} and probe.get("GOVERSION"):
        effective_toolchain = probe["GOVERSION"]

    workspace_request = str(request.workspace or "auto").strip()
    if workspace_request.lower() == "off" or str(environment.get("GOWORK", "")).strip().lower() == "off":
        workspace = "off"
    elif workspace_request not in {"", "auto"}:
        workspace = workspace_request
    elif probe.get("GOWORK"):
        workspace = probe["GOWORK"]
    elif environment.get("GOWORK"):
        workspace = str(environment["GOWORK"])
    elif work:
        workspace = str(work)
    else:
        workspace = ""

    values: dict[str, str] = {}
    for name in ["GOTOOLCHAIN", "GOWORK", "GOVERSION", "GOENV", "GOROOT", "GOMOD", "GOMODCACHE", "GOCACHE", *_NETWORK_VARS, "GOAUTH"]:
        value = probe.get(name, environment.get(name, ""))
        if value:
            values[name] = _safe_env_value(name, str(value))
    if request.offline:
        # Enforce offline mode in the Go toolchain itself.  A Devtool marker is
        # retained for receipts, but it is not the policy mechanism.
        values.update({
            "DEVTOOL_GO_OFFLINE": "1",
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "GOAUTH": "off",
        })

    return ResolvedGoEnvironment(
        request=request,
        launcher=launcher,
        launcher_version=launcher_version,
        toolchain_provider=provider,
        provider_identity=provider_identity,
        composition_identity=composition_identity,
        effective_toolchain=effective_toolchain,
        requested_toolchain=requested_toolchain,
        go_directive=go_directive,
        toolchain_directive=toolchain_directive,
        workspace=workspace,
        environment=tuple(sorted(values.items())),
        destination_owned=destination,
        qualified=qualified,
        detail=detail,
    )


def destination_go_environment(root: Path, execution_environment: str) -> dict[str, str]:
    execution = _normalize_execution(execution_environment)
    if execution not in _REMOTE_ENVIRONMENTS:
        return {}
    # Relative paths are intentional: they are resolved by the destination
    # shell under the mapped repository, never against the controller's HOME.
    base = Path(".devtool") / "cache" / "go" / execution
    return {"GOCACHE": str(base / "build"), "GOMODCACHE": str(base / "mod")}


def destination_go_output_dir(action: str, execution_environment: str) -> str:
    execution = _normalize_execution(execution_environment)
    if execution in _REMOTE_ENVIRONMENTS:
        return str(Path(".devtool") / ("package" if action == "package" else "build") / "go")
    return ""


def _local_go_package_graph(root: Path, *, env: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, set[str]]] | None:
    environment = dict(os.environ if env is None else env)
    go = shutil.which("go", path=str(environment.get("PATH", "")))
    if not go:
        return None
    try:
        proc = subprocess.run(
            [go, "list", "-deps", "-json", "./..."], cwd=root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode:
        return None
    decoder = json.JSONDecoder()
    text = proc.stdout or ""
    index = 0
    by_dir: dict[str, str] = {}
    reverse: dict[str, set[str]] = {}
    local_imports: set[str] = set()
    records: list[dict[str, object]] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            payload, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            continue
        records.append(payload)
        import_path = str(payload.get("ImportPath") or "")
        directory = str(payload.get("Dir") or "")
        if import_path and directory:
            try:
                rel = Path(directory).resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            by_dir[rel if rel != "." else ""] = import_path
            local_imports.add(import_path)
    for payload in records:
        package = str(payload.get("ImportPath") or "")
        if package not in local_imports:
            continue
        deps: list[str] = []
        for key in ("Imports", "TestImports", "XTestImports"):
            value = payload.get(key)
            if isinstance(value, list):
                deps.extend(str(item) for item in value)
        for dep in deps:
            if dep in local_imports:
                reverse.setdefault(dep, set()).add(package)
    return by_dir, reverse


def packages_for_changed_paths(root: Path, paths: Sequence[str], *, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Translate changed source paths to owning packages and reverse dependents.

    If Go graph qualification is unavailable, widen conservatively to ``./...``
    rather than constructing a semantically unrelated ``-run`` pattern.
    """
    root = root.resolve()
    normalized: list[tuple[str, Path]] = []
    for raw in paths:
        text = str(raw or "").strip().replace("\\", "/")
        if not text:
            continue
        if Path(text).name in {"go.mod", "go.sum", "go.work", "go.work.sum"}:
            return ("./...",)
        absolute = Path(text) if Path(text).is_absolute() else root / text
        try:
            absolute.resolve(strict=False).relative_to(root)
        except ValueError:
            raise ValueError(f"Go changed path is outside project root: {raw}")
        normalized.append((text, absolute))
    if not normalized:
        return ()
    graph = _local_go_package_graph(root, env=env)
    if graph is None:
        return ("./...",)
    by_dir, reverse = graph
    selected: set[str] = set()
    import_to_rel = {imp: rel for rel, imp in by_dir.items()}
    for _text, absolute in normalized:
        candidate = absolute if absolute.is_dir() else absolute.parent
        owner = ""
        owner_depth = -1
        for rel, imp in by_dir.items():
            pkg_dir = root / rel if rel else root
            try:
                candidate.resolve(strict=False).relative_to(pkg_dir.resolve(strict=False))
            except ValueError:
                continue
            depth = len(pkg_dir.parts)
            if depth > owner_depth:
                owner, owner_depth = imp, depth
        if owner:
            selected.add(owner)
    if not selected:
        return ("./...",)
    queue = list(selected)
    while queue:
        current = queue.pop()
        for dependent in reverse.get(current, set()):
            if dependent not in selected:
                selected.add(dependent)
                queue.append(dependent)
    scopes: list[str] = []
    for imp in sorted(selected):
        rel = import_to_rel.get(imp, "")
        scopes.append("." if rel in {"", "."} else f"./{rel}")
    return tuple(scopes)


def capture_go_execution_snapshot(resolved: ResolvedGoEnvironment, *, env: Mapping[str, str] | None = None) -> GoExecutionSnapshot:
    if resolved.request.destination:
        raise RuntimeError("Go execution snapshot must be captured on the execution destination")
    environment = dict(os.environ if env is None else env)
    root = Path(resolved.request.root).resolve()
    probe = _probe_go_env(resolved.launcher, root=root, env=environment)
    version = _go_version(resolved.launcher, root=root, env=environment)
    values = {
        name: _safe_env_value(name, str(probe.get(name, "")))
        for name in ["GOTOOLCHAIN", "GOWORK", "GOVERSION", "GOROOT", "GOMODCACHE", "GOCACHE", *_NETWORK_VARS, "GOAUTH"]
        if probe.get(name)
    }
    return GoExecutionSnapshot(
        resolved_identity=resolved.identity,
        launcher=resolved.launcher,
        version=version,
        effective_toolchain=probe.get("GOVERSION") or resolved.effective_toolchain,
        workspace=probe.get("GOWORK") or resolved.workspace,
        environment=tuple(sorted(values.items())),
        execution_environment=resolved.request.execution_environment,
    )
