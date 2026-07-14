#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description='Verify two release artifacts are byte-for-byte identical.')
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    args = parser.parse_args()
    for path in (args.first, args.second):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f'Artifact is missing or empty: {path}')
    first_hash = digest(args.first)
    second_hash = digest(args.second)
    if first_hash != second_hash or args.first.read_bytes() != args.second.read_bytes():
        raise SystemExit(f'Artifacts differ:\n{first_hash}  {args.first}\n{second_hash}  {args.second}')
    print(f'{first_hash}  identical')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
