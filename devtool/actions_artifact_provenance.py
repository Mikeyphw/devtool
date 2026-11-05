"""Reusable source-independent GitHub artifact ZIP integrity validator.

Verifies the *downloaded* archive, not only the GitHub REST metadata digest.
All checks are read-only; success never qualifies signing, devices, or releases.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

DIGEST = re.compile(r"(?:sha256:)?([a-f0-9]{64})\Z")
MAX_ZIP = 512 * 1024 * 1024
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_MEMBERS = 1024


class ArtifactProvenanceError(ValueError):
    pass


def verify_github_zip(archive: Path, expected_digest: str) -> dict:
    """Return authenticated transfer/member digests without extracting anything."""
    match = DIGEST.fullmatch(expected_digest) if isinstance(expected_digest, str) else None
    if match is None:
        raise ArtifactProvenanceError("Exact SHA-256 digest is required")
    archive = Path(archive)
    if archive.is_symlink() or not archive.is_file():
        raise ArtifactProvenanceError("Missing or symlinked ZIP artifact")
    size = archive.stat().st_size
    if not 0 < size <= MAX_ZIP:
        raise ArtifactProvenanceError("Unbounded ZIP artifact size")
    transfer = sha256()
    with archive.open("rb") as fp:
        while block := fp.read(1024 * 1024):
            transfer.update(block)
    if transfer.hexdigest() != match.group(1):
        raise ArtifactProvenanceError("Downloaded ZIP SHA-256 differs from GitHub metadata")
    members = []
    seen = set()
    expanded = 0
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if not 0 < len(infos) <= MAX_MEMBERS:
                raise ArtifactProvenanceError("Unbounded ZIP member count")
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                if (not name or len(name) > 240 or name.startswith("/") or "\\" in name
                        or ":" in name or any(x in ("", ".", "..") for x in name.rstrip("/").split("/"))):
                    raise ArtifactProvenanceError("Unsafe ZIP member path")
                if name.casefold() in seen:
                    raise ArtifactProvenanceError("Duplicate ZIP member")
                seen.add(name.casefold())
                mode = info.external_attr >> 16
                if (info.flag_bits & 1 or stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                    raise ArtifactProvenanceError("Nonregular ZIP member or symlink")
                if info.is_dir():
                    continue
                if not path.parts or info.file_size < 0:
                    raise ArtifactProvenanceError("Invalid ZIP file")
                expanded += info.file_size
                if expanded > MAX_UNCOMPRESSED:
                    raise ArtifactProvenanceError("Unbounded expanded archive")
                if info.file_size > 32 * 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 300:
                    raise ArtifactProvenanceError("Suspected ZIP decompression bomb")
                digest = sha256()
                length = 0
                with zf.open(info, "r") as fp:
                    while chunk := fp.read(1024 * 1024):
                        length += len(chunk)
                        if length > info.file_size:
                            raise ArtifactProvenanceError("ZIP member grew beyond declared size")
                        digest.update(chunk)
                if length != info.file_size:
                    raise ArtifactProvenanceError("Truncated ZIP member")
                members.append({"path": name, "sha256": digest.hexdigest(), "size": length})
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise ArtifactProvenanceError("Corrupt or unreadable ZIP archive") from exc
    if not members:
        raise ArtifactProvenanceError("ZIP archive contains no files")
    return {"schema_version": 1, "kind": "devtool.github-artifact.transfer-integrity",
            "status": "ARCHIVE_BYTES_AND_MEMBERS_VERIFIED_UNQUALIFIED",
            "zip_sha256": transfer.hexdigest(), "zip_size": size,
            "members": members, "release_qualified": False,
            "device_qualified": False, "cutover_authorized": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_github_zip(args.archive, args.sha256)
        if args.out:
            if args.out.exists() or args.out.is_symlink():
                raise ArtifactProvenanceError("Refusing to overwrite provenance output")
            args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ArtifactProvenanceError, OSError, ValueError) as exc:
        print("DEVTOOL_GITHUB_ZIP_REFUSED: " + str(exc)[:240], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
