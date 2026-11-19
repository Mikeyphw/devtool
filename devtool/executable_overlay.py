"""Manifest-declared executable overlay profile; never a second apply engine.

The bootstrap belongs to the package, not to the destination repository.  A
normal ZIP is unaffected: its ``__main__.py`` is ordinary source content.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any, Sequence

PROFILE_V1 = 'devtool.executable-overlay.v1'
PROFILE = 'devtool.executable-overlay.v2'
SUPPORTED_PROFILES = frozenset({PROFILE_V1, PROFILE})
# This bootstrap is a native CLI adapter, not an overlay/transaction engine.
BOOTSTRAP_V1 = r"""#!/usr/bin/env python3
\"\"\"Devtool verified executable-overlay adapter (v1).\"\"\"
# sys is built-in. Remove the untrusted ZIP from import search *before* any
# pure-Python imports, or payload modules could shadow argparse/subprocess.
import sys
_PACKAGE = sys.argv[0]
if sys.path and sys.path[0] == _PACKAGE:
    sys.path.pop(0)
import argparse
import os
from pathlib import Path
import subprocess

def main():
    p = argparse.ArgumentParser(description='Verified Devtool executable overlay adapter')
    p.add_argument('operation', choices=('check', 'apply'), nargs='?', default='check')
    p.add_argument('--repo', required=True, help='Exact named target Git worktree')
    p.add_argument('--yes', action='store_true', help='Explicitly authorize native apply')
    args = p.parse_args()
    if args.operation == 'apply' and not args.yes:
        p.error('native apply requires explicit --yes')
    artifact = Path(sys.argv[0]).expanduser().resolve(strict=True)
    repo = Path(args.repo).expanduser().resolve(strict=True)
    env = dict(os.environ)
    env['DEVTOOL_TEXTUAL_APPLY_HOST'] = '0'
    env['DEVTOOL_TEXTUAL_EXECUTION_HOST'] = '0'
    base = [sys.executable, '-B', '-m', 'devtool', '--no-clipboard', '--repo', str(repo)]
    for command in (('validate-overlay', str(artifact)),
                    ('overlay', 'check', str(artifact))):
        done = subprocess.run([*base, *command], env=env, cwd=str(repo), check=False)
        if done.returncode:
            return done.returncode
    if args.operation == 'check':
        return 0
    done = subprocess.run([*base, '--yes', 'apply-overlay', '--validate', str(artifact)],
                          env=env, cwd=str(repo), check=False)
    return done.returncode

if __name__ == '__main__':
    raise SystemExit(main())
""".replace('\\"', '"').encode('utf-8')


# Immutable v1 bootstrap remains valid for already published source-bound PYZs.
# V2 adds native adoption after Devtool reports a successful commit, so a
# single explicitly approved invocation also creates/reuses the durable session.
_V1_TAIL = """    done = subprocess.run([*base, '--yes', 'apply-overlay', '--validate', str(artifact)],
                          env=env, cwd=str(repo), check=False)
    return done.returncode"""
_V2_TAIL = """    done = subprocess.run([*base, '--yes', 'apply-overlay', '--validate', str(artifact)],
                          env=env, cwd=str(repo), check=False)
    if done.returncode:
        return done.returncode
    adopted = subprocess.run([sys.executable, '-B', '-m',
                              'devtool.native_overlay_delivery', 'adopt-committed',
                              '--repo', str(repo), '--overlay', str(artifact)],
                             env=env, cwd=str(repo), check=False)
    return adopted.returncode"""
assert _V1_TAIL.encode() in BOOTSTRAP_V1
BOOTSTRAP_V2 = BOOTSTRAP_V1.replace(_V1_TAIL.encode(), _V2_TAIL.encode())


def canonical_bootstrap_source(profile: str = PROFILE) -> bytes:
    """Return checked byte-exact bootstrap for this version; never rewrite v1."""
    if profile == PROFILE_V1:
        return BOOTSTRAP_V1
    if profile == PROFILE:
        return BOOTSTRAP_V2
    raise ValueError('Unsupported executable overlay bootstrap version')

MAX_MEMBERS = 512
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_RATIO = 200
_HASH = re.compile(r'[0-9a-f]{64}\Z')



_SHA40 = re.compile(r'[0-9a-f]{40}\Z')


def _require_v2_source_contract(manifest: dict[str, Any]) -> None:
    """Require exact source, target and changed-file identity on new PYZs."""
    if (manifest.get('schema_version') not in (2, 3)
            or manifest.get('base_contract_mode') != 'exact-tree'
            or any(not isinstance(manifest.get(k), str) or not _SHA40.fullmatch(manifest[k])
                   for k in ('base_commit', 'base_tree'))):
        raise ValueError('Executable v2 requires exact-tree base commit and tree')
    repo = manifest.get('repository')
    if (not isinstance(repo, dict)
            or repo.get('enforce_remote') is not True
            or repo.get('enforce_branch') is not True
            or not isinstance(repo.get('remote'), str) or not repo['remote'].strip()
            or not isinstance(repo.get('branch'), str) or not repo['branch'].strip()):
        raise ValueError('Executable v2 requires verified origin and named branch')
    inv = manifest.get('inventory')
    if not isinstance(inv, dict) or inv.get('format') != 1 or not isinstance(inv.get('entries'), list):
        raise ValueError('Executable v2 requires a complete format-1 inventory')
    inventory = inv['entries']
    paths: list[str] = []
    expected: dict[str, str] = {}
    before: dict[str, str | None] = {}
    for row in inventory:
        if not isinstance(row, dict) or row.get('action') not in {'add', 'modify', 'delete'}:
            raise ValueError('Executable v2 inventory has unsupported action')
        name = _path(row.get('path'))
        if name in paths:
            raise ValueError('Executable v2 inventory contains duplicate payload path')
        paths.append(name)
        action = row['action']
        if action in {'modify','delete'}:
            digest = row.get('source_sha256')
            if not isinstance(digest, str) or not _HASH.fullmatch(digest):
                raise ValueError('Executable v2 requires source blob hash for modify/delete')
            before[name] = digest
        else:
            before[name] = None
        if action != 'delete':
            digest = row.get('sha256')
            if not isinstance(digest, str) or not _HASH.fullmatch(digest):
                raise ValueError('Executable v2 requires post hash for all payload files')
            expected[name] = digest
    if (manifest.get('touched_paths') != sorted(paths)
            or manifest.get('source_sha256') != before
            or manifest.get('expected_post_sha256') != expected):
        raise ValueError('Executable v2 source/post hash inventory is not exact')


@dataclass(frozen=True, slots=True)
class ExecutableMemberMap:
    """One verified classification reused by preflight, preview and apply."""

    executable: bool
    metadata: frozenset[str]
    payload: frozenset[str]
    profile: str = ''


def _path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or '\\' in raw or '\x00' in raw:
        raise ValueError('Executable bootstrap path must be a canonical relative string')
    p = PurePosixPath(raw)
    if p.is_absolute() or raw != p.as_posix() or any(s in ('.', '..', '') for s in raw.split('/')):
        raise ValueError('Executable bootstrap path is noncanonical or unsafe')
    return raw


def _metadata_path(raw: str) -> bool:
    return raw in {'__main__.py', 'manifest.json'} or raw.startswith('bootstrap/')


def classify(artifact: str | Path, manifest: dict[str, Any], members: Sequence[Any], *,
             allowed_metadata_payloads: frozenset[str] = frozenset()) -> ExecutableMemberMap:
    """Fail closed for an explicitly executable overlay; never infer a profile.

    Archive path verification, duplicate names and unsafe filesystem member
    kinds are supplied by ``artifacts.list_archive_members``.  This function
    independently verifies the claimed executable metadata and payload split.
    """
    suffix = Path(artifact).suffix.lower()
    profile_present = 'executable_overlay' in manifest
    spec = manifest.get('executable_overlay')
    if not profile_present:
        if suffix == '.pyz':
            raise ValueError('PYZ requires explicit devtool executable_overlay profile')
        return ExecutableMemberMap(False, frozenset(), frozenset(
            item.name for item in members if not item.is_dir
        ))
    if not isinstance(spec, dict) or set(spec) != {'profile', 'bootstrap'}:
        raise ValueError('Invalid executable_overlay profile declaration')
    if spec['profile'] not in SUPPORTED_PROFILES or suffix not in {'.pyz', '.zip'}:
        raise ValueError('Unsupported executable_overlay profile or archive type')
    if str(manifest.get('type') or '').lower() != 'overlay':
        raise ValueError('Executable overlay requires an explicit overlay manifest type')
    # The v2 executable profile is source-bound by construction. Legacy v1
    # already published packages retain their original verification contract;
    # never rewrite their bootstrap bytes or silently repin their source.
    if spec['profile'] == PROFILE:
        _require_v2_source_contract(manifest)
    raw_descriptors = spec['bootstrap']
    if not isinstance(raw_descriptors, list) or not 1 <= len(raw_descriptors) <= 32:
        raise ValueError('Executable bootstrap must declare 1–32 checked members')
    files = {item.name: item for item in members if not item.is_dir}
    if len(files) > MAX_MEMBERS:
        raise ValueError('Executable overlay exceeds member count limit')
    declared: set[str] = set()
    for descriptor in raw_descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != {'path', 'sha256', 'size'}:
            raise ValueError('Executable bootstrap descriptor shape is invalid')
        path = _path(descriptor['path'])
        if not _metadata_path(path):
            raise ValueError(f'Executable bootstrap outside reserved namespace: {path}')
        if path in declared:
            raise ValueError(f'Duplicate executable bootstrap declaration: {path}')
        declared.add(path)
        digest, size = descriptor['sha256'], descriptor['size']
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError(f'Invalid executable bootstrap SHA-256: {path}')
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError(f'Invalid executable bootstrap size: {path}')
        member = files.get(path)
        if member is None or member.data is None:
            raise ValueError(f'Executable bootstrap missing: {path}')
        if len(member.data) != size or hashlib.sha256(member.data).hexdigest() != digest:
            raise ValueError(f'Executable bootstrap hash or size mismatch: {path}')
    if '__main__.py' not in declared:
        raise ValueError('Executable overlay must declare checked __main__.py')
    if files['__main__.py'].data != canonical_bootstrap_source(spec['profile']):
        raise ValueError('Executable overlay entrypoint is not the canonical thin Devtool native adapter')
    undeclared = sorted(path for path in files if _metadata_path(path) and path not in declared)
    if undeclared:
        raise ValueError('Undeclared executable bootstrap member(s): ' + ', '.join(undeclared[:8]))
    inventory = manifest.get('inventory')
    if not isinstance(inventory, dict) or not isinstance(inventory.get('entries'), list):
        raise ValueError('Executable overlay requires complete payload inventory')
    declared_payload = {
        str(x.get('path') or '') for x in inventory['entries']
        if isinstance(x, dict) and str(x.get('action') or '').lower() != 'delete'
    }
    real_payload = {
        path for path in files
        if path not in declared and (
            path in allowed_metadata_payloads
            or (path not in {'.devtool-artifact.json',
                            'artifact.devtool.json', 'devtool-artifact.json',
                            '.devtool-delete'}
                and not path.startswith(('.devtool-artifact/', '.devtool/')))
        )
    }
    if real_payload != declared_payload:
        raise ValueError('Executable overlay payload inventory must match exactly: '
                         + ', '.join(sorted(real_payload ^ declared_payload)[:8]))
    if any(path in declared for path in manifest.get('files', []) if isinstance(path, str)):
        raise ValueError('Executable bootstrap cannot be declared as repository file')
    conflicts = sorted(declared & (declared_payload | set(allowed_metadata_payloads)))
    if conflicts:
        raise ValueError('Executable bootstrap conflicts with repository inventory: ' + ', '.join(conflicts))
    total = 0
    for item in files.values():
        if item.data is None:
            raise ValueError(f'Missing archive member bytes: {item.name}')
        if len(item.data) > MAX_FILE_BYTES:
            raise ValueError(f'Executable overlay member exceeds size limit: {item.name}')
        total += len(item.data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Executable overlay exceeds uncompressed size limit')
    # Bootstrap-only directory markers must not become empty repository dirs.
    metadata_with_dirs = set(declared)
    for item in members:
        if item.is_dir and any(path.startswith(item.name.rstrip('/') + '/') for path in declared):
            metadata_with_dirs.add(item.name)
    return ExecutableMemberMap(True, frozenset(metadata_with_dirs), frozenset(files.keys() - declared), spec['profile'])
