from __future__ import annotations

"""Deterministic repository-pack planning and atomic publication (LIFE-X02)."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import time
from typing import Any, Iterable, Mapping
import zipfile

from .repository_identity import RepositoryIdentity, build_repository_identity


class RepositoryPackError(RuntimeError):
    pass


class RepositoryPackMode(StrEnum):
    WORKTREE = "worktree"
    TRACKED = "tracked"
    COMMIT = "commit"
    HANDOFF = "handoff"


class RepositoryPackCompression(StrEnum):
    TAR_GZ = "tar.gz"
    TAR = "tar"
    ZIP = "zip"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(root: Path, *args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess[Any]:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr if text else proc.stderr.decode("utf-8", "replace")).strip()
        raise RepositoryPackError(detail or f"git {' '.join(args)} failed with exit {proc.returncode}")
    return proc


def _safe_relative(value: str) -> str:
    text = value.replace("\\", "/").strip().lstrip("./")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or text in {".", ".."}:
        raise RepositoryPackError(f"Unsafe repository-pack member path: {value!r}")
    if path.parts and path.parts[0] == ".git":
        raise RepositoryPackError("Raw .git content is never a repository-pack member; request a Git bundle instead.")
    return path.as_posix()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD", check=False).stdout.strip()


def _branch(root: Path) -> str:
    return _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()


def _status(root: Path) -> str:
    return _git(root, "status", "--porcelain=v2", "--untracked-files=all", check=False).stdout


def _source_fingerprint(root: Path, *, members: tuple[str, ...], mode: RepositoryPackMode, commit: str) -> str:
    identity = build_repository_identity(root)
    member_hashes = []
    for rel in members:
        data = _member_bytes(root, mode, rel, commit=commit)
        member_hashes.append((rel, _sha_bytes(data), len(data)))
    payload = {
        "repository": identity.fingerprint,
        "head": _head(root),
        "branch": _branch(root),
        "status_sha256": _sha_bytes(_status(root).encode("utf-8")),
        "mode": mode.value,
        "commit": commit,
        "members": member_hashes,
    }
    return _digest(payload)


def _ls_files(root: Path, *args: str) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RepositoryPackError(proc.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    values = [_safe_relative(part.decode("utf-8", "surrogateescape")) for part in proc.stdout.split(b"\0") if part]
    return tuple(dict.fromkeys(values))


def _members(root: Path, mode: RepositoryPackMode, *, commit: str, handoff_members: tuple[str, ...]) -> tuple[str, ...]:
    if mode == RepositoryPackMode.WORKTREE:
        return _ls_files(root, "--cached", "--others", "--exclude-standard")
    if mode == RepositoryPackMode.TRACKED:
        return _ls_files(root, "--cached")
    if mode == RepositoryPackMode.COMMIT:
        treeish = commit or "HEAD"
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", treeish],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise RepositoryPackError(proc.stderr.decode("utf-8", "replace").strip() or f"Cannot list commit {treeish}")
        return tuple(_safe_relative(part.decode("utf-8", "surrogateescape")) for part in proc.stdout.split(b"\0") if part)
    if not handoff_members:
        raise RepositoryPackError("handoff mode requires an explicit non-empty member list.")
    return tuple(dict.fromkeys(_safe_relative(value) for value in handoff_members))


def _member_bytes(root: Path, mode: RepositoryPackMode, path: str, *, commit: str) -> bytes:
    if mode in {RepositoryPackMode.WORKTREE, RepositoryPackMode.HANDOFF}:
        target = root / path
        if not target.is_file():
            raise RepositoryPackError(f"Repository-pack member is not a regular file: {path}")
        return target.read_bytes()
    spec = f":{path}" if mode == RepositoryPackMode.TRACKED else f"{commit or 'HEAD'}:{path}"
    proc = _git(root, "show", spec, check=False, text=False)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RepositoryPackError(detail or f"Cannot read Git member {path}")
    return bytes(proc.stdout)


@dataclass(frozen=True, slots=True)
class RepositoryPackRequest:
    repositories: tuple[str, ...]
    output: str
    mode: RepositoryPackMode = RepositoryPackMode.WORKTREE
    compression: RepositoryPackCompression = RepositoryPackCompression.TAR_GZ
    include_git_bundle: bool = False
    commit: str = ""
    handoff_members: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    pin: bool = False
    retention: str = "policy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repositories": list(self.repositories),
            "output": self.output,
            "mode": self.mode.value,
            "compression": self.compression.value,
            "include_git_bundle": self.include_git_bundle,
            "commit": self.commit,
            "handoff_members": {str(k): list(v) for k, v in self.handoff_members.items()},
            "pin": self.pin,
            "retention": self.retention,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPackMember:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class RepositoryPackRepositoryPlan:
    root: str
    identity: RepositoryIdentity
    head: str
    branch: str
    dirty: bool
    source_fingerprint: str
    members: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "identity": self.identity.as_dict(),
            "head": self.head,
            "branch": self.branch,
            "dirty": self.dirty,
            "source_fingerprint": self.source_fingerprint,
            "members": list(self.members),
        }


@dataclass(frozen=True, slots=True)
class RepositoryPackPlan:
    request_id: str
    plan_id: str
    output: str
    mode: RepositoryPackMode
    compression: RepositoryPackCompression
    repositories: tuple[RepositoryPackRepositoryPlan, ...]
    include_git_bundle: bool
    pin: bool
    retention: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "plan_id": self.plan_id,
            "output": self.output,
            "mode": self.mode.value,
            "compression": self.compression.value,
            "repositories": [item.as_dict() for item in self.repositories],
            "include_git_bundle": self.include_git_bundle,
            "pin": self.pin,
            "retention": self.retention,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPackRepositoryManifest:
    identity: Mapping[str, Any]
    head: str
    branch: str
    dirty: bool
    source_fingerprint: str
    members: tuple[RepositoryPackMember, ...]
    git_bundle_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": dict(self.identity),
            "head": self.head,
            "branch": self.branch,
            "dirty": self.dirty,
            "source_fingerprint": self.source_fingerprint,
            "members": [asdict(item) for item in self.members],
            "git_bundle_sha256": self.git_bundle_sha256,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPackManifest:
    schema_version: int
    pack_id: str
    plan_id: str
    mode: str
    compression: str
    repositories: tuple[RepositoryPackRepositoryManifest, ...]
    created_unix_ns: int
    pin: bool
    retention: str
    content_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "plan_id": self.plan_id,
            "mode": self.mode,
            "compression": self.compression,
            "repositories": [item.as_dict() for item in self.repositories],
            "created_unix_ns": self.created_unix_ns,
            "pin": self.pin,
            "retention": self.retention,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPackReceipt:
    receipt_id: str
    pack_id: str
    plan_id: str
    output: str
    sha256: str
    size: int
    verified: bool
    published: bool
    pin: bool
    retention: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_repository_pack(request: RepositoryPackRequest) -> RepositoryPackPlan:
    if not request.repositories:
        raise RepositoryPackError("Repository pack requires at least one repository.")
    output_raw = Path(request.output).expanduser()
    if not output_raw.is_absolute() and ".." in output_raw.parts:
        raise RepositoryPackError("Repository-pack output must not escape via '..'.")
    output = output_raw.resolve(strict=False)
    plans: list[RepositoryPackRepositoryPlan] = []
    roots_seen: set[str] = set()
    for raw_root in request.repositories:
        root = Path(raw_root).expanduser().resolve()
        if not root.exists():
            raise RepositoryPackError(f"Repository does not exist: {root}")
        root_key = str(root)
        if root_key in roots_seen:
            continue
        roots_seen.add(root_key)
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise RepositoryPackError(f"Repository-pack output must be outside source repository: {output}")
        identity = build_repository_identity(root)
        handoff = request.handoff_members.get(raw_root) or request.handoff_members.get(str(root)) or ()
        head = _head(root)
        effective_commit = (request.commit or head) if request.mode == RepositoryPackMode.COMMIT else request.commit
        members = _members(root, request.mode, commit=effective_commit, handoff_members=tuple(handoff))
        source_fingerprint = _source_fingerprint(root, members=members, mode=request.mode, commit=effective_commit)
        plans.append(RepositoryPackRepositoryPlan(
            root=str(root),
            identity=identity,
            head=head,
            branch=_branch(root),
            dirty=bool(_status(root).strip()),
            source_fingerprint=source_fingerprint,
            members=members,
        ))
    request_id = f"pack-request:{_digest(request.as_dict())[:24]}"
    basis = {
        "request_id": request_id,
        "output": str(output),
        "repositories": [item.as_dict() for item in plans],
    }
    return RepositoryPackPlan(
        request_id=request_id,
        plan_id=f"pack-plan:{_digest(basis)[:24]}",
        output=str(output),
        mode=request.mode,
        compression=request.compression,
        repositories=tuple(plans),
        include_git_bundle=request.include_git_bundle,
        pin=request.pin,
        retention=request.retention,
    )


def _archive_member_name(identity: RepositoryIdentity, rel: str) -> str:
    return f"repositories/{identity.fingerprint[:20]}/{_safe_relative(rel)}"


def _build_git_bundle(root: Path, destination: Path, commit: str) -> str:
    # git bundle requires a revision expression that contributes a ref; a raw
    # object id can be rejected as an empty bundle. HEAD preserves the portable
    # history reachable from the repository's current authoritative tip.
    ref = "HEAD"
    proc = _git(root, "bundle", "create", str(destination), ref, check=False)
    if proc.returncode != 0:
        raise RepositoryPackError(proc.stderr.strip() or "git bundle create failed")
    verify = _git(root, "bundle", "verify", str(destination), check=False)
    if verify.returncode != 0:
        raise RepositoryPackError(verify.stderr.strip() or "git bundle verify failed")
    return _sha_file(destination)


def _write_archive(path: Path, compression: RepositoryPackCompression, files: Mapping[str, bytes]) -> None:
    if compression == RepositoryPackCompression.ZIP:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = 0o100644 << 16
                archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED)
        return
    if compression == RepositoryPackCompression.TAR_GZ:
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w:", format=tarfile.PAX_FORMAT) as archive:
                    for name in sorted(files):
                        data = files[name]
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        info.mode = 0o644
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = 0
                        archive.addfile(info, io.BytesIO(data))
        return
    with tarfile.open(path, "w:", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))


def _verify_archive(path: Path, compression: RepositoryPackCompression, expected_names: set[str]) -> None:
    if compression == RepositoryPackCompression.ZIP:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    else:
        with tarfile.open(path, "r:*") as archive:
            names = {item.name for item in archive.getmembers() if item.isfile()}
    if names != expected_names:
        missing = sorted(expected_names - names)
        extra = sorted(names - expected_names)
        raise RepositoryPackError(f"Repository-pack verification mismatch: missing={missing[:5]} extra={extra[:5]}")


def create_repository_pack(plan: RepositoryPackPlan) -> RepositoryPackReceipt:
    output = Path(plan.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    file_map: dict[str, bytes] = {}
    repo_manifests: list[RepositoryPackRepositoryManifest] = []
    bundle_temp_files: list[Path] = []
    try:
        for repo_plan in plan.repositories:
            root = Path(repo_plan.root)
            members_manifest: list[RepositoryPackMember] = []
            for rel in repo_plan.members:
                data = _member_bytes(root, plan.mode, rel, commit=repo_plan.head if plan.mode == RepositoryPackMode.COMMIT else "")
                file_map[_archive_member_name(repo_plan.identity, rel)] = data
                members_manifest.append(RepositoryPackMember(rel, _sha_bytes(data), len(data)))
            bundle_digest = ""
            if plan.include_git_bundle:
                fd, raw_tmp = tempfile.mkstemp(prefix="devtool-pack-git-", suffix=".bundle", dir=output.parent)
                os.close(fd)
                bundle_path = Path(raw_tmp)
                bundle_temp_files.append(bundle_path)
                bundle_digest = _build_git_bundle(root, bundle_path, repo_plan.head)
                file_map[f"git/{repo_plan.identity.fingerprint[:20]}.bundle"] = bundle_path.read_bytes()
            repo_manifests.append(RepositoryPackRepositoryManifest(
                identity=repo_plan.identity.as_dict(),
                head=repo_plan.head,
                branch=repo_plan.branch,
                dirty=repo_plan.dirty,
                source_fingerprint=repo_plan.source_fingerprint,
                members=tuple(members_manifest),
                git_bundle_sha256=bundle_digest,
            ))

        content_basis = {
            "plan_id": plan.plan_id,
            "repositories": [item.as_dict() for item in repo_manifests],
        }
        content_digest = _digest(content_basis)
        pack_id = f"repository-pack:{content_digest[:24]}"
        manifest = RepositoryPackManifest(
            schema_version=1,
            pack_id=pack_id,
            plan_id=plan.plan_id,
            mode=plan.mode.value,
            compression=plan.compression.value,
            repositories=tuple(repo_manifests),
            created_unix_ns=0,
            pin=plan.pin,
            retention=plan.retention,
            content_digest=content_digest,
        )
        file_map["devtool-repository-pack.json"] = json.dumps(manifest.as_dict(), indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"

        # Revalidate source identity/member set immediately before publication.
        for repo_plan in plan.repositories:
            root = Path(repo_plan.root)
            current_members = _members(root, plan.mode, commit=repo_plan.head if plan.mode == RepositoryPackMode.COMMIT else "", handoff_members=repo_plan.members if plan.mode == RepositoryPackMode.HANDOFF else ())
            current = _source_fingerprint(root, members=current_members, mode=plan.mode, commit=repo_plan.head if plan.mode == RepositoryPackMode.COMMIT else "")
            if current != repo_plan.source_fingerprint:
                raise RepositoryPackError(f"Repository source changed while packing: {root}")

        suffix = ".tmp"
        fd, raw_staged = tempfile.mkstemp(prefix=f".{output.name}.", suffix=suffix, dir=output.parent)
        os.close(fd)
        staged = Path(raw_staged)
        try:
            _write_archive(staged, plan.compression, file_map)
            _verify_archive(staged, plan.compression, set(file_map))
            staged_digest = _sha_file(staged)
            size = staged.stat().st_size
            os.replace(staged, output)
            if os.name == "posix":
                fd_dir = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(fd_dir)
                finally:
                    os.close(fd_dir)
        finally:
            staged.unlink(missing_ok=True)
        receipt_basis = {"pack_id": pack_id, "plan_id": plan.plan_id, "output": str(output), "sha256": staged_digest}
        return RepositoryPackReceipt(
            receipt_id=f"pack-receipt:{_digest(receipt_basis)[:24]}",
            pack_id=pack_id,
            plan_id=plan.plan_id,
            output=str(output),
            sha256=staged_digest,
            size=size,
            verified=True,
            published=True,
            pin=plan.pin,
            retention=plan.retention,
        )
    finally:
        for path in bundle_temp_files:
            path.unlink(missing_ok=True)


__all__ = [
    "RepositoryPackCompression",
    "RepositoryPackError",
    "RepositoryPackManifest",
    "RepositoryPackMode",
    "RepositoryPackPlan",
    "RepositoryPackReceipt",
    "RepositoryPackRequest",
    "create_repository_pack",
    "plan_repository_pack",
]
