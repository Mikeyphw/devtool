#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shutil
import stat
import zipfile

from devtool.release_qualification import load_versions, repository_root, sha256_file, validate_version_consistency


EXCLUDED_PARTS = {
    ".git", ".gradle", ".idea", ".pytest_cache", "__pycache__", "build", "dist",
    ".devtool", ".venv", "venv", "node_modules",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".class", ".apk", ".aab", ".deb"}


def source_date_epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "1784030400")  # 2026-07-14 12:00:00 UTC
    try:
        value = int(raw)
    except ValueError as error:
        raise SystemExit("SOURCE_DATE_EPOCH must be an integer") from error
    return max(value, 315532800)


def zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(epoch, timezone.utc)
    year = min(max(value.year, 1980), 2107)
    return year, value.month, value.day, value.hour, value.minute, value.second - value.second % 2


def _safe_relative_path(relative: Path) -> str:
    text = relative.as_posix()
    if not text or text.startswith("/") or "\\" in text:
        raise RuntimeError(f"Unsafe release path: {text!r}")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"Unsafe release path: {text!r}")
    return text


def iter_source_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        _safe_relative_path(relative)
        yield path, relative


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path, relative in iter_source_files(root):
        digest.update(_safe_relative_path(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def git_head(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temporary.unlink(missing_ok=True)


def build_source_archive(root: Path, output: Path, *, epoch: int) -> dict[str, object]:
    versions = load_versions(root)
    prefix = f"devtool-{versions['devtool']}/"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, relative in iter_source_files(root):
            info = zipfile.ZipInfo(prefix + _safe_relative_path(relative), date_time=zip_datetime(epoch))
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = (permissions & 0xFFFF) << 16
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt member in generated archive: {bad}")
    return {
        "path": output.name,
        "sha256": sha256_file(output),
        "bytes": output.stat().st_size,
        "entries": sum(1 for _ in iter_source_files(root)),
    }


def verify_release_manifest(out_dir: Path, manifest: dict[str, object]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("Release manifest must list at least one artifact")
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError("Release manifest artifact entry must be an object")
        name = str(item.get("path") or "")
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise RuntimeError(f"Unsafe artifact name in release manifest: {name!r}")
        path = out_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"Release artifact is missing or empty: {path}")
        actual = sha256_file(path)
        if actual != item.get("sha256"):
            raise RuntimeError(f"Release artifact checksum mismatch for {name}: {actual} != {item.get('sha256')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Devtool release artifacts.")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--verify-reproducible", action="store_true")
    args = parser.parse_args()

    root = (args.root or repository_root()).resolve()
    errors = validate_version_consistency(root)
    if errors:
        raise SystemExit("Version consistency failed:\n- " + "\n- ".join(errors))
    versions = load_versions(root)
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    epoch = source_date_epoch()
    archive = out_dir / f"devtool-{versions['devtool']}-source.zip"
    artifact = build_source_archive(root, archive, epoch=epoch)

    if args.verify_reproducible:
        comparison = out_dir / f".{archive.name}.reproducibility-check"
        try:
            build_source_archive(root, comparison, epoch=epoch)
            if sha256_file(comparison) != artifact["sha256"]:
                raise SystemExit("Deterministic source archive check failed")
        finally:
            comparison.unlink(missing_ok=True)

    manifest = {
        "schema_version": 2,
        "release": versions["release"],
        "versions": versions,
        "hash_algorithm": "sha256",
        "source_date_epoch": epoch,
        "generated_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "git_head": git_head(root),
        "source_tree_sha256": tree_hash(root),
        "artifacts": [artifact],
    }
    verify_release_manifest(out_dir, manifest)
    manifest_path = out_dir / "release-artifacts.json"
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    checksums = out_dir / "SHA256SUMS"
    atomic_write_text(checksums, f"{artifact['sha256']}  {archive.name}\n")
    print(archive)
    print(manifest_path)
    print(checksums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
