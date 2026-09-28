from __future__ import annotations

"""Canonical Python environment request/resolution/snapshot model (ENV-X02)."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Mapping, Sequence

from .environment_composition import EnvironmentCompositionPlan
from .python_support import (
    PythonEnvironmentPlan,
    PythonProjectError,
    PythonProjectInfo,
    _interpreter_version,
    _is_guest_execution_environment,
    _specifier_satisfied,
    default_python_environment_root,
    environment_fingerprint,
    python_repo_key,
    resolve_environment,
)

_LOCK_POLICIES = {"require", "prefer", "ignore"}
_MANAGER_OWNED = {"poetry", "pdm", "pipenv", "hatch"}
_REMOTE_ENVIRONMENTS = {"ssh", "oci", "ssh-oci", "ssh-proot-oci", "chroot", "proot", "proot-distro"}


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_lock_policy(value: str | None, *, locked_compat: bool = True) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "prefer" if locked_compat else "ignore"
    aliases = {"true": "prefer", "false": "ignore", "locked": "require", "unlocked": "ignore"}
    text = aliases.get(text, text)
    if text not in _LOCK_POLICIES:
        raise PythonProjectError(f"Unsupported Python lock policy {value!r}; expected require, prefer, or ignore")
    return text


def _normalized_execution(value: str) -> str:
    text = str(value or "host").strip().lower().replace("_", "-")
    return "host" if text in {"", "auto", "current", "native"} else text


def destination_python_environment_root(root: Path, execution_environment: str, name: str = "python") -> str:
    execution = _normalized_execution(execution_environment)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or "python").strip(".-") or "python"
    if execution in _REMOTE_ENVIRONMENTS:
        # Repo-relative allocation survives SSH/chroot/PRoot path translation.
        return str(Path(".devtool") / "environments" / "python" / execution / safe_name)
    return str(default_python_environment_root(root, safe_name).resolve())


@dataclass(frozen=True, slots=True)
class PythonEnvironmentRequest:
    root: str
    action: str = "run"
    runtime_provider: str = "auto"
    manager: str = "auto"
    environment: str = ""
    configured_python: str = "python3"
    configured_python_explicit: bool = False
    lock_policy: str = "prefer"
    offline: bool = False
    groups: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()
    execution_environment: str = "host"
    destination_name: str = "python"

    def __post_init__(self) -> None:
        if self.lock_policy not in _LOCK_POLICIES:
            raise ValueError(f"invalid lock_policy: {self.lock_policy}")

    @property
    def destination(self) -> bool:
        return _normalized_execution(self.execution_environment) in _REMOTE_ENVIRONMENTS

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PythonManagerReceipt:
    manager: str
    executable: str
    manager_version: str
    environment_root: str
    python_executable: str
    lock_file: str
    lock_digest: str
    config_files: tuple[tuple[str, str], ...] = ()
    groups: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()
    lock_policy: str = "prefer"
    runtime_provider: str = "auto"
    install_project: bool = True
    qualified: bool = True
    detail: str = ""

    @property
    def identity(self) -> str:
        return _digest(asdict(self))

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class ResolvedPythonEnvironment:
    request: PythonEnvironmentRequest
    manager: str
    runtime_provider: str
    ownership: str
    python: str
    bootstrap_python: str
    run_prefix: tuple[str, ...]
    environment_root: str
    fingerprint: str
    fingerprint_inputs: tuple[str, ...]
    compatible: bool
    compatibility_detail: str
    manager_receipt: PythonManagerReceipt
    composition_identity: str = ""
    destination_owned: bool = False

    # Compatibility properties let the existing mature Python runner consume
    # the canonical object without duplicating command-generation code.
    @property
    def execution_environment(self) -> str:
        return self.request.execution_environment

    def to_legacy_plan(self) -> PythonEnvironmentPlan:
        return PythonEnvironmentPlan(
            self.manager,
            self.python,
            self.run_prefix,
            self.environment_root,
            self.fingerprint,
            self.fingerprint_inputs,
            self.compatible,
            self.compatibility_detail,
            self.bootstrap_python,
            self.request.execution_environment,
        )

    @property
    def identity(self) -> str:
        return _digest(self.to_dict(include_identity=False))

    def to_dict(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "request": self.request.to_dict(),
            "manager": self.manager,
            "runtime_provider": self.runtime_provider,
            "ownership": self.ownership,
            "python": self.python,
            "bootstrap_python": self.bootstrap_python,
            "run_prefix": list(self.run_prefix),
            "environment_root": self.environment_root,
            "fingerprint": self.fingerprint,
            "fingerprint_inputs": list(self.fingerprint_inputs),
            "compatible": self.compatible,
            "compatibility_detail": self.compatibility_detail,
            "manager_receipt": self.manager_receipt.to_dict(),
            "composition_identity": self.composition_identity,
            "destination_owned": self.destination_owned,
        }
        if include_identity:
            payload["identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class PythonExecutionSnapshot:
    resolved_identity: str
    executable: str
    version: str
    prefix: str
    environment_root: str
    manager: str
    execution_environment: str
    destination: bool

    @classmethod
    def capture(
        cls,
        resolved: ResolvedPythonEnvironment,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 20.0,
    ) -> "PythonExecutionSnapshot":
        if resolved.request.destination:
            raise PythonProjectError("Python execution snapshot must be captured on the execution destination")
        command = [*resolved.run_prefix, "python", "-c", "import json,sys; print(json.dumps({'executable':sys.executable,'version':sys.version.split()[0],'prefix':sys.prefix}))"]
        if resolved.manager == "venv":
            command = [resolved.python, "-c", "import json,sys; print(json.dumps({'executable':sys.executable,'version':sys.version.split()[0],'prefix':sys.prefix}))"]
        proc = subprocess.run(command, cwd=cwd, env=dict(env) if env is not None else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        if proc.returncode:
            raise PythonProjectError(f"Unable to capture Python execution snapshot: {(proc.stdout or '').strip()}")
        try:
            payload = json.loads((proc.stdout or "").splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise PythonProjectError("Python execution snapshot did not return valid JSON") from exc
        return cls(
            resolved_identity=resolved.identity,
            executable=str(payload.get("executable") or ""),
            version=str(payload.get("version") or ""),
            prefix=str(payload.get("prefix") or ""),
            environment_root=resolved.environment_root,
            manager=resolved.manager,
            execution_environment=resolved.request.execution_environment,
            destination=False,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _manager_config_files(info: PythonProjectInfo) -> tuple[tuple[str, str], ...]:
    names = ["pyproject.toml", "poetry.toml", "Pipfile", "Pipfile.lock", "pdm.lock", "uv.lock", "hatch.toml"]
    result: list[tuple[str, str]] = []
    for name in names:
        path = info.root / name
        if path.is_file():
            result.append((name, _hash_file(path)))
    return tuple(result)


def _run_probe(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout: float = 15.0
) -> str:
    try:
        proc = subprocess.run(
            list(argv), cwd=cwd, env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def _manager_version(executable: str, *, cwd: Path, env: Mapping[str, str]) -> str:
    if not executable:
        return ""
    text = _run_probe([executable, "--version"], cwd=cwd, env=env)
    return text.splitlines()[0] if text else ""


def _poetry_lock_consistent(executable: str, *, cwd: Path, env: Mapping[str, str]) -> tuple[bool, str]:
    if not executable:
        return False, "Poetry executable is unavailable"
    try:
        proc = subprocess.run([executable, "check", "--lock"], cwd=cwd, env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Poetry lock check could not run: {exc}"
    return proc.returncode == 0, (proc.stdout or "").strip()


def probe_manager_environment(
    info: PythonProjectInfo, *, env_root_hint: str = "", env: Mapping[str, str] | None = None,
    composition: EnvironmentCompositionPlan | None = None, groups: Sequence[str] = (), extras: Sequence[str] = (),
    lock_policy: str = "prefer", runtime_provider: str = "auto", install_project: bool = True,
) -> PythonManagerReceipt:
    manager = info.manager
    environment = dict(os.environ if env is None else env)
    resolved_provider: dict[str, str] = {}
    if composition and composition.mise.selected:
        resolved_provider = dict(composition.mise.resolved_tools)
    elif composition and composition.asdf.selected:
        resolved_provider = dict(composition.asdf.resolved_tools)
    elif composition and composition.pyenv.selected:
        resolved_provider = dict(composition.pyenv.resolved_tools)
    executable = (resolved_provider.get(manager) or shutil.which(manager, path=str(environment.get("PATH", ""))) or "") if manager != "venv" else ""
    environment_root = env_root_hint
    python = ""
    detail = ""
    if manager == "poetry" and executable:
        out = _run_probe([executable, "env", "info", "--executable"], cwd=info.root, env=environment)
        python = out.splitlines()[-1] if out.splitlines() else ""
        out = _run_probe([executable, "env", "info", "--path"], cwd=info.root, env=environment)
        environment_root = out.splitlines()[-1] if out.splitlines() else environment_root
    elif manager == "pdm" and executable:
        out = _run_probe([executable, "info", "--python"], cwd=info.root, env=environment)
        python = out.splitlines()[-1] if out.splitlines() else ""
        if python:
            environment_root = str(Path(python).expanduser().parent.parent)
    elif manager == "pipenv" and executable:
        out = _run_probe([executable, "--py"], cwd=info.root, env=environment)
        python = out.splitlines()[-1] if out.splitlines() else ""
        if python:
            environment_root = str(Path(python).expanduser().parent.parent)
    elif manager == "hatch" and executable:
        found = _run_probe([executable, "env", "find"], cwd=info.root, env=environment)
        if found:
            environment_root = found.splitlines()[-1]
            candidate = Path(environment_root) / "bin" / "python"
            if candidate.is_file():
                python = str(candidate)
    lock_path = info.root / info.lock_file if info.lock_file else None
    return PythonManagerReceipt(
        manager=manager, executable=executable, manager_version=_manager_version(executable, cwd=info.root, env=environment),
        environment_root=environment_root, python_executable=python, lock_file=info.lock_file,
        lock_digest=_hash_file(lock_path) if lock_path else "", config_files=_manager_config_files(info),
        groups=tuple(groups), extras=tuple(extras), lock_policy=lock_policy, runtime_provider=runtime_provider, install_project=install_project,
        qualified=(manager == "venv" or bool(executable)), detail=detail,
    )


def _ownership(info: PythonProjectInfo, environment_root: str, request: PythonEnvironmentRequest) -> str:
    if info.manager in _MANAGER_OWNED:
        return "manager-owned"
    if request.environment:
        try:
            root = Path(request.environment).expanduser().resolve()
            repo = info.root.resolve()
            return "adopted" if root == repo or repo in root.parents else "external"
        except OSError:
            return "external"
    return "managed" if environment_root else "external"


def resolve_python_environment(
    info: PythonProjectInfo,
    request: PythonEnvironmentRequest,
    *,
    composition: EnvironmentCompositionPlan | None = None,
    env: Mapping[str, str] | None = None,
) -> ResolvedPythonEnvironment:
    if request.manager not in {"", "auto", info.manager}:
        raise PythonProjectError(f"Python request manager {request.manager!r} does not match resolved project manager {info.manager!r}")
    if request.environment and info.manager in _MANAGER_OWNED:
        raise PythonProjectError(
            f"Explicit [python].environment cannot be combined with manager-owned {info.manager}; "
            "configure the manager's environment or select venv/uv explicitly instead of silently ignoring it."
        )
    if request.lock_policy == "require" and not info.lock_file:
        raise PythonProjectError(f"Python lock policy 'require' cannot be satisfied by {info.manager}: no recognized lock file is present")

    execution = _normalized_execution(request.execution_environment)
    environment = request.environment
    if not environment and (info.manager in {"venv", "uv"}) and execution in _REMOTE_ENVIRONMENTS:
        environment = destination_python_environment_root(info.root, execution, request.destination_name)

    legacy = resolve_environment(
        info,
        configured_python=request.configured_python,
        environment=environment,
        groups=request.groups,
        extras=request.extras,
        execution_environment=request.execution_environment,
        strict_configured_python=request.configured_python_explicit,
    )
    receipt = probe_manager_environment(
        info, env_root_hint=legacy.environment_root, env=env, composition=composition, groups=request.groups, extras=request.extras,
        lock_policy=request.lock_policy, runtime_provider=request.runtime_provider,
    ) if execution not in _REMOTE_ENVIRONMENTS else PythonManagerReceipt(
        manager=info.manager, executable=info.manager if info.manager != "venv" else "", manager_version="",
        environment_root=legacy.environment_root, python_executable="", lock_file=info.lock_file,
        lock_digest=_hash_file(info.root / info.lock_file) if info.lock_file else "", config_files=_manager_config_files(info),
        groups=request.groups, extras=request.extras, lock_policy=request.lock_policy, runtime_provider=request.runtime_provider,
        qualified=True, detail="destination runtime probe deferred to execution destination",
    )

    if execution not in _REMOTE_ENVIRONMENTS and info.manager in _MANAGER_OWNED and not receipt.qualified and request.action != "restore":
        raise PythonProjectError(
            f"Python environment manager {info.manager!r} is selected but could not be qualified: "
            f"{receipt.detail or 'manager executable/environment probe unavailable'}"
        )
    if execution not in _REMOTE_ENVIRONMENTS and info.manager == "poetry" and request.lock_policy == "require":
        ok, lock_detail = _poetry_lock_consistent(receipt.executable, cwd=info.root, env=dict(os.environ if env is None else env))
        if not ok:
            raise PythonProjectError("Poetry locked mode requires a consistent existing lock: " + (lock_detail or "poetry check --lock failed"))

    python = legacy.python
    env_root = legacy.environment_root
    compatible = legacy.compatible
    detail = legacy.compatibility_detail
    fingerprint = legacy.fingerprint
    inputs = legacy.fingerprint_inputs
    if receipt.python_executable and execution not in _REMOTE_ENVIRONMENTS:
        python = receipt.python_executable
        env_root = receipt.environment_root or env_root
        version, version_text = _interpreter_version(python)
        compatible = _specifier_satisfied(version, info.requires_python)
        detail = (
            f"Python {version_text or 'unknown'} from {info.manager} environment satisfies {info.requires_python or 'unspecified requirement'}"
            if compatible else f"Python {version_text or 'unknown'} from {info.manager} environment does not satisfy requires-python {info.requires_python}"
        )
        fingerprint, inputs = environment_fingerprint(
            info,
            python=python,
            groups=request.groups,
            extras=request.extras,
            execution_environment=request.execution_environment,
            probe_runtime=True,
        )
        fingerprint = _digest({"base": fingerprint, "manager_receipt": receipt.identity, "lock_policy": request.lock_policy})
    else:
        fingerprint = _digest({"base": fingerprint, "manager_receipt": receipt.identity, "lock_policy": request.lock_policy})

    return ResolvedPythonEnvironment(
        request=request,
        manager=info.manager,
        runtime_provider=request.runtime_provider,
        ownership=_ownership(info, env_root, request),
        python=python,
        bootstrap_python=legacy.bootstrap_python,
        run_prefix=legacy.run_prefix,
        environment_root=env_root,
        fingerprint=fingerprint,
        fingerprint_inputs=inputs,
        compatible=compatible,
        compatibility_detail=detail,
        manager_receipt=receipt,
        composition_identity=composition.identity if composition else "",
        destination_owned=request.destination,
    )
