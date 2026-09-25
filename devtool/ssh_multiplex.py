from __future__ import annotations

"""Operation-scoped OpenSSH multiplexing for Devtool SSH workers.

The control socket is a transport optimization only. Canonical run/workspace
identity remains EXO/RunStore + SSH workspace identity. Paths stay deliberately
short for Termux UNIX-socket limits, and a mux-specific failure disables reuse
for the remainder of that operation instead of failing the build.
"""

from dataclasses import dataclass
import atexit
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .ssh_tmux_runner import SSHRunnerConfig

_FALLBACK_SCOPES: dict[str, str] = {}
_REGISTERED_CONFIGS: dict[str, "SSHRunnerConfig"] = {}
_ATEXIT_REGISTERED = False


@dataclass(frozen=True, slots=True)
class SSHMultiplexEvidence:
    enabled: bool
    control_path: str
    persist_seconds: int
    operation_scope: str
    local_ssh: str
    detail: str
    fallback_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "control_path": self.control_path,
            "persist_seconds": self.persist_seconds,
            "operation_scope": self.operation_scope,
            "local_ssh": self.local_ssh,
            "detail": self.detail,
            "fallback_reason": self.fallback_reason,
        }


def _scope(config: "SSHRunnerConfig") -> str:
    return str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or f"pid-{os.getpid()}").strip()


def _identity(config: "SSHRunnerConfig") -> str:
    return "\n".join((str(getattr(config, "profile", "") or "raw"), str(config.destination), str(config.port), _scope(config)))


def _key(config: "SSHRunnerConfig") -> str:
    return hashlib.sha256(_identity(config).encode("utf-8")).hexdigest()[:24]


def _root(config: "SSHRunnerConfig") -> Path:
    explicit = str(getattr(config, "control_root", "") or os.environ.get("DEVTOOL_SSH_CONTROL_ROOT") or "").strip()
    if explicit:
        root = Path(explicit).expanduser()
    else:
        # On Termux this normally becomes $PREFIX/tmp/dtcm, which is short.
        root = Path(tempfile.gettempdir()) / "dtcm"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def control_path(config: "SSHRunnerConfig") -> str:
    return str(_root(config) / f"c-{_key(config)}")


def multiplex_enabled(config: "SSHRunnerConfig") -> bool:
    if str(os.environ.get("DEVTOOL_SSH_DISABLE_MULTIPLEX", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if _key(config) in _FALLBACK_SCOPES:
        return False
    return bool(getattr(config, "multiplex", bool(getattr(config, "profile", ""))))


def _register_cleanup(config: "SSHRunnerConfig") -> None:
    global _ATEXIT_REGISTERED
    _REGISTERED_CONFIGS[_key(config)] = config
    if not _ATEXIT_REGISTERED:
        atexit.register(_cleanup_registered_masters)
        _ATEXIT_REGISTERED = True


def _cleanup_registered_masters() -> None:
    for config in list(_REGISTERED_CONFIGS.values()):
        try:
            close_master(config)
        except Exception:
            pass
    _REGISTERED_CONFIGS.clear()


def multiplex_options(config: "SSHRunnerConfig") -> list[str]:
    if not multiplex_enabled(config):
        return []
    _register_cleanup(config)
    persist = max(0, int(getattr(config, "control_persist_seconds", 90) or 0))
    return ["-o", "ControlMaster=auto", "-o", f"ControlPersist={persist}", "-o", f"ControlPath={control_path(config)}"]


def strip_multiplex_options(argv: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == "-o" and index + 1 < len(argv):
            value = argv[index + 1]
            if value.startswith(("ControlMaster=", "ControlPersist=", "ControlPath=")):
                index += 2
                continue
        result.append(argv[index])
        index += 1
    return result


def strip_multiplex_from_shell(shell: str) -> str:
    try:
        return shlex.join(strip_multiplex_options(shlex.split(str(shell or ""))))
    except ValueError:
        return str(shell or "")


def multiplex_failure(stderr: str) -> bool:
    text = str(stderr or "").lower()
    return any(token in text for token in (
        "controlpath too long", "mux_client", "control socket", "controlmaster",
        "bad configuration option: control", "master refused", "master is dead",
        "cannot bind to path", "unix_listener", "connection to master failed",
    ))


def disable_for_operation(config: "SSHRunnerConfig", reason: str) -> None:
    _FALLBACK_SCOPES[_key(config)] = str(reason or "multiplexing failed")[:500]


def run_with_multiplex_fallback(config: "SSHRunnerConfig", argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run an SSH-family argv once, retrying without mux only for mux failures."""
    completed = subprocess.run(argv, **kwargs)
    if completed.returncode == 0 or not multiplex_enabled(config):
        return completed
    stderr = getattr(completed, "stderr", "") or ""
    stdout = getattr(completed, "stdout", "") or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    detail = str(stderr or stdout)
    if not multiplex_failure(detail):
        return completed
    disable_for_operation(config, detail.strip() or "OpenSSH multiplexing failed")
    retry = strip_multiplex_options(list(argv))
    return subprocess.run(retry, **kwargs)


def close_master(config: "SSHRunnerConfig") -> bool:
    """Best-effort operation cleanup. ControlPersist remains the safety net."""
    path = control_path(config)
    if not Path(path).exists():
        return True
    base = shutil.which(str(os.environ.get("DEVTOOL_SSH_COMMAND", "ssh") or "ssh")) or "ssh"
    argv = [base, "-p", str(config.port)]
    if config.identity:
        argv.extend(["-i", str(Path(config.identity).expanduser())])
    # Avoid importing ssh_tmux_runner (cycle); host-key settings are irrelevant
    # for an existing local master socket. -S selects the exact operation socket.
    argv.extend(["-S", path, "-O", "exit", config.destination])
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)
        return completed.returncode == 0 or not Path(path).exists()
    except (OSError, subprocess.SubprocessError):
        return False


def evidence(config: "SSHRunnerConfig") -> SSHMultiplexEvidence:
    enabled = multiplex_enabled(config)
    local_ssh = shutil.which(str(os.environ.get("DEVTOOL_SSH_COMMAND", "ssh") or "ssh")) or ""
    fallback = _FALLBACK_SCOPES.get(_key(config), "")
    detail = (
        "operation-scoped OpenSSH ControlMaster enabled"
        if enabled else
        "multiplexing disabled after graceful fallback" if fallback else
        "multiplexing disabled; independent SSH connections will be used"
    )
    return SSHMultiplexEvidence(
        enabled=enabled,
        control_path=control_path(config) if enabled or fallback else "",
        persist_seconds=max(0, int(getattr(config, "control_persist_seconds", 90) or 0)),
        operation_scope=_scope(config),
        local_ssh=local_ssh,
        detail=detail,
        fallback_reason=fallback,
    )


__all__ = [
    "SSHMultiplexEvidence", "close_master", "control_path", "disable_for_operation",
    "evidence", "multiplex_enabled", "multiplex_failure", "multiplex_options",
    "run_with_multiplex_fallback", "strip_multiplex_from_shell", "strip_multiplex_options",
]
