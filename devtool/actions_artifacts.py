"""ACTIONS-04: bounded, source-bound artifact acquisition and local verification.

A GitHub Actions ZIP is an unqualified external archive, not release evidence.
Download does not unpack or execute content. Preserve remote run/commit provenance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .actions_runs import _normalize_run
from .artifact_registry import load_registry, register_paths
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug

_MAX_ARCHIVE = 256 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
_MAX_ENTRIES = 4096
_SHA = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")


def _artifact_id(value: Any) -> int:
    if isinstance(value, bool) or not str(value or "").isdecimal() or int(value) <= 0:
        raise GitHubActionsError("Artifact ID must be a positive integer")
    return int(value)


def _digest(value: Any) -> str:
    match = _SHA.fullmatch(str(value or ""))
    if not match:
        raise GitHubActionsError("Artifact has no valid GitHub sha256 digest; refusing unverified download")
    return match.group(1).lower()


def _metadata(raw: Any, slug: str, remote_run: dict[str, Any], *, exact_id: int | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] <= 0:
        raise GitHubActionsError("GitHub returned an invalid artifact identity")
    if exact_id is not None and raw["id"] != exact_id:
        raise GitHubActionsError("Artifact identity mismatch")
    parent = raw.get("workflow_run")
    if not isinstance(parent, dict) or parent.get("id") != remote_run["id"]:
        raise GitHubActionsError("Artifact is not bound to the selected workflow run")
    sha = parent.get("head_sha")
    if not isinstance(sha, str) or not re.fullmatch("[a-fA-F0-9]{40}", sha):
        raise GitHubActionsError("Artifact lacks a valid workflow source commit")
    if sha.lower() != str(remote_run.get("head_sha") or "").lower():
        raise GitHubActionsError("Artifact commit does not match the selected workflow run")
    if raw.get("expired") is True:
        raise GitHubActionsError("GitHub artifact has expired")
    size = raw.get("size_in_bytes")
    if type(size) is not int or size < 0 or size > _MAX_ARCHIVE:
        raise GitHubActionsError("Artifact advertised size exceeds 256 MiB limit or is invalid")
    name = raw.get("name")
    if not isinstance(name, str) or not name or len(name) > 256:
        raise GitHubActionsError("GitHub artifact name is invalid")
    # GitHub's artifact payload often omits workflow_run.run_attempt, so only
    # compare the attempt when the API actually provided one. Never invent it.
    parent_attempt = parent.get("run_attempt")
    if parent_attempt is not None and parent_attempt != remote_run.get("run_attempt"):
        raise GitHubActionsError("Artifact workflow attempt does not match selected run")
    return {"id": raw["id"], "name": name, "repository": slug,
            "run_id": remote_run["id"], "run_attempt": remote_run.get("run_attempt"),
            "head_sha": sha.lower(), "size_in_bytes": size,
            "digest": raw.get("digest"), "expired": False,
            "created_at": raw.get("created_at"), "expires_at": raw.get("expires_at")}


def artifact_list(client: GitHubActionsClient, slug: str, run_id: int) -> dict[str, Any]:
    slug = _slug(slug)
    remote = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    rows = [_metadata(r, slug, remote) for r in client.run_artifacts(slug, run_id)]
    return {"phase": "ACTIONS-04", "repository": slug, "remote_run": remote,
            "artifacts": rows, "qualified_release": False}


def _signed_host(url: str) -> bool:
    p = urlsplit(url)
    hostname = (p.hostname or "").lower()
    return (p.scheme == "https" and p.port in (None, 443) and not p.username
            and not p.password and not p.fragment and hostname in (
                "objects.githubusercontent.com", "results.actions.githubusercontent.com")
            or p.scheme == "https" and p.port in (None, 443) and not p.username
            and not p.password and not p.fragment and (
                hostname.endswith(".actions.githubusercontent.com")
                or hostname.endswith(".blob.core.windows.net")))


def check_zip(path: Path) -> dict[str, int]:
    """Inspect names, types and bounded expanded content without extracting."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ENTRIES or not infos:
                raise GitHubActionsError("Artifact ZIP has zero or too many members")
            expanded = 0
            names: set[str] = set()
            for info in infos:
                filename = info.filename
                parts = PurePosixPath(filename).parts
                if (not filename or filename.startswith(("/", "\\")) or "\\" in filename
                    or any(p in ("..", ".") for p in parts)
                    or any(ord(c) < 32 for c in filename)
                    or (parts and re.match(r"[a-zA-Z]:", parts[0]))):
                    raise GitHubActionsError("Artifact ZIP contains unsafe paths")
                # Reject distinct ZIP spellings of the same normalized path.
                # PurePosixPath silently folds ./ and duplicate separators.
                if ("//" in filename or any(part in ("", ".") for part in filename.rstrip("/").split("/"))):
                    raise GitHubActionsError("Artifact ZIP contains noncanonical member paths")
                key = filename.rstrip("/").casefold()
                if not key or key in names:
                    raise GitHubActionsError("Artifact ZIP contains duplicate or unsafe member names")
                names.add(key)
                mode = (info.external_attr >> 16) & 0xffff
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFDIR, stat.S_IFREG):
                    raise GitHubActionsError("Artifact ZIP contains a symbolic link or special file")
                if info.flag_bits & 1:
                    raise GitHubActionsError("Encrypted artifact ZIP members are unsupported")
                expanded += info.file_size
                if expanded > _MAX_TOTAL_UNCOMPRESSED:
                    raise GitHubActionsError("Artifact ZIP expands beyond 512 MiB limit")
                if info.file_size > 1000 * max(info.compress_size, 1) and info.file_size > 1024 * 1024:
                    raise GitHubActionsError("Artifact ZIP contains excessive compression")
                # CRC check also catches corrupted payloads; no destination extraction.
                with archive.open(info) as stream:
                    while stream.read(1024 * 1024):
                        pass
            return {"members": len(infos), "uncompressed_bytes": expanded}
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise GitHubActionsError("Downloaded artifact is not a valid safe ZIP archive") from None


def verify_embedded_debug_receipt(archive: Path, *, slug: str, remote: dict[str, Any]) -> dict[str, Any]:
    """Check a Wave C APK+receipt ZIP against independently queried GitHub run identity.

    Unlike a ZIP hash, this checks the embedded APK and its producer *claims*.
    It does not independently verify Android signing, trust the package name,
    prove provenance of the GitHub runner, or qualify a release/device build.
    Diagnostic ZIPs without build-evidence.json remain archive-only.
    """
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if 'build-evidence.json' not in names:
            return {'receipt_status': 'absent', 'receipt_integrity_verified': False}
        if len(names) != 2:
            raise GitHubActionsError('Build receipt archive must contain exactly an APK and build-evidence.json')
        try:
            entry = z.getinfo('build-evidence.json')
            if not 0 < entry.file_size <= 32768:
                raise ValueError('receipt size')
            receipt = json.loads(z.read(entry))
        except (KeyError, ValueError, UnicodeError, TypeError):
            raise GitHubActionsError('Invalid embedded Android build receipt') from None
        if not isinstance(receipt, dict) or receipt.get('schema_version') != 2:
            raise GitHubActionsError('Unsupported embedded Android build receipt schema')
        member = receipt.get('artifact_member')
        if (not isinstance(member, str) or not re.fullmatch(r'[A-Za-z0-9._-]+\.apk', member)
                or set(names) != {member, 'build-evidence.json'}):
            raise GitHubActionsError('Embedded build receipt does not identify exactly one APK')
        apk = z.getinfo(member)
        if apk.file_size <= 0 or apk.file_size > 150*1024*1024:
            raise GitHubActionsError('Embedded APK exceeds bounded size')
        sha = str(remote.get('head_sha') or '').lower()
        rid = remote.get('id');attempt = remote.get('run_attempt')
        if (receipt.get('repository', '').lower() != slug.lower()
                or receipt.get('source_sha') != sha
                or type(rid) is not int or type(attempt) is not int
                or receipt.get('github_run_id') != rid
                or receipt.get('github_run_attempt') != attempt
                or receipt.get('workflow_run_url') != f'https://github.com/{slug}/actions/runs/{rid}'):
            raise GitHubActionsError('Embedded build receipt does not match the selected GitHub run')
        for name in ('run_id', 'run_attempt'):
            if name in receipt and receipt[name] != (rid if name == 'run_id' else attempt):
                raise GitHubActionsError('Embedded build receipt has conflicting run identity')
        if (receipt.get('format') != 'apk' or receipt.get('variant') != 'debug'
                or receipt.get('apk_size_bytes') != apk.file_size
                or receipt.get('apk_signature_verified') is not True
                or receipt.get('apk_manifest_verified') is not True
                or receipt.get('unit_tests_and_lint_passed') is not True
                or receipt.get('device_qualified') is not False
                or receipt.get('qualified_release', receipt.get('release_qualified')) is not False
                or not re.fullmatch('[a-f0-9]{64}', str(receipt.get('signing_certificate_sha256', '')).lower())):
            raise GitHubActionsError('Embedded build verification claims are incomplete or unsafe')
        h = hashlib.sha256()
        with z.open(apk) as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                h.update(chunk)
        if h.hexdigest() != receipt.get('apk_sha256'):
            raise GitHubActionsError('Embedded APK bytes do not match build receipt sha256')
        return {'receipt_status': 'integrity-verified', 'receipt_integrity_verified': True,
                'apk_member': member, 'apk_sha256': h.hexdigest(),
                'apk_size_bytes': apk.file_size, 'package_claimed': receipt.get('package'),
                'apk_signature_independently_verified': False,
                'device_qualified': False, 'qualified_release': False}


def _storage(repo_root: Path) -> Path:
    root = repo_root.resolve()
    folder = root / ".devtool" / "artifacts" / "github-downloads"
    for parent in (root / ".devtool", root / ".devtool" / "artifacts", folder):
        if parent.is_symlink():
            raise GitHubActionsError("Artifact destination contains a symlink")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _download_blob(client: GitHubActionsClient, signed: str, output: Path) -> tuple[str, int]:
    if not _signed_host(signed):
        raise GitHubActionsError("Untrusted GitHub artifact download redirect; no token forwarded")
    req = Request(signed, headers={"User-Agent": "devtool-actions-artifacts/1"}, method="GET")
    digest = hashlib.sha256()
    size = 0
    try:
        with client._opener.open(req, timeout=client.timeout) as response:
            if int(response.status) != 200:
                raise GitHubActionsError("GitHub artifact download did not return HTTP 200")
            with output.open("wb") as stream:
                os.chmod(output, 0o600)
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    size += len(data)
                    if size > _MAX_ARCHIVE:
                        raise GitHubActionsError("Downloaded artifact exceeded 256 MiB limit")
                    stream.write(data)
                    digest.update(data)
                stream.flush(); os.fsync(stream.fileno())
    except HTTPError as exc:
        raise GitHubActionsError(f"GitHub artifact archive HTTP {exc.code}") from None
    except (OSError, URLError, TimeoutError):
        raise GitHubActionsError("GitHub artifact archive download failed") from None
    return digest.hexdigest(), size


def download_artifact(client: GitHubActionsClient, slug: str, identity: int, artifact_id: int,
                      repo_root: Path) -> dict[str, Any]:
    slug = _slug(slug)
    identity = int(identity)
    artifact_id = _artifact_id(artifact_id)
    remote = _normalize_run(client.get_run(slug, identity), slug, identity)
    artifact = _metadata(client.get_artifact(slug, artifact_id), slug, remote, exact_id=artifact_id)
    expected = _digest(artifact["digest"])
    folder = _storage(repo_root)
    fd, tmp_name = tempfile.mkstemp(prefix=".download-", suffix=".zip", dir=folder)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        signed = client.artifact_download_location(slug, artifact_id)
        actual, size = _download_blob(client, signed, tmp)
        if actual != expected:
            raise GitHubActionsError("GitHub artifact sha256 mismatch; discarded archive")
        zip_check = check_zip(tmp)
        embedded = verify_embedded_debug_receipt(tmp, slug=slug, remote=remote)
        target = folder / f"github-{slug.replace('/', '_')}-run{identity}-artifact{artifact_id}-{expected[:16]}.zip"
        if target.is_symlink() or target.exists():
            raise GitHubActionsError("Artifact already downloaded; use actions verify instead of overwriting")
        os.replace(tmp, target)
        dfd = os.open(folder, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)
    meta = {"producer": "github-actions", "state": "downloaded-unqualified",
            "remote_repository": slug, "remote_run_id": identity,
            "remote_run_attempt": remote.get("run_attempt"),
            "remote_workflow_id": remote.get("workflow_id"),
            "remote_head_sha": remote.get("head_sha"),
            "remote_artifact_id": artifact_id,
            "remote_digest": f"sha256:{expected}", "archive_digest_verified": True,
            "archive_members": zip_check["members"], "qualified_release": False,
            "embedded_build_receipt": embedded,
            "signing_verified": False, "extracted": False, "verification_status": "archive-sha256-only"}
    records = register_paths(repo_root, [target], metadata=meta)
    if not records:
        raise GitHubActionsError("Archive saved but canonical artifact registry did not accept it")
    return {"phase": "ACTIONS-04", "repository": slug, "run_id": identity,
            "artifact": artifact, "file": str(target), "sha256": expected,
            "bytes": size, "zip_validation": zip_check, "embedded_build_receipt": embedded,
            "registry_id": records[0]["id"], "download_verified": True,
            "extracted": False, "qualified_release": False}


def verify_download(client: GitHubActionsClient, slug: str, identity: int, artifact_id: int,
                    repo_root: Path) -> dict[str, Any]:
    slug = _slug(slug)
    artifact_id = _artifact_id(artifact_id)
    remote = _normalize_run(client.get_run(slug, identity), slug, identity)
    artifact = _metadata(client.get_artifact(slug, artifact_id), slug, remote, exact_id=artifact_id)
    expected = _digest(artifact["digest"])
    data = load_registry(repo_root)
    records = [r for r in data.get("items",[]) if isinstance(r,dict) and
               isinstance(r.get("metadata"),dict) and
               r["metadata"].get("remote_repository", "").lower() == slug.lower() and
               r["metadata"].get("remote_run_id") == identity and
               r["metadata"].get("remote_artifact_id") == artifact_id]
    if not records:
        raise GitHubActionsError("No registered download for this GitHub artifact")
    record = records[0]
    path = (repo_root.resolve() / str(record.get("path", ""))).resolve()
    folder = _storage(repo_root)
    if path.parent != folder or path.is_symlink() or not path.is_file():
        raise GitHubActionsError("Registered artifact file is missing or outside download storage")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024 * 1024),b""): h.update(block)
    if h.hexdigest() != expected or record.get("sha256") != expected:
        raise GitHubActionsError("Registered artifact archive integrity check failed")
    zip_check = check_zip(path)
    embedded = verify_embedded_debug_receipt(path, slug=slug, remote=remote)
    recorded = record["metadata"].get("embedded_build_receipt")
    if recorded is not None and recorded.get("receipt_status") != embedded["receipt_status"]:
        raise GitHubActionsError("Registered embedded receipt evidence differs from current archive")
    if record["metadata"].get("remote_head_sha") != remote.get("head_sha"):
        raise GitHubActionsError("Registered artifact remote source commit changed")
    if record["metadata"].get("remote_run_attempt") != remote.get("run_attempt"):
        raise GitHubActionsError("Registered artifact belongs to a different workflow attempt")
    return {"phase":"ACTIONS-04", "repository":slug,"run_id":identity,
            "artifact_id":artifact_id,"sha256":expected,"file":str(path),
            "zip_validation":zip_check,"embedded_build_receipt":embedded,
            "archive_verified":True,"qualified_release":False}
