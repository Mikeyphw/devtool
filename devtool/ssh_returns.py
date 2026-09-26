from __future__ import annotations

"""Remote SSH build output/evidence return and recovery support.

SSHW-04 keeps the repository authoritative on the phone while treating generated
remote outputs as evidence that must come home before local report/artifact
processing.  Every command-mode phase gets an immutable local return bundle with
an on-disk manifest.  Transfer is rsync-based, resumable, filtered to generated
reports/distributables, and can be retried explicitly after an interrupted SSH
connection without re-running the build.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any, Iterable, Mapping

from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_base_argv
from .oci_resilience import attach_producer_identity, producer_identity, validate_producer_identity

_SCHEMA_VERSION = 1
_DEFAULT_PATTERNS = (
    "**/build/test-results/***",
    "**/build/reports/**/*.xml",
    "**/build/reports/**/*.json",
    "**/build/outputs/***",
    "**/build/libs/*.jar",
    "**/build/libs/*.aar",
    "**/target/surefire-reports/***",
    "**/target/failsafe-reports/***",
    "**/target/*.jar",
    "**/target/*.war",
    "**/target/*.ear",
    "**/TestResults/***",
    "**/test-results/***",
    "**/coverage.xml",
    "**/coverage.json",
    ".devtool/ssh-runtime-logs/***",
    ".devtool-federation-attestation.json",
)


@dataclass(frozen=True, slots=True)
class SSHReturnBundle:
    run_id: str
    session: str
    root: Path
    repo_root: Path
    project_root: Path
    manifest_path: Path
    status_path: Path
    remote_root: str
    patterns: tuple[str, ...]
    source_sync_id: str
    producer_sha256: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "ssh_return_run_id": self.run_id,
            "ssh_return_session": self.session,
            "ssh_return_root": str(self.root),
            "ssh_return_repo_root": str(self.repo_root),
            "ssh_return_project_root": str(self.project_root),
            "ssh_return_manifest": str(self.manifest_path),
            "ssh_return_status_file": str(self.status_path),
            "ssh_return_patterns": list(self.patterns),
            "ssh_return_remote_root": self.remote_root,
            "ssh_source_sync_id": self.source_sync_id,
            "ssh_return_producer_sha256": self.producer_sha256,
        }


def _safe(value: str, fallback: str = "run") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return text[:160] or fallback


def _iter_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        for item in value.split(","):
            item = item.strip()
            if item:
                yield item
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            text = str(item or "").strip()
            if text:
                yield text


def _relative_pattern(value: str, *, local_root: Path, project_root: Path, directory: bool = False) -> str:
    text = str(value or "").strip()
    if not text or text.startswith(("~/", "$HOME/")):
        return ""
    # Preserve glob spelling while resolving the non-glob prefix relative to the
    # declared project root. Absolute local paths are accepted only under repo.
    has_glob = any(ch in text for ch in "*?[")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = project_root / path
    try:
        rel = path.relative_to(local_root) if has_glob else path.resolve().relative_to(local_root)
    except (OSError, ValueError):
        return ""
    pattern = rel.as_posix()
    if directory and not any(ch in pattern for ch in "*?["):
        pattern = pattern.rstrip("/") + "/***"
    return pattern


def return_patterns(project_config: Any, spec_metadata: dict[str, Any], *, local_root: Path, project_root: Path) -> tuple[str, ...]:
    patterns: list[str] = list(_DEFAULT_PATTERNS)
    for key in ("test_report", "coverage_report", "package_report"):
        pattern = _relative_pattern(str(spec_metadata.get(key) or ""), local_root=local_root, project_root=project_root)
        if pattern:
            patterns.append(pattern)

    options = getattr(project_config, "runner_options", {}) or {}
    artifacts = options.get("artifacts", {}) if isinstance(options, dict) else {}
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    for key in ("paths", "artifact_paths"):
        for value in _iter_values(artifacts.get(key)):
            pattern = _relative_pattern(value, local_root=local_root, project_root=project_root)
            if pattern:
                patterns.append(pattern)
    for key in ("directories", "artifact_directories"):
        for value in _iter_values(artifacts.get(key)):
            pattern = _relative_pattern(value, local_root=local_root, project_root=project_root, directory=True)
            if pattern:
                patterns.append(pattern)

    seen: set[str] = set()
    result: list[str] = []
    for item in patterns:
        clean = str(item or "").strip().lstrip("/")
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return tuple(result)


def create_return_bundle(
    config: SSHRunnerConfig,
    *,
    local_root: Path,
    host_cwd: str,
    remote_root: str,
    session: str,
    project_config: Any,
    spec_metadata: dict[str, Any],
    source_sync_id: str = "",
    provider_metadata: Mapping[str, Any] | None = None,
) -> SSHReturnBundle:
    run_id = _safe(str(os.environ.get("DEVTOOL_RUN_ID") or config.operation_id or "run"))
    session_id = _safe(session, "phase")
    root = local_root / ".devtool" / "ssh-return" / run_id / session_id
    repo_mirror = root / "repo"
    try:
        host_project = Path(host_cwd).expanduser().resolve()
        relative_project = host_project.relative_to(local_root)
    except (OSError, ValueError):
        relative_project = Path(".")
    project_mirror = repo_mirror / relative_project
    root.mkdir(parents=True, exist_ok=True)
    patterns = return_patterns(project_config, spec_metadata, local_root=local_root, project_root=Path(host_cwd).expanduser().resolve())
    manifest = root / "manifest.json"
    status = root / "transfer.status"
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "ssh-return-bundle",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "session": session,
        "profile": config.profile,
        "destination": config.destination,
        "port": config.port,
        "remote_root": remote_root,
        "local_repo_root": str(local_root),
        "local_return_root": str(root),
        "local_return_repo_root": str(repo_mirror),
        "local_return_project_root": str(project_mirror),
        "patterns": list(patterns),
        "source_sync_id": source_sync_id,
        "state": "pending",
    }
    producer_meta = dict(provider_metadata or {})
    producer_meta.update({key: value for key, value in spec_metadata.items() if key not in producer_meta})
    if producer_meta.get("federation_child_repository"):
        producer_meta["federation_artifact_return_root"] = str(root)
    producer = producer_identity(
        profile=config.profile,
        destination=config.destination,
        run_id=run_id,
        session=session_id,
        source_sync_id=source_sync_id,
        metadata=producer_meta,
    )
    attach_producer_identity(payload, producer)
    tmp = manifest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, manifest)
    return SSHReturnBundle(run_id, session, root, repo_mirror, project_mirror, manifest, status, remote_root, patterns, source_sync_id, str(payload.get("producer_sha256") or ""))


def _local_rsync() -> str:
    candidate = str(os.environ.get("DEVTOOL_RSYNC_COMMAND", "rsync") or "rsync").strip()
    resolved = shutil.which(candidate) if os.sep not in candidate else candidate
    if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"Local rsync executable is unavailable or not executable: {candidate}")
    return str(resolved)


def _ssh_shell(config: SSHRunnerConfig) -> str:
    argv = build_ssh_base_argv(config, tty=False)
    return shlex.join(argv[:-1])


def build_return_rsync_argv(
    config: SSHRunnerConfig,
    *,
    remote_root: str,
    local_repo_root: Path,
    patterns: Iterable[str],
    remote_rsync: str = "rsync",
) -> list[str]:
    argv = [
        _local_rsync(), "-a", "--no-owner", "--no-group", "--no-devices", "--no-specials",
        "--protect-args", "--human-readable", "--partial", "--delay-updates", "--prune-empty-dirs",
        "--itemize-changes", "--out-format=%i|%n%L", "-e", _ssh_shell(config),
        f"--rsync-path={remote_rsync}", "--include=*/",
    ]
    for pattern in patterns:
        argv.append(f"--include={pattern}")
    argv.extend(["--exclude=*", f"{config.destination}:{remote_root.rstrip('/')}/", str(local_repo_root) + "/"])
    return argv


def _return_archive_thresholds() -> tuple[int, int]:
    try:
        min_bytes = max(0, int(os.environ.get("DEVTOOL_SSH_RETURN_ARCHIVE_MIN_BYTES", str(8 * 1024 * 1024)) or 0))
    except ValueError:
        min_bytes = 8 * 1024 * 1024
    try:
        min_files = max(0, int(os.environ.get("DEVTOOL_SSH_RETURN_ARCHIVE_MIN_FILES", "256") or 0))
    except ValueError:
        min_files = 256
    return min_bytes, min_files


def _return_selection_python(bundle: SSHReturnBundle, *, archive: bool, remote_zstd: str = "") -> str:
    root = str(bundle.remote_root)
    patterns = list(bundle.patterns)
    common = (
        "import pathlib,json,os,sys;"
        f"root=pathlib.Path({root!r}).resolve();patterns=json.loads({json.dumps(patterns)!r});"
        "seen=set();files=[];"
        "norm=lambda p:p.replace('***','**/*');"
        "\nfor pat in patterns:\n"
        "  for p in root.glob(norm(pat)):\n"
        "    try:\n      rp=p.resolve();rp.relative_to(root)\n    except (OSError,ValueError):\n      continue\n"
        "    if p.is_file() and rp not in seen:\n      seen.add(rp);files.append(p)\n"
    )
    if not archive:
        return common + "\ntotal=sum((p.stat().st_size if p.exists() else 0) for p in files);print(str(total)+' '+str(len(files)))"
    return common + (
        "\nimport subprocess,tarfile;"
        f"proc=subprocess.Popen([{remote_zstd!r},'-1','-T0','-q','-c'],stdin=subprocess.PIPE);"
        "tf=tarfile.open(fileobj=proc.stdin,mode='w|');"
        "\nfor p in sorted(files,key=lambda x:x.as_posix()):\n  tf.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)\n"
        "tf.close();proc.stdin.close();rc=proc.wait();raise SystemExit(rc)"
    )


def _remote_python_command(config: SSHRunnerConfig, script: str, *, remote_python: str) -> str:
    from .ssh_tmux_runner import build_ssh_remote_sh_argv
    remote = f"{shlex.quote(remote_python)} -c {shlex.quote(script)}"
    return shlex.join(build_ssh_remote_sh_argv(config, remote, tty=False))


def shell_return_fragment(
    config: SSHRunnerConfig,
    bundle: SSHReturnBundle,
    *,
    remote_rsync: str,
    remote_zstd: str = "",
    remote_python: str = "",
) -> str:
    """Return a local-shell fragment with adaptive compressed evidence return.

    Tiny bundles and workers without the required streaming tools retain the
    established resumable rsync path. Large bundles are selected remotely,
    streamed as zstd-compressed tar data, extracted only after the SSH transfer
    succeeds, and never marked complete on an interrupted transfer/extraction.
    """
    rsync = build_return_rsync_argv(
        config,
        remote_root=bundle.remote_root,
        local_repo_root=bundle.repo_root,
        patterns=bundle.patterns,
        remote_rsync=remote_rsync or "rsync",
    )
    status = shlex.quote(str(bundle.status_path))
    repo = shlex.quote(str(bundle.repo_root))
    status_tmp = shlex.quote(str(bundle.status_path) + ".tmp")
    min_bytes, min_files = _return_archive_thresholds()
    adaptive = bool(remote_zstd and remote_python)
    if adaptive:
        stats_cmd = _remote_python_command(
            config, _return_selection_python(bundle, archive=False), remote_python=remote_python,
        )
        archive_cmd = _remote_python_command(
            config, _return_selection_python(bundle, archive=True, remote_zstd=remote_zstd), remote_python=remote_python,
        )
        zst = shlex.quote(str(bundle.root / ".return.tar.zst"))
        tarfile = shlex.quote(str(bundle.root / ".return.tar"))
        archive_branch = (
            f"return_stats=$({stats_cmd} 2>/dev/null) || return_stats='0 0'; "
            "set -- $return_stats; return_bytes=${1:-0}; return_files=${2:-0}; "
            "return_transferred_bytes=0; "
            f"if command -v zstd >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 && "
            f"{{ [ \"$return_bytes\" -ge {min_bytes} ] || [ \"$return_files\" -ge {min_files} ]; }}; then "
            f"{archive_cmd} > {zst}; return_rc=$?; return_mode=archive-zstd; "
            f"[ -f {zst} ] && return_transferred_bytes=$(wc -c < {zst} 2>/dev/null || printf 0); "
            "if [ \"$return_rc\" -eq 0 ]; then "
            f"zstd -d -q -c {zst} > {tarfile}; return_rc=$?; "
            f"if [ \"$return_rc\" -eq 0 ]; then tar -C {repo} -xf {tarfile}; return_rc=$?; fi; "
            "fi; "
            f"rm -f -- {zst} {tarfile}; "
            "else "
            f"{shlex.join(rsync)}; return_rc=$?; return_mode=rsync-delta; "
            "[ \"$return_rc\" -eq 0 ] && return_transferred_bytes=$return_bytes; "
            "fi; "
        )
    else:
        archive_branch = f"return_bytes=0; return_files=0; return_transferred_bytes=0; {shlex.join(rsync)}; return_rc=$?; return_mode=rsync-delta; "
    return (
        "; return_rc=0; return_mode=deferred; return_bytes=0; return_files=0; "
        "if [ \"$rc\" -eq 125 ]; then "
        f"printf 'returncode=125\\nstate=deferred\\nmode=deferred\\n' > {status_tmp}; mv -f -- {status_tmp} {status}; "
        "else "
        f"rm -rf -- {repo}; mkdir -p {repo}; " + archive_branch +
        f"printf 'returncode=%s\\nstate=%s\\nmode=%s\\nsource_bytes=%s\\nsource_files=%s\\ntransferred_bytes=%s\\n' \"$return_rc\" \"$([ \"$return_rc\" -eq 0 ] && printf complete || printf partial)\" \"$return_mode\" \"$return_bytes\" \"$return_files\" \"$return_transferred_bytes\" > {status_tmp}; "
        f"mv -f -- {status_tmp} {status}; "
        "fi"
    )

def _artifact_lifecycle_records(
    *, manifest: Mapping[str, Any], repo_root: Path, state: str, returncode: int
) -> list[dict[str, Any]]:
    """Project per-artifact transfer truth from the manifest-owned local mirror.

    The return transport can be interrupted mid-bundle, so records describe only
    bytes proven present locally. Expected selectors remain explicit records when
    no concrete artifact has arrived yet.
    """
    records: list[dict[str, Any]] = []
    concrete: list[Path] = []
    if repo_root.is_dir():
        concrete = sorted(path for path in repo_root.rglob("*") if path.is_file() and not path.is_symlink())
    for path in concrete:
        try:
            rel = path.relative_to(repo_root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except (OSError, ValueError):
            continue
        complete = state == "complete" and returncode == 0
        records.append({
            "artifact": rel,
            "selector": "",
            "expected": True,
            "produced": True,
            "staged": True,
            "transferred": True,
            "verified": True,
            "restored_locally": True,
            "failed": False,
            "recoverable": not complete,
            "sha256": digest,
            "size_bytes": int(size),
        })
    if not records:
        for pattern in manifest.get("patterns", []) if isinstance(manifest.get("patterns"), list) else []:
            selector = str(pattern or "").strip()
            if not selector:
                continue
            records.append({
                "artifact": "",
                "selector": selector,
                "expected": True,
                "produced": False,
                "staged": False,
                "transferred": False,
                "verified": False,
                "restored_locally": False,
                "failed": state == "partial" and returncode not in {0, -1},
                "recoverable": state in {"partial", "pending", "deferred"},
                "sha256": "",
                "size_bytes": 0,
            })
    return records


def load_return_state(metadata: dict[str, Any]) -> dict[str, Any]:
    status_path = Path(str(metadata.get("ssh_return_status_file") or "")).expanduser()
    manifest_path = Path(str(metadata.get("ssh_return_manifest") or "")).expanduser()
    values: dict[str, str] = {}
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    try:
        returncode = int(values.get("returncode", -1))
    except ValueError:
        returncode = -1
    state = values.get("state") or ("pending" if returncode < 0 else "complete" if returncode == 0 else "partial")
    source_bytes = int(values.get("source_bytes", "0") or 0) if str(values.get("source_bytes", "0")).isdigit() else 0
    source_files = int(values.get("source_files", "0") or 0) if str(values.get("source_files", "0")).isdigit() else 0
    transferred_bytes = int(values.get("transferred_bytes", "0") or 0) if str(values.get("transferred_bytes", "0")).isdigit() else 0
    ratio = (float(transferred_bytes) / float(source_bytes)) if source_bytes > 0 and transferred_bytes >= 0 else None
    repo_root = Path(str(metadata.get("ssh_return_repo_root") or manifest.get("local_return_repo_root") or "")).expanduser()
    artifacts = _artifact_lifecycle_records(manifest=manifest, repo_root=repo_root, state=state, returncode=returncode)
    return {
        "state": state,
        "returncode": returncode,
        "recoverable": state in {"partial", "pending", "deferred"},
        "execution_failed": False,
        "return_failed": state == "partial" and returncode not in {0, -1},
        "artifacts": artifacts,
        "artifact_summary": {
            "expected": sum(1 for item in artifacts if item["expected"]),
            "produced": sum(1 for item in artifacts if item["produced"]),
            "transferred": sum(1 for item in artifacts if item["transferred"]),
            "verified": sum(1 for item in artifacts if item["verified"]),
            "restored_locally": sum(1 for item in artifacts if item["restored_locally"]),
            "failed": sum(1 for item in artifacts if item["failed"]),
            "recoverable": sum(1 for item in artifacts if item["recoverable"]),
        },
        "manifest": str(manifest_path) if manifest_path else "",
        "local_root": str(metadata.get("ssh_return_root") or manifest.get("local_return_root") or ""),
        "repo_root": str(metadata.get("ssh_return_repo_root") or manifest.get("local_return_repo_root") or ""),
        "project_root": str(metadata.get("ssh_return_project_root") or manifest.get("local_return_project_root") or ""),
        "remote_root": str(metadata.get("ssh_return_remote_root") or manifest.get("remote_root") or ""),
        "source_sync_id": str(metadata.get("ssh_source_sync_id") or manifest.get("source_sync_id") or ""),
        "producer": dict(manifest.get("producer") or {}) if isinstance(manifest.get("producer"), Mapping) else {},
        "producer_sha256": str(manifest.get("producer_sha256") or ""),
        "transport_mode": values.get("mode", ""),
        "source_bytes": source_bytes,
        "source_files": source_files,
        "transferred_bytes": transferred_bytes,
        "compression_ratio": ratio if values.get("mode", "") == "archive-zstd" else None,
    }


def load_return_manifest_state(path: str | Path) -> dict[str, Any]:
    """Load SSH-02 per-artifact return truth from its immutable manifest.

    FRX-Y08 consumes this projection but does not own or duplicate SSH return
    state. The adjacent transfer.status remains the physical-transfer authority.
    """
    manifest_path = Path(path).expanduser().resolve(strict=False)
    manifest = _read_manifest(manifest_path)
    metadata = {
        "ssh_return_manifest": str(manifest_path),
        "ssh_return_status_file": str(manifest_path.parent / "transfer.status"),
        "ssh_return_repo_root": str(manifest.get("local_return_repo_root") or ""),
        "ssh_return_root": str(manifest.get("local_return_root") or ""),
        "ssh_return_project_root": str(manifest.get("local_return_project_root") or ""),
        "ssh_return_remote_root": str(manifest.get("remote_root") or ""),
        "ssh_source_sync_id": str(manifest.get("source_sync_id") or ""),
    }
    return load_return_state(metadata)


def publish_return_transfer_event(metadata: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    """Publish authoritative post-return transport truth into EXO's event stream."""
    from .ssh_transport import SSHTransferTelemetry

    source_bytes = max(0, int(state.get("source_bytes") or 0))
    source_files = max(0, int(state.get("source_files") or 0))
    transferred_bytes = max(0, int(state.get("transferred_bytes") or 0))
    complete = str(state.get("state") or "") == "complete"
    telemetry = SSHTransferTelemetry(
        mode=str(state.get("transport_mode") or "return"),
        state=str(state.get("state") or "pending"),
        source_bytes=source_bytes,
        transferred_bytes=transferred_bytes,
        source_files=source_files,
        transferred_files=source_files if complete else 0,
        remaining_files=0 if complete else source_files,
        compression_ratio=(float(state["compression_ratio"]) if state.get("compression_ratio") is not None else None),
        workspace_sync_id=str(state.get("source_sync_id") or ""),
        detail="SSH return bundle completed" if complete else "SSH return bundle is incomplete/recoverable",
    )
    telemetry.publish(
        run_id=str(metadata.get("ssh_return_run_id") or os.environ.get("DEVTOOL_RUN_ID") or ""),
        node_id=str(metadata.get("ssh_return_session") or "ssh-return"),
    )


def map_returned_path(value: str, *, local_repo_root: Path, returned_repo_root: Path, project_root: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = project_root / path
    try:
        relative = path.resolve().relative_to(local_repo_root.resolve())
    except (OSError, ValueError):
        return text
    return str((returned_repo_root / relative).resolve())


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read SSH return manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("kind") != "ssh-return-bundle":
        raise ValueError(f"Invalid SSH return manifest: {path}")
    validate_producer_identity(value)
    return value


def _manifest_candidates(repo_root: Path, *, run_id: str = "", session: str = "") -> list[Path]:
    base = repo_root / ".devtool" / "ssh-return"
    if run_id:
        base = base / _safe(run_id)
    manifests = list(base.glob("*/manifest.json")) if run_id else list(base.glob("*/*/manifest.json"))
    if session:
        wanted = _safe(session, "phase")
        manifests = [path for path in manifests if path.parent.name == wanted or str(_read_manifest(path).get("session") or "") == session]
    return sorted(manifests, key=lambda p: p.stat().st_mtime, reverse=True)


def recover_return_bundles(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    run_id: str = "",
    session: str = "",
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    manifests = _manifest_candidates(repo, run_id=run_id, session=session)
    if not manifests:
        raise ValueError("No SSH return manifests matched the requested run/session")

    allowed_local = (repo / ".devtool" / "ssh-return").resolve()
    prepared: list[tuple[Path, dict[str, Any], str, Path, tuple[str, ...]]] = []
    for manifest_path in manifests:
        manifest = _read_manifest(manifest_path)
        if str(manifest.get("destination") or "") != config.destination:
            raise ValueError(
                f"SSH return manifest destination {manifest.get('destination')!r} does not match profile destination {config.destination!r}"
            )
        remote_root = str(manifest.get("remote_root") or "")
        if not remote_root.startswith("/"):
            raise ValueError(f"SSH return manifest has unsafe remote root: {remote_root!r}")
        resolved_manifest = manifest_path.resolve()
        try:
            resolved_manifest.relative_to(allowed_local)
        except ValueError as exc:
            raise ValueError(f"SSH return manifest escapes the repository return root: {resolved_manifest}") from exc
        local_repo = Path(str(manifest.get("local_return_repo_root") or "")).expanduser().resolve()
        expected_local_repo = (resolved_manifest.parent / "repo").resolve()
        if local_repo != expected_local_repo:
            raise ValueError(
                f"SSH return manifest local mirror does not match its owned bundle directory: {local_repo}"
            )
        try:
            local_repo.relative_to(allowed_local)
        except ValueError as exc:
            raise ValueError(f"SSH return manifest local path escapes the repository return root: {local_repo}") from exc
        patterns = tuple(str(item) for item in manifest.get("patterns", []) if str(item).strip())
        prepared.append((resolved_manifest, manifest, remote_root, local_repo, patterns))

    # Only after all local manifests pass their trust checks do we touch the
    # network. This keeps a tampered/mismatched recovery request fail-closed
    # without contacting an unrelated host.
    from .ssh_worker import probe_worker
    from .ssh_workspace import resolve_workspace_spec
    probe = probe_worker(config)
    boundary = resolve_workspace_spec(config, repo, operation_id="recovery", remote_home=probe.home)
    remote_rsync = str(probe.tools.get("rsync") or "")
    if not remote_rsync:
        raise ValueError("SSH return recovery requires rsync on the remote worker")

    results: list[dict[str, Any]] = []
    for manifest_path, manifest, remote_root, local_repo, patterns in prepared:
        if boundary.mode == "existing":
            if remote_root.rstrip("/") != str(boundary.remote_path).rstrip("/"):
                raise ValueError(f"SSH return manifest remote root is outside the configured existing workspace: {remote_root}")
        elif boundary.mode == "managed":
            if remote_root.rstrip("/") != str(boundary.remote_path).rstrip("/"):
                raise ValueError(f"SSH return manifest remote root is outside the configured managed workspace: {remote_root}")
        else:
            prefix = str(boundary.workspace_root).rstrip("/") + "/.ephemeral/" + boundary.workspace_id + "/"
            if not remote_root.startswith(prefix):
                raise ValueError(f"SSH return manifest remote root is outside the configured ephemeral workspace boundary: {remote_root}")
        # Recovery produces an exact bundle snapshot. The path was proven above
        # to be the manifest-owned ``repo`` directory under .devtool/ssh-return.
        if local_repo.exists():
            if local_repo.is_symlink() or not local_repo.is_dir():
                raise ValueError(f"SSH return local mirror is not a safe directory: {local_repo}")
            shutil.rmtree(local_repo)
        local_repo.mkdir(parents=True, exist_ok=True)
        argv = build_return_rsync_argv(
            config, remote_root=remote_root, local_repo_root=local_repo, patterns=patterns, remote_rsync=remote_rsync,
        )
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=None)
        status_path = manifest_path.parent / "transfer.status"
        state = "complete" if completed.returncode == 0 else "partial"
        tmp = status_path.with_suffix(".status.tmp")
        tmp.write_text(f"returncode={completed.returncode}\nstate={state}\n", encoding="utf-8")
        os.replace(tmp, status_path)
        lifecycle = _artifact_lifecycle_records(manifest=manifest, repo_root=local_repo, state=state, returncode=int(completed.returncode))
        results.append({
            "manifest": str(manifest_path),
            "session": str(manifest.get("session") or ""),
            "run_id": str(manifest.get("run_id") or ""),
            "returncode": int(completed.returncode),
            "state": state,
            "execution_failed": False,
            "return_failed": int(completed.returncode) != 0,
            "recoverable": int(completed.returncode) != 0,
            "artifacts": lifecycle,
            "artifact_summary": {
                "expected": sum(1 for item in lifecycle if item["expected"]),
                "produced": sum(1 for item in lifecycle if item["produced"]),
                "transferred": sum(1 for item in lifecycle if item["transferred"]),
                "verified": sum(1 for item in lifecycle if item["verified"]),
                "restored_locally": sum(1 for item in lifecycle if item["restored_locally"]),
                "failed": sum(1 for item in lifecycle if item["failed"]),
                "recoverable": sum(1 for item in lifecycle if item["recoverable"]),
            },
            "detail": (completed.stderr or completed.stdout or "").strip(),
        })

    return {
        "schema_version": 1,
        "kind": "ssh-return-recovery",
        "ok": all(item["returncode"] == 0 for item in results),
        "profile": config.profile,
        "destination": config.destination,
        "results": results,
    }
