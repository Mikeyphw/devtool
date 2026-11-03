"""DN03-R2: fail-closed .NET 6.0.36 Linux x64 runtime-only provisioning.

The archive checksum is published by Microsoft at
https://builds.dotnet.microsoft.com/dotnet/checksums/6.0.36-sha.txt
This is a local integrity check, not publisher signature attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

RUNTIME = '6.0.36'
ARCHIVE = 'dotnet-runtime-6.0.36-linux-x64.tar.gz'
URL = ('https://download.visualstudio.microsoft.com/download/pr/'
       'd0d7fabb-4221-441a-84ae-e94f59c8ab42/'
       'a7cd6251bd8ce5fac4baa1c057e4c5ed/' + ARCHIVE)
SHA512 = ('afb6018fcabec468ccd7ae2f1131d8c9de7f4de7645b8f0c223efbbdbfdc515f'
          'b0642a399ebfe372c02044416c4cae463c9c802cd156b9da4181efff0e33ee94')
PREFIX = ('shared', 'Microsoft.NETCore.App', RUNTIME)
MAX_COMPRESSED = 90_000_000
MAX_EXPANDED = 400_000_000
MAX_MEMBERS = 4000


class RuntimeOnlyError(ValueError):
    pass


def archive_digest(archive: Path) -> str:
    if archive.is_symlink() or not archive.is_file() or not (1 <= archive.stat().st_size <= MAX_COMPRESSED):
        raise RuntimeOnlyError('Missing, linked or oversized .NET runtime-only archive')
    h = hashlib.sha512()
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_archive(archive: Path) -> str:
    digest = archive_digest(archive)
    if digest != SHA512:
        raise RuntimeOnlyError('Microsoft runtime-only archive SHA-512 differs from reviewed release pin')
    return digest


def fetch_archive(archive: Path) -> None:
    if archive.exists() or archive.is_symlink():
        raise RuntimeOnlyError('Refusing to replace an existing runtime download')
    archive.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                             '--retry', '3', '--max-time', '150', '--proto', '=https',
                             '--output', str(archive), URL], check=False, timeout=180,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        archive.unlink(missing_ok=True)
        raise RuntimeOnlyError('Pinned Microsoft runtime-only download failed')
    verify_archive(archive)


def _safe_member(name: str) -> tuple[str, ...]:
    # Only the reviewed runtime version's exact subtree can be extracted.
    value = name[2:] if name.startswith('./') else name
    if name in ('.', './'):
        return ()
    if not value or value.startswith('/') or '\\' in value:
        raise RuntimeOnlyError('Unsafe archive member path')
    parts = PurePosixPath(value).parts
    if any(part in ('', '.', '..') for part in parts) or any(part == '..' for part in value.split('/')):
        raise RuntimeOnlyError('Archive member path traversal')
    return parts


def _root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeOnlyError('DOTNET_INSTALL_DIR must be an existing regular directory')
    root = root.resolve(strict=True)
    for rel in (('shared',), ('shared', 'Microsoft.NETCore.App')):
        part = root.joinpath(*rel)
        if part.is_symlink():
            raise RuntimeOnlyError('Symlinked .NET runtime installation root')
        if part.exists() and not part.is_dir():
            raise RuntimeOnlyError('Unexpected .NET runtime installation root type')
    return root


def install_archive(archive: Path, root: Path, *, receipt: Path | None = None) -> dict:
    digest = verify_archive(archive)
    root = _root(root)
    parent = root / 'shared' / 'Microsoft.NETCore.App'
    target = parent / RUNTIME
    stage = parent / ('.dn03-runtime-stage-' + str(os.getpid()))
    if target.exists() or target.is_symlink() or stage.exists() or stage.is_symlink():
        raise RuntimeOnlyError('Existing runtime or staging path; refusing overwrite')
    if receipt is not None and (receipt.exists() or receipt.is_symlink()):
        raise RuntimeOnlyError('Runtime receipt is not create-only')
    parent.mkdir(parents=True, exist_ok=True)
    stage.mkdir(mode=0o700)
    total = 0
    seen: set[tuple[str, ...]] = set()
    files = 0
    try:
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS:
                raise RuntimeOnlyError('Too many entries in runtime archive')
            for item in members:
                parts = _safe_member(item.name)
                if parts[:len(PREFIX)] != PREFIX:
                    continue
                tail = parts[len(PREFIX):]
                if not tail:
                    continue
                if tail in seen:
                    raise RuntimeOnlyError('Duplicate runtime archive entry')
                seen.add(tail)
                if len(tail) > 12 or any(len(piece) > 180 for piece in tail):
                    raise RuntimeOnlyError('Unbounded runtime archive path')
                dest = stage.joinpath(*tail)
                if item.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                if not item.isfile() or item.size < 0 or item.size > 80_000_000:
                    raise RuntimeOnlyError('Runtime archive contains unsupported file/link')
                total += item.size
                if total > MAX_EXPANDED:
                    raise RuntimeOnlyError('Unbounded runtime extraction')
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() or dest.is_symlink():
                    raise RuntimeOnlyError('Duplicate runtime payload path')
                f = tar.extractfile(item)
                if f is None:
                    raise RuntimeOnlyError('Unreadable runtime payload file')
                with f, dest.open('xb') as out:
                    shutil.copyfileobj(f, out, 1 << 20)
                dest.chmod(0o755 if (item.mode & 0o111) else 0o644)
                files += 1
        if files < 30 or not (stage / 'libcoreclr.so').is_file():
            raise RuntimeOnlyError('Incomplete Microsoft.NETCore.App runtime layout')
        stage.rename(target)
        value = {'schema_version': 1, 'kind': 'devtool.dn03-runtime-only',
                 'runtime': RUNTIME, 'archive': ARCHIVE, 'archive_sha512': digest,
                 'source': URL, 'files': files, 'expanded_bytes': total,
                 'sdk_installed_by_this_provisioner': False,
                 'release_qualified': False, 'publisher_authenticated': False}
        if receipt is not None:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            with receipt.open('x', encoding='utf-8') as out:
                json.dump(value, out, indent=2, sort_keys=True)
                out.write('\n')
        return value
    finally:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inspect', 'install'))
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--dotnet-root', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.download:
            fetch_archive(args.archive)
        if args.mode == 'inspect':
            value = {'kind':'devtool.dn03-runtime-archive-review',
                     'sha512': verify_archive(args.archive)}
        else:
            if args.dotnet_root is None or args.receipt is None:
                raise RuntimeOnlyError('install requires --dotnet-root and --receipt')
            value = install_archive(args.archive, args.dotnet_root, receipt=args.receipt)
    except (RuntimeOnlyError, OSError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print('DN03_RUNTIME_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
