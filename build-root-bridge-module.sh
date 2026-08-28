#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SRC="$ROOT/root-module/devtool-bridge"
OUT="${1:-$ROOT/devtool_root_bridge_module.zip}"

if [[ -z ${SOURCE_DATE_EPOCH:-} ]]; then
  SOURCE_DATE_EPOCH=$(git -C "$ROOT" log -1 --format=%ct HEAD 2>/dev/null || true)
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}
fi
case "$SOURCE_DATE_EPOCH" in
  *[!0-9]*|'') echo "SOURCE_DATE_EPOCH must be an integer" >&2; exit 2 ;;
esac
export SOURCE_DATE_EPOCH
mkdir -p "$(dirname "$OUT")"

python3 - "$SRC" "$OUT" <<'PY'
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

src = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
epoch = max(int(os.environ["SOURCE_DATE_EPOCH"]), 315532800)
dt = datetime.fromtimestamp(epoch, timezone.utc)
date_time = (min(max(dt.year, 1980), 2107), dt.month, dt.day, dt.hour, dt.minute, dt.second - dt.second % 2)

source_files = [p for p in src.rglob("*") if p.is_file() and not p.is_symlink() and p.name != ".DS_Store" and "__MACOSX" not in p.parts]
source_files.sort(key=lambda p: p.relative_to(src).as_posix())
fd, temp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
os.close(fd)
temp = Path(temp_name)
try:
    # Package from a private staging tree. This makes source-tree mutation
    # impossible and gives the archive one normalized permission model.
    with tempfile.TemporaryDirectory(prefix="devtool-root-bridge-stage-", dir=out.parent) as stage_name:
        stage = Path(stage_name)
        staged: list[tuple[Path, str, int]] = []
        for source_path in source_files:
            relative = source_path.relative_to(src)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            source_mode = source_path.stat().st_mode
            executable = bool(source_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            permissions = 0o755 if executable else 0o644
            os.chmod(destination, permissions)
            staged.append((destination, relative.as_posix(), permissions))

        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, relative, permissions in staged:
                info = zipfile.ZipInfo(relative, date_time=date_time)
                info.external_attr = (permissions & 0xFFFF) << 16
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    sync_fd = os.open(temp, os.O_RDONLY)
    try:
        os.fsync(sync_fd)
    finally:
        os.close(sync_fd)
    with zipfile.ZipFile(temp) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"Corrupt member in generated root module: {bad}")
    os.replace(temp, out)
    if os.name == "posix":
        directory_fd = os.open(out.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
finally:
    temp.unlink(missing_ok=True)
print(out)
PY
