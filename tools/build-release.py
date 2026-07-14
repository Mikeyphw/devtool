#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

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


def iter_source_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        yield path, relative


def build_source_archive(root: Path, output: Path, *, epoch: int) -> dict[str, object]:
    versions = load_versions(root)
    prefix = f"devtool-{versions['devtool']}/"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, relative in iter_source_files(root):
            info = zipfile.ZipInfo(prefix + relative.as_posix(), date_time=zip_datetime(epoch))
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
        "schema_version": 1,
        "release": versions["release"],
        "source_date_epoch": epoch,
        "generated_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "artifacts": [artifact],
    }
    manifest_path = out_dir / "release-artifacts.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums = out_dir / "SHA256SUMS"
    checksums.write_text(f"{artifact['sha256']}  {archive.name}\n", encoding="utf-8")
    print(archive)
    print(manifest_path)
    print(checksums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
