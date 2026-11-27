"""Tree-equivalent CI evidence identities, not a commit or release attestation.

Only an independently verified historical run may become a reuse candidate.
The stored index is a locator, never proof; the caller revalidates remote run
and artifacts. Missing history, ambiguous index, or changed tree means no reuse.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from .actions_dispatch import _durable_record
from .github_actions import GitHubActionsError, _slug
from .xdg import state_dir

SHA = re.compile(r'[a-f0-9]{40}\Z')
SCHEMA = 'devtool.ci24-tree-locator.v1'


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
    try:
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args], env=env, text=True,
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubActionsError('CI tree identity unavailable') from exc
    if result.returncode:
        raise GitHubActionsError('CI tree identity not established')
    return result.stdout.strip()


def tree(root: Path, sha: str) -> str:
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubActionsError('CI tree commit identity invalid')
    value = git(root, 'rev-parse', '--verify', sha+'^{tree}')
    if not SHA.fullmatch(value):
        raise GitHubActionsError('CI tree identity invalid')
    return value


def equivalent(root: Path, current: str, tested: str, expected_tree: str) -> bool:
    """Require equal bytes AND genuine ancestry (no squash-history inference)."""
    if current == tested or not isinstance(expected_tree, str) or not SHA.fullmatch(expected_tree):
        return False
    try:
        # History-dependent CI must not trust locally rewritten or truncated ancestry.
        for name in ('info/grafts', 'shallow'):
            path = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', name))
            if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size)):
                return False
        return (tree(root, current) == tree(root, tested) == expected_tree
                and git(root, 'merge-base', tested, current) == tested)
    except (GitHubActionsError, OSError, subprocess.TimeoutExpired):
        return False


def _folder(slug: str, identity: str) -> Path:
    slug = _slug(slug).casefold()
    if not isinstance(identity, str) or not SHA.fullmatch(identity):
        raise GitHubActionsError('Invalid CI tree index identity')
    root = state_dir() / 'ci24-tree-evidence'
    folder = root / hashlib.sha256(slug.encode()).hexdigest() / identity
    for path in (root, folder.parent, folder):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise GitHubActionsError('Unsafe CI tree index directory')
    return folder


def _valid(row: Any, slug: str, identity: str) -> bool:
    return (isinstance(row, dict) and set(row) == {'schema','repository','source_sha',
             'source_tree','branch','workflow_id','run_id','run_attempt'}
            and row['schema'] == SCHEMA and row['repository'] == slug.casefold()
            and row['source_tree'] == identity
            and isinstance(row['source_sha'], str) and SHA.fullmatch(row['source_sha']) is not None
            and isinstance(row['branch'], str) and re.fullmatch(r'[A-Za-z0-9._/-]{1,128}', row['branch']) is not None
            and '..' not in row['branch'] and type(row['workflow_id']) is int and row['workflow_id'] > 0
            and type(row['run_id']) is int and row['run_id'] > 0
            and type(row['run_attempt']) is int and row['run_attempt'] == 1)


def remember(root: Path, record: dict[str, Any]) -> None:
    """Called only after the native 25-archive verifier has returned success."""
    v = record.get('verification') or {}
    if (record.get('state') != 'PASS_CI24_VERIFIED' or v.get('complete') is not True
            or v.get('run_attempt') != 1 or v.get('shards_verified') != 24
            or v.get('jobs_verified') != 25 or v.get('artifact_archives_verified') != 25):
        raise GitHubActionsError('Refusing incomplete CI tree evidence locator')
    slug = _slug(record['repository'])
    identity = tree(root, record['source_sha'])
    row = dict(schema=SCHEMA, repository=slug.casefold(), source_sha=record['source_sha'],
               source_tree=identity, branch=record['branch'], workflow_id=record['workflow_id'],
               run_id=record['run_id'], run_attempt=v['run_attempt'])
    if not _valid(row, slug, identity):
        raise GitHubActionsError('Invalid CI tree evidence locator')
    folder = _folder(slug, identity)
    for path in (folder.parent.parent, folder.parent, folder):
        path.mkdir(mode=0o700, exist_ok=True, parents=True)
    folder = _folder(slug, identity)
    target = folder / f"{row['source_sha']}-{row['run_id']}.json"
    if target.is_symlink():
        raise GitHubActionsError('Unsafe CI tree index record')
    _durable_record(target, row)


def candidates(root: Path, slug: str, current: str, identity: str, workflow_id: int) -> list[dict[str, Any]]:
    folder = _folder(slug, identity)
    if not folder.exists():
        return []
    paths = sorted(folder.iterdir())
    if len(paths) > 128:
        raise GitHubActionsError('CI tree index inventory exceeds bound')
    out = []
    for path in paths:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 4096):
            raise GitHubActionsError('Unsafe CI tree index record')
        row = json.loads(path.read_text(encoding='utf-8'))
        if (not _valid(row, slug, identity)
                or path.name != f"{row['source_sha']}-{row['run_id']}.json"):
            raise GitHubActionsError('Invalid CI tree index record')
        if row['workflow_id'] == workflow_id and equivalent(root, current, row['source_sha'], identity):
            out.append(row)
    return out
