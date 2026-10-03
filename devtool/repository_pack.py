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
import shutil
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
    TAR_ZST = "tar.zst"
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
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts or text in {".", ".."}:
        raise RepositoryPackError(f"Unsafe repository-pack member path: {value!r}")
    if path.parts and path.parts[0] == ".git":
        raise RepositoryPackError("Raw .git content is never a repository-pack member; request a Git bundle instead.")
    return path.as_posix()


def _is_devtool_private_path(value: str) -> bool:
    path = PurePosixPath(str(value).replace("\\", "/"))
    return len(path.parts) >= 2 and path.parts[0] == ".devtool" and path.parts[1] == "private"


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD", check=False).stdout.strip()


def _branch(root: Path) -> str:
    return _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()


def _status(root: Path) -> str:
    raw = _git(root, "status", "--porcelain=v2", "--untracked-files=all", check=False).stdout
    # Devtool's private RUN/registry receipts are lifecycle evidence about pack
    # publication, not source members of the repository being packed.  Including
    # them in the source fingerprint makes a second publication of the same plan
    # falsely look like source drift solely because the first publication wrote
    # its own authority records.
    rows = []
    for line in raw.splitlines():
        # Porcelain v2 stores paths after a space for ordinary rows and after a
        # tab for rename rows.  The simple split keeps this filter independent of
        # status kind while preserving the original stable status text.
        path_tail = line.rsplit(" ", 1)[-1].split("	")[-1]
        if _is_devtool_private_path(path_tail):
            continue
        rows.append(line)
    return "\n".join(rows) + ("\n" if rows else "")


def _source_fingerprint(root: Path, *, members: tuple[str, ...], mode: RepositoryPackMode, commit: str) -> str:
    identity = build_repository_identity(root)
    member_hashes = []
    for rel in members:
        data = _member_bytes(root, mode, rel, commit=commit)
        member_hashes.append((rel, _sha_bytes(data), len(data)))
    payload = {
        "repository": identity.fingerprint,
        "mode": mode.value,
        "commit": commit,
        "members": member_hashes,
    }
    # Historical commit packs are bound to the selected immutable commit, not
    # to whatever HEAD/worktree happens to exist when publication occurs.
    if mode != RepositoryPackMode.COMMIT:
        payload.update({
            "head": _head(root),
            "branch": _branch(root),
            "status_sha256": _sha_bytes(_status(root).encode("utf-8")),
        })
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
    values = [item for item in values if not _is_devtool_private_path(item)]
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
    selected_commit: str
    branch: str
    dirty: bool
    source_fingerprint: str
    members: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "identity": self.identity.as_dict(),
            "head": self.head,
            "selected_commit": self.selected_commit,
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
    selected_commit: str
    branch: str
    dirty: bool
    source_fingerprint: str
    members: tuple[RepositoryPackMember, ...]
    git_bundle_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": dict(self.identity),
            "head": self.head,
            "selected_commit": self.selected_commit,
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


def _persist_repository_pack_receipt(repo_root: str | Path, receipt: RepositoryPackReceipt) -> str:
    """Persist the publication receipt as canonical RUN evidence.

    Repository-pack bytes are lifecycle-managed and may legitimately be garbage
    collected later.  The immutable receipt remains durable RUN evidence, so we
    attach the receipt file rather than pinning the pack payload itself forever.
    """
    from .core_runtime import private_run_root
    from .run_store import AttachmentDescriptor, RunCatalog

    root = Path(repo_root).expanduser().resolve(strict=False)
    run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip() or f"repo-pack-{receipt.pack_id.split(':')[-1]}"
    catalog_root = private_run_root(root)
    evidence_dir = catalog_root / "evidence" / run_id / "repository-pack"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        evidence_dir.chmod(0o700)
    except OSError:
        pass
    safe_id = receipt.receipt_id.replace(":", "-")
    path = evidence_dir / f"{safe_id}.json"
    payload = {
        "schema_version": 1,
        "kind": "devtool.repository-pack-receipt",
        "run_id": run_id,
        "receipt": receipt.as_dict(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)

    catalog = RunCatalog.open(catalog_root)
    prior = dict(catalog.runs.get(run_id, {}))
    prior.update({
        "run_id": run_id,
        "command_id": str(prior.get("command_id") or "repo-pack.create"),
        "status": str(prior.get("status") or "running"),
        "repository_pack_receipt_id": receipt.receipt_id,
        "repository_pack_id": receipt.pack_id,
        "repository_pack_output": receipt.output,
        "repository_pack_sha256": receipt.sha256,
    })
    catalog.add_run(prior)
    attachment = AttachmentDescriptor.from_file(
        run_id=run_id, path=path, kind="repository-pack-receipt", privacy="private",
        media_type="application/json",
    )
    ref = catalog.add_attachment(attachment)
    catalog.save()
    return ref.ref_id


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
        effective_commit = request.commit
        if request.mode == RepositoryPackMode.COMMIT:
            requested = request.commit or head
            resolved = _git(root, "rev-parse", "--verify", f"{requested}^{{commit}}", check=False).stdout.strip()
            if not resolved:
                raise RepositoryPackError(f"Cannot resolve repository-pack commit {requested!r} in {root}")
            effective_commit = resolved
        members = _members(root, request.mode, commit=effective_commit, handoff_members=tuple(handoff))
        source_fingerprint = _source_fingerprint(root, members=members, mode=request.mode, commit=effective_commit)
        plans.append(RepositoryPackRepositoryPlan(
            root=str(root),
            identity=identity,
            head=head,
            selected_commit=effective_commit if request.mode == RepositoryPackMode.COMMIT else "",
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
    # Bundle the selected commit through a short-lived private ref.  `git bundle`
    # requires a named ref tip; using HEAD here would silently replace a requested
    # historical commit with the current repository tip.
    selected = commit or _head(root)
    resolved = _git(root, "rev-parse", "--verify", f"{selected}^{{commit}}", check=False).stdout.strip()
    if not resolved:
        raise RepositoryPackError(f"Cannot resolve Git bundle commit {selected!r}")
    ref = f"refs/devtool/repository-pack/{_digest({'commit': resolved, 'destination': str(destination)})[:24]}"
    update = _git(root, "update-ref", ref, resolved, check=False)
    if update.returncode != 0:
        raise RepositoryPackError(update.stderr.strip() or "git update-ref for repository pack failed")
    try:
        proc = _git(root, "bundle", "create", str(destination), ref, check=False)
        if proc.returncode != 0:
            raise RepositoryPackError(proc.stderr.strip() or "git bundle create failed")
        verify = _git(root, "bundle", "verify", str(destination), check=False)
        if verify.returncode != 0:
            raise RepositoryPackError(verify.stderr.strip() or "git bundle verify failed")
        heads = _git(root, "bundle", "list-heads", str(destination), check=False).stdout
        if resolved not in heads:
            raise RepositoryPackError("repository-pack Git bundle does not contain the selected commit ref")
        return _sha_file(destination)
    finally:
        _git(root, "update-ref", "-d", ref, check=False)


def _write_tar_stream(path: Path, files: Mapping[str, bytes]) -> None:
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


def _zstd_binary() -> str:
    binary = shutil.which("zstd")
    if not binary:
        raise RepositoryPackError("tar.zst repository packs require the zstd executable")
    return binary


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
    if compression == RepositoryPackCompression.TAR_ZST:
        fd, raw_tar = tempfile.mkstemp(prefix="devtool-pack-tar-", suffix=".tar", dir=path.parent)
        os.close(fd)
        tar_path = Path(raw_tar)
        try:
            _write_tar_stream(tar_path, files)
            proc = subprocess.run([_zstd_binary(), "-q", "-f", "-19", "--no-progress", str(tar_path), "-o", str(path)], capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise RepositoryPackError(proc.stderr.strip() or "zstd repository-pack compression failed")
        finally:
            tar_path.unlink(missing_ok=True)
        return
    _write_tar_stream(path, files)


def _verify_archive(path: Path, compression: RepositoryPackCompression, expected_names: set[str]) -> None:
    if compression == RepositoryPackCompression.ZIP:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    elif compression == RepositoryPackCompression.TAR_ZST:
        fd, raw_tar = tempfile.mkstemp(prefix="devtool-pack-verify-", suffix=".tar", dir=path.parent)
        os.close(fd)
        tar_path = Path(raw_tar)
        try:
            with tar_path.open("wb") as stream:
                proc = subprocess.run([_zstd_binary(), "-q", "-d", "-c", str(path)], stdout=stream, stderr=subprocess.PIPE, check=False)
            if proc.returncode != 0:
                detail = proc.stderr.decode("utf-8", "replace").strip() if isinstance(proc.stderr, bytes) else str(proc.stderr or "").strip()
                raise RepositoryPackError(detail or "zstd repository-pack verification failed")
            with tarfile.open(tar_path, "r:") as archive:
                names = {item.name for item in archive.getmembers() if item.isfile()}
        finally:
            tar_path.unlink(missing_ok=True)
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
                data = _member_bytes(root, plan.mode, rel, commit=repo_plan.selected_commit if plan.mode == RepositoryPackMode.COMMIT else "")
                file_map[_archive_member_name(repo_plan.identity, rel)] = data
                members_manifest.append(RepositoryPackMember(rel, _sha_bytes(data), len(data)))
            bundle_digest = ""
            if plan.include_git_bundle:
                fd, raw_tmp = tempfile.mkstemp(prefix="devtool-pack-git-", suffix=".bundle", dir=output.parent)
                os.close(fd)
                bundle_path = Path(raw_tmp)
                bundle_temp_files.append(bundle_path)
                bundle_digest = _build_git_bundle(root, bundle_path, repo_plan.selected_commit or repo_plan.head)
                file_map[f"git/{repo_plan.identity.fingerprint[:20]}.bundle"] = bundle_path.read_bytes()
            repo_manifests.append(RepositoryPackRepositoryManifest(
                identity=repo_plan.identity.as_dict(),
                head=repo_plan.head,
                selected_commit=repo_plan.selected_commit,
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
            current_members = _members(root, plan.mode, commit=repo_plan.selected_commit if plan.mode == RepositoryPackMode.COMMIT else "", handoff_members=repo_plan.members if plan.mode == RepositoryPackMode.HANDOFF else ())
            current = _source_fingerprint(root, members=current_members, mode=plan.mode, commit=repo_plan.selected_commit if plan.mode == RepositoryPackMode.COMMIT else "")
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
        receipt = RepositoryPackReceipt(
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
        # Publication is not complete until the source repositories have durable
        # lifecycle records. A registry failure therefore fails the create call
        # instead of leaving a plausible receipt with no retention authority.
        from .repository_pack_registry import register_repository_pack
        for repo_plan in plan.repositories:
            register_repository_pack(repo_plan.root, plan=plan, receipt=receipt, repository=repo_plan)
            _persist_repository_pack_receipt(repo_plan.root, receipt)
        return receipt
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
