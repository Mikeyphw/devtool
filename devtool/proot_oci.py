from __future__ import annotations

"""PRoot-Distro / OCI lifecycle management for named SSH workers.

OCI-02 adds read-only discovery plus explicit bootstrap/install/remove lifecycle
operations. Repository command execution remains fail-closed until a later OCI
overlay. All executable OCI lifecycle work uses the caller's canonical EXO run
identity and publishes process ownership, diagnostics, provenance, and truthful
(indeterminate when unmeasured) progress through the canonical event stream.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Iterable, Mapping, Sequence

from .core.events import (
    ArtifactProduced, GraphSealed, NodeBlocked, NodeDiscovered, NodeFinished, NodeProgressChanged,
    NodeStarted, RunFinished, RunStarted, get_event_bus, publish_event,
)
from .core.execution_contract import ExecutionStatus
from .core.execution_subscribers import ExecutionProjectionSubscriber
from .logstore import redact_sensitive_text, redact_sensitive_value
from .oci_image import canonical_android_oci_definition
from .progress_events import RunnerExecutionAdapter
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv
from .xdg import state_dir

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ARCHITECTURES = {
    "aarch64", "arm", "i686", "riscv64", "x86_64",
    "linux/arm64", "linux/amd64", "linux/arm/v7", "linux/386", "linux/riscv64",
}
_LOCAL_PREFIXES = ("/", "./", "../", "~")


@dataclass(frozen=True, slots=True)
class ProotOciRuntimeProbe:
    available: bool
    proot_distro: str
    proot: str
    proot_distro_version: str
    proot_version: str
    platform: str
    architecture: str
    free_storage_kib: int
    containers: tuple[str, ...]
    images: tuple[str, ...]
    container_rc: int
    image_rc: int
    container_error: str
    image_error: str
    nested_proot: bool
    cache_kib: int
    runtime_root: str
    cache_root: str

    @property
    def ready(self) -> bool:
        return bool(self.available and self.proot_distro and self.proot and not self.nested_proot)

    @property
    def inventory_healthy(self) -> bool:
        return bool(self.container_rc == 0 and self.image_rc == 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "ready": self.ready,
            "proot_distro": self.proot_distro,
            "proot": self.proot,
            "proot_distro_version": self.proot_distro_version,
            "proot_version": self.proot_version,
            "platform": self.platform,
            "architecture": self.architecture,
            "free_storage_kib": self.free_storage_kib,
            "containers": list(self.containers),
            "images": list(self.images),
            "container_count": len(self.containers),
            "image_count": len(self.images),
            "container_rc": self.container_rc,
            "image_rc": self.image_rc,
            "inventory_healthy": self.inventory_healthy,
            "container_error": self.container_error,
            "image_error": self.image_error,
            "nested_proot": self.nested_proot,
            "cache_kib": self.cache_kib,
            "runtime_root": self.runtime_root,
            "cache_root": self.cache_root,
        }


def _timeout(name: str, default: float, maximum: float = 7200.0) -> float:
    raw = str(os.environ.get(name, str(default)) or default).strip()
    try:
        return max(1.0, min(float(raw), maximum))
    except ValueError:
        return default


def _transport_timeout() -> float:
    return _timeout("DEVTOOL_SSH_TRANSPORT_TIMEOUT", 30.0, 3600.0)


def _oci_timeout() -> float:
    return _timeout("DEVTOOL_OCI_OPERATION_TIMEOUT", 1800.0, 14400.0)


def _transfer_timeout() -> float:
    return _timeout("DEVTOOL_OCI_TRANSFER_TIMEOUT", 1800.0, 14400.0)


def _sanitized_subprocess_env() -> dict[str, str]:
    """Return a child environment that cannot implicitly forward registry auth.

    OCI-02 intentionally defers private-registry credential transport to OCI-06.
    OpenSSH can forward variables configured through SendEnv, so simply keeping
    PD_DOCKER_AUTH out of argv/provenance is not sufficient. Strip it before
    every SSH subprocess in this module.
    """
    env = dict(os.environ)
    env.pop("PD_DOCKER_AUTH", None)
    return env


def _provenance(
    config: SSHRunnerConfig,
    *,
    operation: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "transport": "ssh",
        "host": config.host,
        "profile": config.profile,
        "port": config.port,
        "execution_provider": "ssh-proot-oci",
        "runtime": "proot-distro",
        "operation": operation,
    }
    for key, item in dict(extra or {}).items():
        if item not in (None, "", (), [], {}):
            value[str(key)] = item
    return dict(redact_sensitive_value(value))


def _stable_node_id(config: SSHRunnerConfig, action: str, key: str = "") -> str:
    profile = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(config.profile or config.host or "worker")).strip("-._") or "worker"
    action = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(action or "operation")).strip("-._") or "operation"
    if not key:
        return f"oci:{profile}:{action}"
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]
    return f"oci:{profile}:{action}:{digest}"


def _safe_text(value: object) -> str:
    return redact_sensitive_text(str(value or ""))


def _discover_nodes(run_id: str, nodes: Sequence[tuple[str, str, tuple[str, ...]]]) -> None:
    for node_id, label, depends_on in nodes:
        publish_event(NodeDiscovered(
            run_id=run_id,
            node_id=node_id,
            label=_safe_text(label),
            kind="oci-lifecycle",
            depends_on=depends_on,
            source="oci-proot",
            metadata={"runtime": "proot-distro"},
        ))
    publish_event(GraphSealed(run_id=run_id, reason="OCI lifecycle operation plan is complete", source="oci-proot"))


def _start_node(run_id: str, node_id: str, label: str, detail: str) -> None:
    publish_event(NodeStarted(
        run_id=run_id,
        node_id=node_id,
        label=_safe_text(label),
        kind="oci-lifecycle",
        source="oci-proot",
    ))
    publish_event(NodeProgressChanged(
        run_id=run_id,
        node_id=node_id,
        indeterminate=True,
        detail=_safe_text(detail),
        source="oci-proot",
    ))


def _block_node(run_id: str, node_id: str, *, blocked_by: Iterable[str], reason: str) -> None:
    publish_event(NodeBlocked(
        run_id=run_id,
        node_id=node_id,
        blocked_by=tuple(str(value) for value in blocked_by if str(value)),
        reason=_safe_text(reason),
        source="oci-proot",
    ))


def _finish_node(
    run_id: str,
    node_id: str,
    *,
    status: str,
    exit_code: int,
    duration: float = 0.0,
    diagnostic_ids: Iterable[str] = (),
    reason: str = "",
) -> None:
    publish_event(NodeFinished(
        run_id=run_id,
        node_id=node_id,
        status=status,
        exit_code=exit_code,
        duration_seconds=duration,
        diagnostic_ids=tuple(diagnostic_ids),
        transition_origin="observed",
        transition_reason=_safe_text(reason),
        source="oci-proot",
    ))


def _remote_artifact_records(text: str) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for raw in str(text or "").splitlines():
        if not raw.startswith("DEVTOOL_OCI_ARTIFACT\t"):
            continue
        parts = raw.split("\t", 4)
        if len(parts) != 5:
            continue
        _, remote_path, digest, size_raw, artifact_type = parts
        if not remote_path or not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
            continue
        try:
            remote_size = int(size_raw)
        except ValueError:
            continue
        if remote_size < 0:
            continue
        records.append({
            "path": _safe_text(remote_path),
            "sha256": digest,
            "size": remote_size,
            "artifact_type": _safe_text(artifact_type or "oci-archive"),
        })
    return tuple(records)


def _run_process(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    node_id: str,
    label: str,
    argv: Sequence[str],
    detail: str,
    timeout: float,
    stdin_path: Path | None = None,
    stdin_bytes: bytes | None = None,
    diagnostic_code: str,
    provenance_extra: Mapping[str, Any] | None = None,
    publish_remote_artifacts: bool = False,
) -> subprocess.CompletedProcess[str]:
    safe_label = _safe_text(label)
    _start_node(run_id, node_id, safe_label, detail)
    adapter = RunnerExecutionAdapter(
        run_id=run_id,
        parent_node_id=node_id,
        tool="proot-distro",
        source="oci-proot",
        provenance=_provenance(config, operation=safe_label, extra=provenance_extra),
    )
    started = time.monotonic()
    if stdin_path is not None and stdin_bytes is not None:
        raise ValueError("OCI process input cannot use both stdin_path and stdin_bytes")
    handle: Any = subprocess.DEVNULL
    proc: subprocess.Popen[bytes] | None = None

    def _fail_before_or_during_process(
        exc: BaseException,
        *,
        code_suffix: str,
        failure_kind: str,
        exit_code: int,
    ) -> ValueError:
        duration = time.monotonic() - started
        message = _safe_text(f"{safe_label} {failure_kind.replace('_', ' ')}: {exc}")
        if proc is not None:
            try:
                if proc.returncode is None:
                    proc.kill()
                    proc.communicate()
            except Exception:
                pass
            adapter.process_exited(
                exit_code=exit_code,
                duration_seconds=duration,
                failure_kind=failure_kind,
                evidence=(message[-2048:],),
            )
        diagnostic_id = adapter.diagnostic(
            severity="error",
            code=f"{diagnostic_code}_{code_suffix}",
            message=message,
            failure_kind=failure_kind,
            evidence=(message[-2048:],),
        )
        _finish_node(
            run_id,
            node_id,
            status=ExecutionStatus.FAILED.value,
            exit_code=exit_code,
            duration=duration,
            diagnostic_ids=(diagnostic_id,),
            reason=failure_kind.replace("_", " "),
        )
        return ValueError(message)

    try:
        if stdin_path is not None:
            try:
                handle = stdin_path.open("rb")
            except OSError as exc:
                raise _fail_before_or_during_process(
                    exc,
                    code_suffix="SOURCE",
                    failure_kind="input_open_error",
                    exit_code=66,
                ) from exc
        try:
            proc = subprocess.Popen(
                list(argv),
                stdin=(subprocess.PIPE if stdin_bytes is not None else handle),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                env=_sanitized_subprocess_env(),
            )
        except OSError as exc:
            raise _fail_before_or_during_process(
                exc,
                code_suffix="SPAWN",
                failure_kind="process_launch_error",
                exit_code=127,
            ) from exc

        adapter.process_started(pid=proc.pid, argv=argv, cwd=str(Path.cwd()))
        try:
            if stdin_bytes is None:
                out_b, err_b = proc.communicate(timeout=timeout)
            else:
                out_b, err_b = proc.communicate(input=stdin_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_b, err_b = proc.communicate()
            duration = time.monotonic() - started
            stdout = (out_b or b"").decode("utf-8", errors="replace")
            stderr = (err_b or b"").decode("utf-8", errors="replace")
            safe_evidence = _safe_text(stderr[-2048:] or stdout[-2048:] or f"timeout:{timeout:g}s")
            adapter.process_exited(
                exit_code=124,
                duration_seconds=duration,
                failure_kind="timeout",
                evidence=(safe_evidence,),
            )
            diagnostic_id = adapter.diagnostic(
                severity="error",
                code=f"{diagnostic_code}_TIMEOUT",
                message=f"{safe_label} timed out after {timeout:g}s",
                failure_kind="timeout",
                evidence=(safe_evidence,),
            )
            _finish_node(
                run_id, node_id, status=ExecutionStatus.FAILED.value, exit_code=124,
                duration=duration, diagnostic_ids=(diagnostic_id,), reason="process timeout",
            )
            raise ValueError(f"{safe_label} timed out after {timeout:g}s")
        except (OSError, subprocess.SubprocessError) as exc:
            raise _fail_before_or_during_process(
                exc,
                code_suffix="IO",
                failure_kind="process_io_error",
                exit_code=1,
            ) from exc
    finally:
        if stdin_path is not None and handle is not subprocess.DEVNULL:
            try:
                handle.close()
            except OSError:
                pass

    duration = time.monotonic() - started
    stdout = (out_b or b"").decode("utf-8", errors="replace")
    stderr = (err_b or b"").decode("utf-8", errors="replace")
    safe_evidence = _safe_text(stderr[-2048:] or stdout[-2048:])
    remote_artifacts = _remote_artifact_records(stdout) if publish_remote_artifacts else ()
    if proc.returncode == 0 and publish_remote_artifacts and not remote_artifacts:
        message = f"{safe_label} completed without required SHA-verified artifact evidence"
        adapter.process_exited(
            exit_code=65,
            duration_seconds=duration,
            failure_kind="artifact_evidence_missing",
            evidence=(_safe_text(stdout[-2048:] or message),),
        )
        diagnostic_id = adapter.diagnostic(
            severity="error",
            code=f"{diagnostic_code}_ARTIFACT",
            message=message,
            failure_kind="artifact_evidence_missing",
            evidence=(_safe_text(stdout[-2048:] or message),),
        )
        _finish_node(
            run_id, node_id, status=ExecutionStatus.FAILED.value, exit_code=65,
            duration=duration, diagnostic_ids=(diagnostic_id,), reason="required artifact evidence missing",
        )
        raise ValueError(message)
    if proc.returncode == 0:
        for record in remote_artifacts:
            remote_path = record["path"]
            digest = record["sha256"]
            remote_size = record["size"]
            artifact_type = record["artifact_type"]
            safe_path = _safe_text(remote_path)
            producer = node_id
            stable_key = "\x1f".join((run_id, producer, safe_path, digest))
            publish_event(ArtifactProduced(
                run_id=run_id,
                node_id=producer,
                path=safe_path,
                artifact_id="oci-" + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:20],
                sha256=digest,
                size=remote_size,
                artifact_type=_safe_text(artifact_type or "oci-archive"),
                producer_task=safe_label,
                producer_node_id=producer,
                process_id=adapter.active_process_id,
                verification_status="sha256-verified",
                source="oci-proot",
                metadata={
                    "tool": "proot-distro",
                    "remote": True,
                    **({"image_digest": _safe_text(_parse_key_values(stdout).get("image_digest", ""))} if _parse_key_values(stdout).get("image_digest") else {}),
                    **({"image_reference": _safe_text(str((provenance_extra or {}).get("image_reference", "")))} if (provenance_extra or {}).get("image_reference") else {}),
                    **({"architecture": _safe_text(str((provenance_extra or {}).get("architecture", "")))} if (provenance_extra or {}).get("architecture") else {}),
                },
            ))
    adapter.process_exited(
        exit_code=int(proc.returncode or 0),
        duration_seconds=duration,
        failure_kind="" if proc.returncode == 0 else "oci_runtime_error",
        evidence=(safe_evidence,) if proc.returncode else (),
    )
    if proc.returncode != 0:
        raw_message = (stderr or stdout or "no diagnostic output").strip()
        message = _safe_text(raw_message)
        diagnostic_id = adapter.diagnostic(
            severity="error",
            code=diagnostic_code,
            message=f"{safe_label} failed with exit {proc.returncode}: {message[:4000]}",
            failure_kind="oci_runtime_error",
            evidence=(safe_evidence,),
        )
        _finish_node(
            run_id, node_id, status=ExecutionStatus.FAILED.value, exit_code=proc.returncode,
            duration=duration, diagnostic_ids=(diagnostic_id,), reason="remote process exited nonzero",
        )
        raise ValueError(f"{safe_label} failed with exit {proc.returncode}: {message}")
    _finish_node(run_id, node_id, status=ExecutionStatus.SUCCEEDED.value, exit_code=0, duration=duration)
    return subprocess.CompletedProcess(list(argv), proc.returncode, stdout=stdout, stderr=stderr)

def _run_remote_plain(config: SSHRunnerConfig, script: str, *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        build_ssh_remote_sh_argv(config, script, tty=False),
        check=False,
        capture_output=True,
        text=True,
        timeout=_transport_timeout() if timeout is None else timeout,
        env=_sanitized_subprocess_env(),
    )


def _runtime_probe_script(proot_distro: str = "", *, strict_inventory: str = "") -> str:
    preferred = shlex.quote(str(proot_distro or ""))
    strict = str(strict_inventory or "").strip().lower()
    if strict not in {"", "containers", "images"}:
        raise ValueError(f"Unknown OCI inventory strictness: {strict_inventory}")
    strict_q = shlex.quote(strict)
    template = r'''set +e
pd=__PD__
strict_inventory=__STRICT__
if [ -z "$pd" ] || [ ! -x "$pd" ]; then
  pd="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"
fi
proot_bin="${PD_PROOT_BIN:-}"
if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]; then
  proot_bin="$(command -v proot 2>/dev/null || true)"
fi
printf 'proot_distro=%s\n' "$pd"
printf 'proot=%s\n' "$proot_bin"
nested_proot=0
if [ -n "${PROOT_TMP_DIR:-}" ] || [ -n "${PROOT_LOADER:-}" ]; then nested_proot=1; fi
printf 'nested_proot=%s\n' "$nested_proot"
termux_score=0
if [ -f /system/build.prop ] || [ -d /data/app ]; then termux_score=$((termux_score + 1)); fi
if [ -n "${TERMUX_APP__APP_VERSION_NAME:-}" ] || [ -n "${TERMUX_VERSION:-}" ]; then termux_score=$((termux_score + 1)); fi
termux_prefix="${TERMUX__PREFIX:-${PREFIX:-/data/data/com.termux/files/usr}}"
if [ -r "$termux_prefix" ] && [ -x "$termux_prefix" ]; then termux_score=$((termux_score + 1)); fi
platform=linux
if [ "$termux_score" -ge 2 ]; then platform=termux; fi
printf 'platform=%s\n' "$platform"
printf 'architecture=%s\n' "$(uname -m 2>/dev/null || printf unknown)"
free="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4; exit}')"
case "$free" in ''|*[!0-9]*) free=0;; esac
printf 'free_storage_kib=%s\n' "$free"
pd_version=""
python_bin="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [ -n "$pd" ] && [ -n "$python_bin" ]; then
  pd_version="$($python_bin -c 'import importlib.metadata as m; print(m.version("proot-distro"))' 2>/dev/null | awk 'NF {print; exit}')"
fi
printf 'proot_distro_version=%s\n' "$pd_version"
proot_version=""
if [ -n "$proot_bin" ]; then proot_version="$($proot_bin --version 2>&1 | awk 'NF {print; exit}')"; fi
printf 'proot_version=%s\n' "$proot_version"
runtime_root=""
cache_root=""
if [ "$platform" = termux ]; then
  runtime_root="$termux_prefix/var/lib/proot-distro"
  cache_root="$runtime_root/cache"
else
  data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"
  runtime_root="$data_home/proot-distro"
  cache_root="$cache_home/proot-distro"
fi
printf 'runtime_root=%s\n' "$runtime_root"
printf 'cache_root=%s\n' "$cache_root"
cache_kib=0
if [ -n "$cache_root" ] && [ -d "$cache_root" ]; then
  cache_kib="$(du -sk "$cache_root" 2>/dev/null | awk '{print $1; exit}')"
fi
case "$cache_kib" in ''|*[!0-9]*) cache_kib=0;; esac
printf 'cache_kib=%s\n' "$cache_kib"
if [ "$nested_proot" = 1 ]; then
  printf 'containers_rc=125\n'
  printf 'container=Refusing PRoot-Distro inventory inside an existing PRoot environment\n'
  printf 'images_rc=125\n'
  printf 'image=Refusing PRoot-Distro inventory inside an existing PRoot environment\n'
  case "$strict_inventory" in containers|images) exit 125;; esac
  exit 0
fi
if [ -z "$pd" ]; then
  printf 'containers_rc=127\nimages_rc=127\n'
  case "$strict_inventory" in containers|images) exit 127;; esac
  exit 0
fi
containers="$($pd list --quiet 2>&1)"; containers_rc=$?
printf 'containers_rc=%s\n' "$containers_rc"
printf '%s\n' "$containers" | while IFS= read -r line; do [ -n "$line" ] && printf 'container=%s\n' "$line"; done
images="$($pd list --image --quiet 2>&1)"; images_rc=$?
printf 'images_rc=%s\n' "$images_rc"
printf '%s\n' "$images" | while IFS= read -r line; do [ -n "$line" ] && printf 'image=%s\n' "$line"; done
case "$strict_inventory" in
  containers) exit "$containers_rc";;
  images) exit "$images_rc";;
esac
exit 0
'''
    return template.replace("__PD__", preferred, 1).replace("__STRICT__", strict_q, 1)

def _parse_runtime_probe(stdout: str) -> ProotOciRuntimeProbe:
    scalar: dict[str, str] = {}
    containers: list[str] = []
    images: list[str] = []
    for raw in str(stdout or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "container":
            containers.append(value)
        elif key == "image":
            images.append(value)
        else:
            scalar[key] = value
    def _rc(name: str) -> int:
        try:
            return int(scalar.get(name, "127") or 127)
        except (TypeError, ValueError):
            return 127

    containers_rc = _rc("containers_rc")
    images_rc = _rc("images_rc")
    container_error = "" if containers_rc == 0 else "\n".join(containers)
    image_error = "" if images_rc == 0 else "\n".join(images)
    if containers_rc != 0:
        containers = []
    if images_rc != 0:
        images = []
    combined = (container_error + "\n" + image_error).lower()
    nested = scalar.get("nested_proot", "0") == "1" or (
        "proot" in combined and ("nested" in combined or "another proot" in combined or "inside proot" in combined)
    )
    try:
        cache_kib = max(0, int(scalar.get("cache_kib", "0") or 0))
    except ValueError:
        cache_kib = 0
    try:
        free_storage_kib = max(0, int(scalar.get("free_storage_kib", "0") or 0))
    except ValueError:
        free_storage_kib = 0
    pd = scalar.get("proot_distro", "")
    proot = scalar.get("proot", "")
    return ProotOciRuntimeProbe(
        available=bool(pd),
        proot_distro=pd,
        proot=proot,
        proot_distro_version=scalar.get("proot_distro_version", ""),
        proot_version=scalar.get("proot_version", ""),
        platform=scalar.get("platform", "unknown") or "unknown",
        architecture=scalar.get("architecture", "unknown") or "unknown",
        free_storage_kib=free_storage_kib,
        containers=tuple(containers),
        images=tuple(images),
        container_rc=containers_rc,
        image_rc=images_rc,
        container_error=container_error,
        image_error=image_error,
        nested_proot=nested,
        cache_kib=cache_kib,
        runtime_root=scalar.get("runtime_root", ""),
        cache_root=scalar.get("cache_root", ""),
    )


def probe_proot_oci_runtime(config: SSHRunnerConfig, *, proot_distro: str = "") -> ProotOciRuntimeProbe:
    completed = _run_remote_plain(config, _runtime_probe_script(proot_distro), timeout=max(_transport_timeout(), 20.0))
    if completed.returncode != 0:
        detail = _safe_text((completed.stderr or completed.stdout or "").strip())
        raise ValueError(f"PRoot-Distro runtime probe failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return _parse_runtime_probe(completed.stdout)


def _probe_runtime_exo(
    config: SSHRunnerConfig,
    run_id: str,
    *,
    action: str,
    strict_inventory: str = "",
) -> tuple[ProotOciRuntimeProbe, str]:
    node_id = _stable_node_id(config, f"{action}-runtime-probe")
    _discover_nodes(run_id, ((node_id, "Discover PRoot-Distro runtime", ()),))
    completed = _run_process(
        config,
        run_id=run_id,
        node_id=node_id,
        label="PRoot-Distro runtime discovery",
        argv=build_ssh_remote_sh_argv(
            config, _runtime_probe_script(strict_inventory=strict_inventory), tty=False
        ),
        detail="Discovering PRoot-Distro, containers, and cached OCI images",
        timeout=max(_transport_timeout(), 20.0),
        diagnostic_code="OCI_RUNTIME_PROBE",
    )
    probe = _parse_runtime_probe(completed.stdout)
    if not strict_inventory and probe.available and not probe.nested_proot:
        warning_adapter = RunnerExecutionAdapter(
            run_id=run_id,
            parent_node_id=node_id,
            tool="proot-distro",
            source="oci-proot",
            provenance=_provenance(config, operation="PRoot-Distro runtime discovery"),
        )
        if probe.container_rc != 0:
            detail = _safe_text(probe.container_error or f"proot-distro list exited {probe.container_rc}")
            warning_adapter.diagnostic(
                severity="warning",
                code="OCI_CONTAINER_INVENTORY",
                message=f"PRoot-Distro container inventory was incomplete: {detail[:2000]}",
                failure_kind="oci_inventory_error",
                evidence=(detail[-2048:],),
            )
        if probe.image_rc != 0:
            detail = _safe_text(probe.image_error or f"proot-distro list --image exited {probe.image_rc}")
            warning_adapter.diagnostic(
                severity="warning",
                code="OCI_IMAGE_INVENTORY",
                message=f"PRoot-Distro image inventory was incomplete: {detail[:2000]}",
                failure_kind="oci_inventory_error",
                evidence=(detail[-2048:],),
            )
    return probe, node_id


def oci_doctor_payload(config: SSHRunnerConfig, *, run_id: str) -> dict[str, Any]:
    probe, node_id = _probe_runtime_exo(config, run_id, action="doctor")
    return {
        "schema_version": 1,
        "kind": "oci-doctor",
        "ok": bool(probe.ready and probe.inventory_healthy),
        "profile": config.profile,
        "destination": config.destination,
        "port": config.port,
        "run_id": run_id,
        "node_id": node_id,
        "runtime": probe.as_dict(),
        "bootstrap_supported": bool(probe.platform == "termux" and config.profile and not probe.nested_proot),
        "detail": (
            "PRoot-Distro runtime ready"
            if probe.ready and probe.inventory_healthy
            else (
                "PRoot-Distro runtime is ready but container/image inventory is incomplete"
                if probe.ready
                else (
                    "PRoot-Distro runtime is blocked inside an existing nested PRoot environment"
                    if probe.nested_proot
                    else (
                        "PRoot-Distro runtime is not ready; run devtool oci bootstrap PROFILE"
                        if probe.platform == "termux"
                        else "PRoot-Distro runtime is not ready; provision proot-distro/proot on this Linux worker"
                    )
                )
            )
        ),
    }


def oci_images_payload(config: SSHRunnerConfig, *, run_id: str) -> dict[str, Any]:
    probe, node_id = _probe_runtime_exo(config, run_id, action="images", strict_inventory="images")
    if not probe.available:
        raise ValueError("PRoot-Distro is not installed on this worker; run devtool oci bootstrap")
    if probe.image_error:
        raise ValueError(f"Unable to list cached OCI images: {probe.image_error}")
    return {
        "schema_version": 1,
        "kind": "oci-images",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "run_id": run_id,
        "node_id": node_id,
        "records": [{"reference": value} for value in probe.images],
        "count": len(probe.images),
        "cache_kib": probe.cache_kib,
        "cache_root": probe.cache_root,
    }


def oci_containers_payload(config: SSHRunnerConfig, *, run_id: str) -> dict[str, Any]:
    probe, node_id = _probe_runtime_exo(config, run_id, action="containers", strict_inventory="containers")
    if not probe.available:
        raise ValueError("PRoot-Distro is not installed on this worker; run devtool oci bootstrap")
    if probe.container_error:
        raise ValueError(f"Unable to list PRoot-Distro containers: {probe.container_error}")
    return {
        "schema_version": 1,
        "kind": "oci-containers",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "run_id": run_id,
        "node_id": node_id,
        "records": [{"name": value} for value in probe.containers],
        "count": len(probe.containers),
        "runtime_root": probe.runtime_root,
    }


def _validate_architecture(value: str) -> str:
    arch = str(value or "").strip()
    if arch and arch not in _ARCHITECTURES:
        raise ValueError(
            f"Unsupported OCI architecture {arch!r}; expected one of: {', '.join(sorted(_ARCHITECTURES))}"
        )
    return arch


def _validate_name(value: str) -> str:
    name = str(value or "").strip()
    if name and not _CONTAINER_RE.fullmatch(name):
        raise ValueError("OCI container name must start with a letter/digit and contain only letters, digits, '_', '.', '-'")
    return name


def _source_kind(source: str) -> tuple[str, Path | None]:
    text = str(source or "").strip()
    if not text:
        raise ValueError("OCI image reference or archive path cannot be empty")
    # Match PRoot-Distro's own source classification: only explicit path-like
    # operands are local archives. A bare token such as ``ubuntu`` remains an
    # OCI registry reference even if a controller-side file named ``ubuntu``
    # happens to exist in the current directory.
    if text.startswith(_LOCAL_PREFIXES):
        path = Path(text).expanduser()
        if path.is_file():
            return "local-archive", path.resolve()
        raise ValueError(f"Local OCI archive does not exist: {path}")
    return "registry", None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def oci_bootstrap_payload(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    node_id = _stable_node_id(config, "bootstrap-proot-distro")
    _discover_nodes(run_id, ((node_id, "Bootstrap PRoot-Distro", ()),))
    command = r'''set -eu
if [ -n "${PROOT_TMP_DIR:-}" ] || [ -n "${PROOT_LOADER:-}" ]; then
  echo 'Refusing OCI bootstrap inside an existing PRoot environment' >&2
  exit 125
fi
termux_score=0
if [ -f /system/build.prop ] || [ -d /data/app ]; then termux_score=$((termux_score + 1)); fi
if [ -n "${TERMUX_APP__APP_VERSION_NAME:-}" ] || [ -n "${TERMUX_VERSION:-}" ]; then termux_score=$((termux_score + 1)); fi
termux_prefix="${TERMUX__PREFIX:-${PREFIX:-/data/data/com.termux/files/usr}}"
if [ -r "$termux_prefix" ] && [ -x "$termux_prefix" ]; then termux_score=$((termux_score + 1)); fi
if [ "$termux_score" -lt 2 ]; then
  echo 'OCI bootstrap is supported only on a native Termux worker; install proot-distro/proot manually on regular Linux' >&2
  exit 126
fi
changed=0
existing_proot="${PD_PROOT_BIN:-}"
if [ -z "$existing_proot" ] || [ ! -x "$existing_proot" ]; then existing_proot="$(command -v proot 2>/dev/null || true)"; fi
if [ -n "$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)" ] && [ -n "$existing_proot" ] && [ "${DEVTOOL_OCI_FORCE_BOOTSTRAP:-0}" != 1 ]; then
  :
else
  command -v pkg >/dev/null 2>&1 || { echo 'Termux pkg is required for OCI bootstrap' >&2; exit 126; }
  pkg install -y proot-distro
  changed=1
fi
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"
proot_bin="${PD_PROOT_BIN:-}"
if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]; then proot_bin="$(command -v proot 2>/dev/null || true)"; fi
[ -n "$pd_bin" ] || { echo 'proot-distro is still unavailable after bootstrap' >&2; exit 127; }
[ -n "$proot_bin" ] || { echo 'proot is still unavailable after bootstrap' >&2; exit 127; }
printf 'changed=%s\nproot_distro=%s\nproot=%s\n' "$changed" "$pd_bin" "$proot_bin"
'''
    if dry_run:
        _finish_node(run_id, node_id, status=ExecutionStatus.SKIPPED.value, exit_code=0, reason="dry-run")
        return {
            "schema_version": 1, "kind": "oci-bootstrap", "ok": True, "changed": False,
            "dry_run": True, "profile": config.profile, "destination": config.destination,
            "run_id": run_id, "node_ids": [node_id], "command": "pkg install -y proot-distro",
            "detail": "PRoot-Distro bootstrap plan ready",
        }
    if force:
        command = "DEVTOOL_OCI_FORCE_BOOTSTRAP=1; export DEVTOOL_OCI_FORCE_BOOTSTRAP; " + command
    completed = _run_process(
        config, run_id=run_id, node_id=node_id, label="Bootstrap PRoot-Distro",
        argv=build_ssh_remote_sh_argv(config, command, tty=False),
        detail="Ensuring proot-distro and proot are installed through Termux pkg",
        timeout=_oci_timeout(), diagnostic_code="OCI_BOOTSTRAP",
    )
    values: dict[str, str] = {}
    for raw in completed.stdout.splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return {
        "schema_version": 1, "kind": "oci-bootstrap", "ok": True,
        "changed": values.get("changed") == "1",
        "profile": config.profile, "destination": config.destination, "run_id": run_id,
        "node_ids": [node_id],
        "runtime": {"proot_distro": values.get("proot_distro", ""), "proot": values.get("proot", "")},
        "detail": "PRoot-Distro runtime ready",
    }

def oci_install_payload(
    config: SSHRunnerConfig,
    source: str,
    *,
    run_id: str,
    name: str = "",
    architecture: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    name = _validate_name(name)
    architecture = _validate_architecture(architecture)
    source_kind, local_path = _source_kind(source)
    safe_source = _safe_text(source)
    upload_node = ""
    digest = ""
    source_size: int | None = None
    staged_source = str(source)
    nodes: list[tuple[str, str, tuple[str, ...]]] = []
    if local_path is not None:
        # Capture immutable controller-side source evidence before planning any
        # remote mutation. The full SHA-256 is the remote cache identity.
        source_size = local_path.stat().st_size
        digest = _sha256_file(local_path)
        staged_source = f"$HOME/.cache/devtool/oci/uploads/sha256-{digest}"
        upload_node = _stable_node_id(config, "upload-archive", digest)
        nodes.append((upload_node, f"Upload OCI archive {local_path.name}", ()))
    install_identity = digest if local_path is not None else str(source)
    install_key = f"{source_kind}|{install_identity}|{name}|{architecture}"
    install_node = _stable_node_id(config, "install", install_key)
    nodes.append((install_node, f"Install OCI image {safe_source}", (upload_node,) if upload_node else ()))
    _discover_nodes(run_id, tuple(nodes))

    if dry_run:
        # Planned work that never executes must never transition through RUNNING
        # or claim measured/indeterminate process progress.
        for node_id, _label, _deps in nodes:
            _finish_node(run_id, node_id, status=ExecutionStatus.SKIPPED.value, exit_code=0, reason="dry-run")
        return {
            "schema_version": 1, "kind": "oci-install", "ok": True, "dry_run": True,
            "profile": config.profile, "destination": config.destination, "run_id": run_id,
            "node_ids": [item[0] for item in nodes], "source": source, "source_kind": source_kind,
            "source_sha256": digest, "source_size": source_size,
            "name": name, "architecture": architecture, "staged_source": staged_source if local_path else "",
            "detail": "OCI installation plan ready",
        }

    if local_path is not None:
        filename = staged_source.rsplit("/", 1)[-1]
        # Refuse the use case before any remote filesystem mutation. Stream into
        # a temporary file, verify the complete SHA-256, then atomically publish
        # it under its full content identity. A failed upload therefore cannot
        # poison the content-addressed cache or leave the install node runnable.
        script = (
            "set -eu; "
            "if [ -n \"${PROOT_TMP_DIR:-}\" ] || [ -n \"${PROOT_LOADER:-}\" ]; then "
            "echo 'Refusing OCI archive staging inside an existing PRoot environment' >&2; exit 125; fi; "
            "d=\"$HOME/.cache/devtool/oci/uploads\"; mkdir -p \"$d\"; "
            f"p=\"$d/{filename}\"; tmp=\"$p.part.$$\"; "
            "trap 'rm -f \"$tmp\"' EXIT HUP INT TERM; cat > \"$tmp\"; "
            "command -v sha256sum >/dev/null 2>&1 || { echo 'sha256sum is required for OCI archive verification' >&2; exit 127; }; "
            "actual=\"$(sha256sum \"$tmp\" | awk '{print $1}')\"; "
            f"expected={shlex.quote(digest)}; "
            "[ \"$actual\" = \"$expected\" ] || { echo 'OCI archive SHA-256 mismatch after SSH transfer' >&2; exit 65; }; "
            "mv -f \"$tmp\" \"$p\"; trap - EXIT HUP INT TERM; printf 'staged=%s\\n' \"$p\""
        )
        try:
            _run_process(
                config, run_id=run_id, node_id=upload_node, label=f"Upload OCI archive {local_path.name}",
                argv=build_ssh_remote_sh_argv(config, script, tty=False), detail="Uploading and verifying local OCI archive on worker",
                timeout=_transfer_timeout(), stdin_path=local_path, diagnostic_code="OCI_ARCHIVE_UPLOAD",
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            _block_node(
                run_id,
                install_node,
                blocked_by=(upload_node,),
                reason="OCI install blocked because archive staging did not succeed",
            )
            raise

    args = ["install"]
    if name:
        args += ["--name", name]
    if architecture:
        args += ["--architecture", architecture]
    # Registry refs are shell-quoted literally. Staged local archives use a
    # shell variable to preserve HOME expansion without credential-bearing argv.
    if local_path is not None:
        filename = staged_source.rsplit("/", 1)[-1]
        source_expr = f'"$HOME/.cache/devtool/oci/uploads/{filename}"'
    else:
        source_expr = shlex.quote(str(source))
    args_text = " ".join(shlex.quote(item) for item in args)
    install_script = (
        "set -eu; "
        "if [ -n \"${PROOT_TMP_DIR:-}\" ] || [ -n \"${PROOT_LOADER:-}\" ]; then "
        "echo 'Refusing OCI install inside an existing PRoot environment' >&2; exit 125; fi; "
        "pd_bin=\"$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)\"; "
        "[ -n \"$pd_bin\" ] || { echo 'PRoot-Distro is not installed; run devtool oci bootstrap' >&2; exit 127; }; "
        "proot_bin=\"${PD_PROOT_BIN:-}\"; if [ -z \"$proot_bin\" ] || [ ! -x \"$proot_bin\" ]; then proot_bin=\"$(command -v proot 2>/dev/null || true)\"; fi; "
        "[ -n \"$proot_bin\" ] || { echo 'proot is not installed; run devtool oci bootstrap' >&2; exit 127; }; "
        f"\"$pd_bin\" {args_text} {source_expr}"
    )
    _run_process(
        config, run_id=run_id, node_id=install_node, label=f"Install OCI image {safe_source}",
        argv=build_ssh_remote_sh_argv(config, install_script, tty=False),
        detail="Installing OCI image through PRoot-Distro", timeout=_oci_timeout(), diagnostic_code="OCI_INSTALL",
        provenance_extra={"image_source": safe_source, "source_kind": source_kind, "architecture": architecture, "container": name},
    )
    return {
        "schema_version": 1, "kind": "oci-install", "ok": True,
        "profile": config.profile, "destination": config.destination, "run_id": run_id,
        "node_ids": [item[0] for item in nodes], "source": source, "source_kind": source_kind,
        "source_sha256": digest, "source_size": source_size,
        "staged_source": staged_source if local_path else "",
        "name": name, "architecture": architecture,
        "detail": "OCI image installed",
    }


def oci_remove_payload(
    config: SSHRunnerConfig,
    target: str,
    *,
    run_id: str,
    image: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = str(target or "").strip()
    if not target:
        raise ValueError("OCI remove target cannot be empty")
    if not image:
        target = _validate_name(target)
    safe_target = _safe_text(target)
    node_id = _stable_node_id(config, "remove-image" if image else "remove-container", target)
    label = f"Remove cached OCI image {safe_target}" if image else f"Remove PRoot-Distro container {safe_target}"
    _discover_nodes(run_id, ((node_id, label, ()),))
    if dry_run:
        _finish_node(run_id, node_id, status=ExecutionStatus.SKIPPED.value, exit_code=0, reason="dry-run")
        return {
            "schema_version": 1, "kind": "oci-remove", "ok": True, "dry_run": True,
            "profile": config.profile, "destination": config.destination, "run_id": run_id,
            "node_id": node_id, "target": target, "target_kind": "image" if image else "container",
            "detail": "OCI removal plan ready",
        }
    flag = " --image" if image else ""
    script = (
        "set -eu; "
        "if [ -n \"${PROOT_TMP_DIR:-}\" ] || [ -n \"${PROOT_LOADER:-}\" ]; then "
        "echo 'Refusing OCI remove inside an existing PRoot environment' >&2; exit 125; fi; "
        "pd_bin=\"$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)\"; "
        "[ -n \"$pd_bin\" ] || { echo 'PRoot-Distro is not installed; run devtool oci bootstrap' >&2; exit 127; }; "
        f"\"$pd_bin\" remove{flag} {shlex.quote(target)}"
    )
    _run_process(
        config, run_id=run_id, node_id=node_id, label=label,
        argv=build_ssh_remote_sh_argv(config, script, tty=False),
        detail="Removing OCI runtime state", timeout=_oci_timeout(), diagnostic_code="OCI_REMOVE",
    )
    return {
        "schema_version": 1, "kind": "oci-remove", "ok": True,
        "profile": config.profile, "destination": config.destination, "run_id": run_id,
        "node_id": node_id, "target": target, "target_kind": "image" if image else "container",
        "detail": "OCI runtime state removed",
    }




def _oci_stage_definition_script(definition_digest: str, context_sha256: str) -> str:
    return f'''set -eu
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then
  echo 'Refusing OCI image definition staging inside an existing PRoot environment' >&2; exit 125
fi
command -v sha256sum >/dev/null 2>&1 || {{ echo 'sha256sum is required for OCI context verification' >&2; exit 127; }}
command -v tar >/dev/null 2>&1 || {{ echo 'tar is required for OCI context staging' >&2; exit 127; }}
root="$HOME/.cache/devtool/oci/definitions"
archive="$root/context-{context_sha256}.tar"
dst="$root/sha256-{definition_digest}"
mkdir -p "$root"
tmp="$archive.part.$$"; trap 'rm -rf "$tmp" "$dst.part.$$"' EXIT HUP INT TERM
cat > "$tmp"
actual="$(sha256sum "$tmp" | awk '{{print $1}}')"
[ "$actual" = {shlex.quote(context_sha256)} ] || {{ echo 'OCI build-context SHA-256 mismatch after SSH transfer' >&2; exit 65; }}
mv -f "$tmp" "$archive"
if [ ! -f "$dst/.context-sha256" ] || [ "$(cat "$dst/.context-sha256" 2>/dev/null || true)" != {shlex.quote(context_sha256)} ]; then
  work="$dst.part.$$"; rm -rf "$work"; mkdir -p "$work"
  tar -xf "$archive" -C "$work"
  printf '%s\n' {shlex.quote(context_sha256)} > "$work/.context-sha256"
  rm -rf "$dst"; mv "$work" "$dst"
fi
trap - EXIT HUP INT TERM
printf 'context_path=%s\ncontext_sha256=%s\ndefinition_digest=%s\n' "$dst" {shlex.quote(context_sha256)} {shlex.quote(definition_digest)}
'''


def _oci_build_script(*, definition_digest: str, context_sha256: str, tag: str, no_cache: bool) -> str:
    no_cache_flag = " --no-cache" if no_cache else ""
    return f'''set -eu
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then
  echo 'Refusing OCI image build inside an existing PRoot environment' >&2; exit 125
fi
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"
[ -n "$pd_bin" ] || {{ echo 'PRoot-Distro is not installed; run devtool oci bootstrap' >&2; exit 127; }}
proot_bin="${{PD_PROOT_BIN:-}}"; if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]; then proot_bin="$(command -v proot 2>/dev/null || true)"; fi
[ -n "$proot_bin" ] || {{ echo 'proot is not installed; run devtool oci bootstrap' >&2; exit 127; }}
help="$($pd_bin build --help 2>&1 || true)"
printf '%s' "$help" | grep -q -- '--architecture' || {{ echo 'Installed PRoot-Distro build command lacks --architecture support' >&2; exit 69; }}
printf '%s' "$help" | grep -q -- '--build-arg' || {{ echo 'Installed PRoot-Distro build command lacks --build-arg support' >&2; exit 69; }}
printf '%s' "$help" | grep -q -- '--output' || {{ echo 'Installed PRoot-Distro build command lacks --output support' >&2; exit 69; }}
ctx="$HOME/.cache/devtool/oci/definitions/sha256-{definition_digest}"
[ -f "$ctx/.context-sha256" ] && [ "$(cat "$ctx/.context-sha256")" = {shlex.quote(context_sha256)} ] || {{ echo 'Staged OCI definition is missing or has the wrong context digest' >&2; exit 66; }}
outdir="$HOME/.cache/devtool/oci/builds"; mkdir -p "$outdir"
out="$outdir/android-arm64-{context_sha256}.oci.tar"; tmp="$out.part.$$"; trap 'rm -f "$tmp"' EXIT HUP INT TERM
"$pd_bin" build --architecture aarch64 --file "$ctx/Dockerfile" --tag {shlex.quote(tag)} --build-arg DEVTOOL_DEFINITION_DIGEST={shlex.quote(definition_digest)} --build-arg DEVTOOL_BUILD_CONTEXT_SHA256={shlex.quote(context_sha256)} --output "$tmp"{no_cache_flag} "$ctx"
[ -s "$tmp" ] || {{ echo 'PRoot-Distro image build did not produce an OCI archive' >&2; exit 74; }}
command -v python3 >/dev/null 2>&1 || {{ echo 'python3 is required to verify OCI manifest digest evidence' >&2; exit 127; }}
image_digest="$(python3 -c 'import hashlib,json,sys,tarfile; p=sys.argv[1]; t=tarfile.open(p,"r:*"); i=json.load(t.extractfile("index.json")); m=i.get("manifests") or []; assert len(m)==1,"expected exactly one OCI manifest"; d=str(m[0].get("digest") or ""); a,h=d.split(":",1); assert a=="sha256" and len(h)==64 and all(c in "0123456789abcdef" for c in h),"invalid OCI manifest digest"; b=t.extractfile("blobs/sha256/"+h).read(); assert hashlib.sha256(b).hexdigest()==h,"OCI manifest blob digest mismatch"; print(d)' "$tmp")"
sha="$(sha256sum "$tmp" | awk '{{print $1}}')"; size="$(wc -c < "$tmp" | tr -d ' ')"; mv -f "$tmp" "$out"; trap - EXIT HUP INT TERM
printf 'image_reference=%s\nimage_digest=%s\nimage_archive=%s\nimage_sha256=%s\nimage_size=%s\n' {shlex.quote(tag)} "$image_digest" "$out" "$sha" "$size"
printf 'DEVTOOL_OCI_ARTIFACT\t%s\t%s\t%s\toci-archive\n' "$out" "$sha" "$size"
'''


def _oci_install_built_script(*, definition_digest: str, context_sha256: str, name: str) -> str:
    return f'''set -eu
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then echo 'Refusing OCI image install inside an existing PRoot environment' >&2; exit 125; fi
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"; [ -n "$pd_bin" ] || {{ echo 'PRoot-Distro is not installed' >&2; exit 127; }}
archive="$HOME/.cache/devtool/oci/builds/android-arm64-{context_sha256}.oci.tar"; [ -s "$archive" ] || {{ echo 'Built OCI archive is missing' >&2; exit 66; }}
"$pd_bin" install --name {shlex.quote(name)} --architecture aarch64 "$archive"
printf 'installed_container=%s\n' {shlex.quote(name)}
'''


def _oci_validate_container_script(container: str, definition_digest: str, context_sha256: str) -> str:
    verify = (
        "import json; p='/usr/local/lib/devtool/oci-build-metadata.json'; d=json.load(open(p)); "
        f"assert d.get('definition_digest') == {definition_digest!r}, 'definition digest mismatch'; "
        f"assert d.get('context_sha256') == {context_sha256!r}, 'context digest mismatch'; "
        "print('DEVTOOL_OCI_METADATA='+json.dumps(d,sort_keys=True,separators=(',',':')))"
    )
    inner = "/usr/local/lib/devtool/validate-android-oci.sh; python3 -c " + shlex.quote(verify)
    return f'''set -eu
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then echo 'Refusing OCI image validation inside an existing PRoot environment' >&2; exit 125; fi
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"; [ -n "$pd_bin" ] || {{ echo 'PRoot-Distro is not installed' >&2; exit 127; }}
"$pd_bin" login {shlex.quote(container)} -- /bin/bash -lc {shlex.quote(inner)}
'''


def _parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def oci_validate_image_payload(
    config: SSHRunnerConfig,
    container: str,
    *,
    run_id: str,
    depends_on: tuple[str, ...] = (),
    discover_graph: bool = True,
) -> dict[str, Any]:
    container = _validate_name(container)
    if not container:
        raise ValueError("OCI image validation requires an installed container name")
    definition = canonical_android_oci_definition()
    node_id = _stable_node_id(config, "validate-android-image", f"{container}|{definition.definition_digest}")
    if discover_graph:
        _discover_nodes(run_id, ((node_id, f"Validate Android OCI image {container}", depends_on),))
    completed = _run_process(
        config,
        run_id=run_id,
        node_id=node_id,
        label=f"Validate Android OCI image {container}",
        argv=build_ssh_remote_sh_argv(config, _oci_validate_container_script(container, definition.definition_digest, definition.context_sha256), tty=False),
        detail="Validating immutable Android OCI toolchain capabilities and build metadata",
        timeout=_oci_timeout(),
        diagnostic_code="OCI_IMAGE_CAPABILITY",
        provenance_extra={"container": container, "image_definition": definition.contract["definition_id"], "definition_digest": definition.definition_digest},
    )
    values = _parse_key_values(completed.stdout)
    metadata_raw = values.get("DEVTOOL_OCI_METADATA", "")
    try:
        import json
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    capability = {key: value for key, value in values.items() if key != "DEVTOOL_OCI_METADATA"}
    return {
        "schema_version": 1,
        "kind": "oci-image-validation",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "run_id": run_id,
        "node_id": node_id,
        "container": container,
        "definition_id": definition.contract["definition_id"],
        "definition_digest": definition.definition_digest,
        "context_sha256": definition.context_sha256,
        "capabilities": capability,
        "build_metadata": metadata,
        "detail": "Android OCI image capability contract ready",
    }


def oci_build_payload(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    tag: str = "",
    install_as: str = "",
    dry_run: bool = False,
    no_cache: bool = False,
) -> dict[str, Any]:
    definition = canonical_android_oci_definition()
    tag = str(tag or definition.reference).strip()
    if not tag or any(ch.isspace() for ch in tag):
        raise ValueError("OCI image tag/reference cannot be empty or contain whitespace")
    install_as = _validate_name(install_as)
    stage_node = _stable_node_id(config, "stage-image-definition", definition.context_sha256)
    build_node = _stable_node_id(config, "build-android-image", f"{definition.definition_digest}|{tag}|{int(no_cache)}")
    nodes: list[tuple[str, str, tuple[str, ...]]] = [
        (stage_node, "Stage canonical Android OCI image definition", ()),
        (build_node, f"Build Android OCI image {tag}", (stage_node,)),
    ]
    install_node = ""
    validate_node = ""
    if install_as:
        install_node = _stable_node_id(config, "install-built-image", f"{definition.context_sha256}|{install_as}")
        validate_node = _stable_node_id(config, "validate-android-image", f"{install_as}|{definition.definition_digest}")
        nodes.extend([
            (install_node, f"Install built Android OCI image as {install_as}", (build_node,)),
            (validate_node, f"Validate Android OCI image {install_as}", (install_node,)),
        ])
    _discover_nodes(run_id, tuple(nodes))
    if dry_run:
        for node_id, _label, _deps in nodes:
            _finish_node(run_id, node_id, status=ExecutionStatus.SKIPPED.value, exit_code=0, reason="dry-run")
        return {
            "schema_version": 1, "kind": "oci-image-build", "ok": True, "dry_run": True,
            "profile": config.profile, "destination": config.destination, "run_id": run_id,
            "node_ids": [node[0] for node in nodes], "definition_id": definition.contract["definition_id"],
            "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256,
            "reference": tag, "architecture": definition.architecture, "install_as": install_as,
            "no_cache": bool(no_cache), "detail": "Deterministic Android OCI image build plan ready",
        }
    try:
        _run_process(
            config, run_id=run_id, node_id=stage_node, label="Stage canonical Android OCI image definition",
            argv=build_ssh_remote_sh_argv(config, _oci_stage_definition_script(definition.definition_digest, definition.context_sha256), tty=False),
            detail="Uploading content-addressed deterministic OCI build context", timeout=_transfer_timeout(), stdin_bytes=definition.context_bytes,
            diagnostic_code="OCI_IMAGE_CONTEXT", provenance_extra={"definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256},
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        _block_node(run_id, build_node, blocked_by=(stage_node,), reason="Image build blocked because canonical context staging failed")
        if install_node:
            _block_node(run_id, install_node, blocked_by=(build_node,), reason="Image install blocked because image build did not run")
            _block_node(run_id, validate_node, blocked_by=(install_node,), reason="Image validation blocked because image install did not run")
        raise
    try:
        completed = _run_process(
            config, run_id=run_id, node_id=build_node, label=f"Build Android OCI image {tag}",
            argv=build_ssh_remote_sh_argv(config, _oci_build_script(definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, tag=tag, no_cache=no_cache), tty=False),
            detail="Building pinned immutable Android ARM64 OCI image through PRoot-Distro", timeout=_oci_timeout(), diagnostic_code="OCI_IMAGE_BUILD",
            provenance_extra={"image_reference": tag, "architecture": definition.architecture, "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256},
            publish_remote_artifacts=True,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        if install_node:
            _block_node(run_id, install_node, blocked_by=(build_node,), reason="Image install blocked because image build failed")
            _block_node(run_id, validate_node, blocked_by=(install_node,), reason="Image validation blocked because image install did not run")
        raise
    values = _parse_key_values(completed.stdout)
    artifact_records = _remote_artifact_records(completed.stdout)
    if not artifact_records:
        # _run_process already fails this case; keep this defensive assertion so
        # payload assembly can never invent missing digest evidence.
        raise RuntimeError("OCI image build succeeded without parsed artifact evidence")
    image_artifact = artifact_records[0]
    validation: dict[str, Any] | None = None
    if install_as:
        try:
            _run_process(
                config, run_id=run_id, node_id=install_node, label=f"Install built Android OCI image as {install_as}",
                argv=build_ssh_remote_sh_argv(config, _oci_install_built_script(definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, name=install_as), tty=False),
                detail="Installing locally built OCI archive through PRoot-Distro", timeout=_oci_timeout(), diagnostic_code="OCI_IMAGE_INSTALL",
                provenance_extra={"container": install_as, "image_reference": tag, "image_sha256": image_artifact["sha256"]},
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            _block_node(run_id, validate_node, blocked_by=(install_node,), reason="Image validation blocked because image install failed")
            raise
        validation = oci_validate_image_payload(config, install_as, run_id=run_id, depends_on=(install_node,), discover_graph=False)
    return {
        "schema_version": 1, "kind": "oci-image-build", "ok": True,
        "profile": config.profile, "destination": config.destination, "run_id": run_id,
        "node_ids": [node[0] for node in nodes], "definition_id": definition.contract["definition_id"],
        "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256,
        "context_size": definition.context_size, "reference": tag, "architecture": definition.architecture,
        "image_archive": image_artifact["path"], "image_sha256": image_artifact["sha256"],
        "image_digest": values.get("image_digest", ""), "image_size": image_artifact["size"], "install_as": install_as,
        "no_cache": bool(no_cache), "validation": validation,
        "detail": "Deterministic Android OCI image built" + (" and capability-validated" if install_as else ""),
    }

# OCI-03 Gate 1 closure -----------------------------------------------------

def _gate1_preflight_script() -> str:
    return r"""set -eu
if [ -n "${PROOT_TMP_DIR:-}" ] || [ -n "${PROOT_LOADER:-}" ]; then
  echo 'OCI-03 Gate 1 refuses a worker already inside PRoot' >&2; exit 125
fi
termux_score=0
if [ -f /system/build.prop ] || [ -d /data/app ]; then termux_score=$((termux_score + 1)); fi
if [ -n "${TERMUX_APP__APP_VERSION_NAME:-}" ] || [ -n "${TERMUX_VERSION:-}" ]; then termux_score=$((termux_score + 1)); fi
termux_prefix="${TERMUX__PREFIX:-${PREFIX:-/data/data/com.termux/files/usr}}"
if [ -r "$termux_prefix" ] && [ -x "$termux_prefix" ]; then termux_score=$((termux_score + 1)); fi
[ "$termux_score" -ge 2 ] || { echo 'OCI-03 Gate 1 requires a fresh Termux SSH worker' >&2; exit 69; }
pd_bin="$(command -v proot-distro 2>/dev/null || command -v pd 2>/dev/null || true)"
proot_bin="${PD_PROOT_BIN:-}"; if [ -z "$proot_bin" ] || [ ! -x "$proot_bin" ]; then proot_bin="$(command -v proot 2>/dev/null || true)"; fi
[ -n "$pd_bin" ] || { echo 'proot-distro is not installed' >&2; exit 127; }
[ -n "$proot_bin" ] || { echo 'proot is not installed' >&2; exit 127; }
arch="$(uname -m 2>/dev/null || true)"
case "$arch" in aarch64|arm64) ;; *) echo "Gate 1 worker architecture is $arch, expected aarch64/arm64" >&2; exit 69;; esac
"$pd_bin" list --quiet >/dev/null
"$pd_bin" list --image --quiet >/dev/null
printf 'platform=termux\narchitecture=%s\nproot_distro=%s\nproot=%s\n' "$arch" "$pd_bin" "$proot_bin"
"""


def _gate1_run_directory(run_id: str) -> Path:
    return state_dir() / "oci-gate1" / str(run_id)


def _gate1_fail_semantic(*, run_id: str, node_id: str, label: str, code: str, message: str, provenance: Mapping[str, Any]) -> None:
    _start_node(run_id, node_id, label, "Verifying Gate 1 semantic evidence")
    adapter = RunnerExecutionAdapter(
        run_id=run_id, parent_node_id=node_id, tool="oci-gate1", source="oci-gate1",
        provenance=dict(redact_sensitive_value(dict(provenance))),
    )
    diagnostic_id = adapter.diagnostic(
        severity="error", code=code, message=_safe_text(message), failure_kind="gate_evidence_mismatch"
    )
    _finish_node(
        run_id, node_id, status=ExecutionStatus.FAILED.value, exit_code=65,
        diagnostic_ids=(diagnostic_id,), reason="Gate 1 evidence contract mismatch",
    )
    raise ValueError(message)


def oci_gate1_payload(
    config: SSHRunnerConfig,
    *,
    run_id: str,
    container: str = "",
    tag: str = "",
) -> dict[str, Any]:
    """Execute OCI-03 external Gate 1 as one durable canonical EXO run."""
    definition = canonical_android_oci_definition()
    container = _validate_name(container or f"devtool-android-api36-gate-{run_id[:12]}")
    if not container:
        raise ValueError("OCI-03 Gate 1 requires a valid installed container name")
    tag = str(tag or definition.reference).strip()
    if not tag or any(ch.isspace() for ch in tag):
        raise ValueError("OCI-03 Gate 1 image reference cannot be empty or contain whitespace")

    preflight = _stable_node_id(config, "gate1-preflight", definition.definition_digest)
    stage = _stable_node_id(config, "gate1-stage-definition", definition.context_sha256)
    build = _stable_node_id(config, "gate1-build-image", f"{definition.definition_digest}|{tag}|no-cache")
    verify_artifact = _stable_node_id(config, "gate1-verify-image-evidence", tag)
    install = _stable_node_id(config, "gate1-install-image", f"{definition.context_sha256}|{container}")
    validate = _stable_node_id(config, "gate1-validate-capabilities", f"{container}|{definition.definition_digest}")
    verify_provenance = _stable_node_id(config, "gate1-verify-provenance", definition.definition_digest)
    seal = _stable_node_id(config, "gate1-seal", f"{definition.definition_digest}|{container}")
    nodes = (
        (preflight, "Gate 1 fresh Termux worker preflight", ()),
        (stage, "Stage canonical Android OCI image definition", (preflight,)),
        (build, f"Build Android OCI image {tag} without cache", (stage,)),
        (verify_artifact, "Verify OCI image digest and archive evidence", (build,)),
        (install, f"Install built OCI image as {container}", (verify_artifact,)),
        (validate, f"Validate Android OCI capabilities in {container}", (install,)),
        (verify_provenance, "Verify Gate 1 image provenance", (validate,)),
        (seal, "Seal OCI-03 Validation Gate 1 evidence", (verify_provenance,)),
    )

    run_dir = _gate1_run_directory(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    bus = get_event_bus()
    projection = ExecutionProjectionSubscriber(
        store=bus.execution_store, run_id=run_id, directory=run_dir,
        history_directory=state_dir() / "history", checkpoint_interval=1,
    )
    bus.subscribe_all(projection)
    started = time.monotonic()
    provenance = _provenance(config, operation="OCI-03 Validation Gate 1", extra={
        "container": container, "image_reference": tag,
        "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256,
        "validation_gate": "oci03-gate1",
    })
    node_ids = [item[0] for item in nodes]
    node_dependencies = {node_id: tuple(deps) for node_id, _label, deps in nodes}
    try:
        publish_event(RunStarted(
            run_id=run_id, command="oci gate1", project="devtool", source="oci-gate1",
            metadata={"provenance": provenance, "validation_gate": "oci03-gate1"},
        ))
        _discover_nodes(run_id, nodes)

        _run_process(
            config, run_id=run_id, node_id=preflight, label="Gate 1 fresh Termux worker preflight",
            argv=build_ssh_remote_sh_argv(config, _gate1_preflight_script(), tty=False),
            detail="Checking fresh Termux ARM64 worker and PRoot-Distro inventories",
            timeout=max(_transport_timeout(), 20.0), diagnostic_code="OCI_GATE1_PREFLIGHT",
            provenance_extra={"validation_gate": "oci03-gate1"},
        )
        _run_process(
            config, run_id=run_id, node_id=stage, label="Stage canonical Android OCI image definition",
            argv=build_ssh_remote_sh_argv(config, _oci_stage_definition_script(definition.definition_digest, definition.context_sha256), tty=False),
            detail="Uploading content-addressed deterministic OCI build context", timeout=_transfer_timeout(),
            stdin_bytes=definition.context_bytes, diagnostic_code="OCI_GATE1_CONTEXT",
            provenance_extra={"definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256, "validation_gate": "oci03-gate1"},
        )
        completed = _run_process(
            config, run_id=run_id, node_id=build, label=f"Build Android OCI image {tag} without cache",
            argv=build_ssh_remote_sh_argv(config, _oci_build_script(
                definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, tag=tag, no_cache=True,
            ), tty=False),
            detail="Building pinned Android ARM64 image with PRoot-Distro cache disabled", timeout=_oci_timeout(),
            diagnostic_code="OCI_GATE1_BUILD",
            provenance_extra={"image_reference": tag, "architecture": definition.architecture, "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256, "validation_gate": "oci03-gate1"},
            publish_remote_artifacts=True,
        )
        values = _parse_key_values(completed.stdout)
        artifacts = _remote_artifact_records(completed.stdout)
        image_digest = values.get("image_digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) or len(artifacts) != 1:
            _gate1_fail_semantic(
                run_id=run_id, node_id=verify_artifact, label="Verify OCI image digest and archive evidence", code="OCI_GATE1_IMAGE_EVIDENCE",
                message="Gate 1 build did not produce exactly one verified OCI archive plus OCI manifest digest",
                provenance=provenance,
            )
        _start_node(run_id, verify_artifact, "Verify OCI image digest and archive evidence", "Checking OCI manifest/archive identities")
        _finish_node(run_id, verify_artifact, status=ExecutionStatus.SUCCEEDED.value, exit_code=0)
        image_artifact = artifacts[0]

        _run_process(
            config, run_id=run_id, node_id=install, label=f"Install built OCI image as {container}",
            argv=build_ssh_remote_sh_argv(config, _oci_install_built_script(
                definition_digest=definition.definition_digest, context_sha256=definition.context_sha256, name=container,
            ), tty=False),
            detail="Installing the exact freshly built OCI archive through PRoot-Distro", timeout=_oci_timeout(),
            diagnostic_code="OCI_GATE1_INSTALL",
            provenance_extra={"container": container, "image_reference": tag, "image_digest": image_digest, "image_sha256": image_artifact["sha256"], "validation_gate": "oci03-gate1"},
        )
        validated = _run_process(
            config, run_id=run_id, node_id=validate, label=f"Validate Android OCI capabilities in {container}",
            argv=build_ssh_remote_sh_argv(config, _oci_validate_container_script(container, definition.definition_digest, definition.context_sha256), tty=False),
            detail="Running immutable Android toolchain and real NDK compile/link probes", timeout=_oci_timeout(),
            diagnostic_code="OCI_GATE1_CAPABILITY",
            provenance_extra={"container": container, "image_reference": tag, "image_digest": image_digest, "definition_digest": definition.definition_digest, "validation_gate": "oci03-gate1"},
        )
        parsed = _parse_key_values(validated.stdout)
        metadata_raw = parsed.get("DEVTOOL_OCI_METADATA", "")
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except (TypeError, ValueError):
            metadata = {}
        capabilities = {k: v for k, v in parsed.items() if k != "DEVTOOL_OCI_METADATA"}
        expected = {"status": "ready", "api": "36", "build_tools": "36.0.0", "ndk": "29.0.14206865", "ndk_host": "qemu-x86_64-wrapped-verified"}
        errors = [f"{k}={capabilities.get(k)!r}, expected {want!r}" for k, want in expected.items() if str(capabilities.get(k) or "") != want]
        arch = str(capabilities.get("architecture") or "")
        if arch not in {"aarch64", "arm64"}:
            errors.append(f"architecture={arch!r}, expected aarch64/arm64")
        if not isinstance(metadata, dict) or metadata.get("definition_digest") != definition.definition_digest:
            errors.append("definition_digest provenance mismatch")
        if not isinstance(metadata, dict) or metadata.get("context_sha256") != definition.context_sha256:
            errors.append("context_sha256 provenance mismatch")
        wrapped = ((metadata.get("toolchains") or {}).get("ndk_wrapped_host_binaries") if isinstance(metadata, dict) else 0)
        try:
            wrapped_count = int(wrapped or 0)
        except (TypeError, ValueError):
            wrapped_count = 0
        if wrapped_count <= 0:
            errors.append("transparent NDK host wrapper inventory is empty")
        if errors:
            _gate1_fail_semantic(
                run_id=run_id, node_id=verify_provenance, label="Verify Gate 1 image provenance", code="OCI_GATE1_PROVENANCE",
                message="Gate 1 provenance/capability mismatch: " + "; ".join(errors), provenance=provenance,
            )
        _start_node(run_id, verify_provenance, "Verify Gate 1 image provenance", "Checking definition/context/toolchain identities")
        _finish_node(run_id, verify_provenance, status=ExecutionStatus.SUCCEEDED.value, exit_code=0)

        _start_node(run_id, seal, "Seal OCI-03 Validation Gate 1 evidence", "Writing bounded machine-readable Gate 1 evidence artifact")
        evidence = dict(redact_sensitive_value({
            "schema_version": 1, "kind": "devtool.oci03.gate1.evidence", "status": "passed", "run_id": run_id,
            "profile": config.profile, "destination": config.destination, "provider": "ssh-proot-oci", "container": container,
            "image_reference": tag, "image_digest": image_digest, "archive_sha256": image_artifact["sha256"],
            "archive_size": image_artifact["size"], "definition_digest": definition.definition_digest,
            "context_sha256": definition.context_sha256, "capabilities": capabilities, "build_metadata": metadata,
        }))
        evidence_path = run_dir / "gate1-evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        adapter = RunnerExecutionAdapter(
            run_id=run_id, parent_node_id=seal, tool="oci-gate1", source="oci-gate1", provenance=provenance,
        )
        artifact_id = adapter.artifact(path=str(evidence_path), producer_node_id=seal, producer_task="oci03-gate1-seal", verification_status="verified")
        _finish_node(run_id, seal, status=ExecutionStatus.SUCCEEDED.value, exit_code=0)
        duration = time.monotonic() - started
        publish_event(RunFinished(run_id=run_id, status=ExecutionStatus.SUCCEEDED.value, exit_code=0, duration_seconds=duration, source="oci-gate1"))
        return {
            "schema_version": 1, "kind": "oci03-gate1", "ok": True, "status": "passed", "run_id": run_id,
            "profile": config.profile, "destination": config.destination, "provider": "ssh-proot-oci", "container": container,
            "reference": tag, "image_digest": image_digest, "archive_sha256": image_artifact["sha256"], "archive_size": image_artifact["size"],
            "definition_digest": definition.definition_digest, "context_sha256": definition.context_sha256,
            "capabilities": capabilities, "execution_dir": str(run_dir), "execution_json": str(run_dir / "execution.json"),
            "journal": str(run_dir / "execution-events.jsonl"), "checkpoint": str(run_dir / "execution-checkpoint.json"),
            "evidence_artifact": str(evidence_path), "evidence_artifact_id": artifact_id, "node_ids": node_ids,
            "detail": "OCI-03 Validation Gate 1 passed with canonical EXO evidence",
        }
    except Exception:
        snap = bus.execution_store.snapshot(run_id)
        failed = ""
        if snap is not None:
            failed_nodes = [node for node in snap.nodes if node.status == ExecutionStatus.FAILED.value]
            failed = failed_nodes[-1].node_id if failed_nodes else ""
            status_by_id = {node.node_id: node.status for node in snap.nodes}
            for node_id in node_ids:
                if status_by_id.get(node_id) in {ExecutionStatus.PLANNED.value, ExecutionStatus.PENDING.value, ExecutionStatus.READY.value}:
                    dependencies = node_dependencies.get(node_id, ())
                    blocked_by = dependencies or ((failed,) if failed else ())
                    _block_node(
                        run_id, node_id, blocked_by=blocked_by,
                        reason=(f"Gate 1 dependency {blocked_by[0]} did not succeed" if blocked_by else "Gate 1 stopped after an earlier failure"),
                    )
        try:
            publish_event(RunFinished(
                run_id=run_id, status=ExecutionStatus.FAILED.value, exit_code=1, duration_seconds=time.monotonic() - started,
                source="oci-gate1", metadata={"failed_node_id": failed},
            ))
        except Exception:
            pass
        raise
    finally:
        bus.unsubscribe_all(projection)
        projection.close()
