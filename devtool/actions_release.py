"""ACTIONS-08: fail-closed Android release qualification.

This is a verification boundary, not a local signature or release publisher.
The independent user-owned policy must live outside the source repository.
Qualified results require a live successful GitHub workflow_dispatch run,
ACTIONS-04 archive integrity, ACTIONS-05 Android signature/manifest checks,
and a cryptographically verified GitHub provenance attestation of the exact APK/AAB.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from typing import Any

from .actions_android import android_collect
from .actions_artifacts import verify_download
from .actions_runs import _normalize_run
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug

_SHA40 = re.compile(r'[a-f0-9]{40}\Z')
_SHA64 = re.compile(r'[a-f0-9]{64}\Z')
_PKG = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z')
_WORKFLOW = '.github/workflows/devtool-android-build.yml'
_POLICY_FIELDS = {'schema', 'repository', 'workflow', 'ref', 'source_sha', 'application_id',
                  'signer_sha256', 'format', 'minimum_version_code'}


def release_policy(root: Path, filename: str) -> dict[str, Any]:
    """Read user-protected trust anchors, never a policy from the checked-out commit."""
    path = Path(filename).expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise GitHubActionsError('Release policy must be an absolute, non-symlink path outside the repository')
    resolved = path.resolve()
    if resolved == root.resolve() or resolved.is_relative_to(root.resolve()):
        raise GitHubActionsError('Release trust policy cannot be stored inside the source repository')
    # Inspect the opened inode instead of stat-then-open to reject symlink races.
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077
                or not 1 <= st.st_size <= 16 * 1024):
            raise GitHubActionsError('Release trust policy must be a private (0600), owned, regular file <=16 KiB')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            contents = stream.read(16 * 1024 + 1)
        if len(contents) != st.st_size:
            raise GitHubActionsError('Release trust policy changed during verification')
    except OSError:
        raise GitHubActionsError('Release trust policy is missing or unsafe') from None
    finally:
        if fd is not None:
            os.close(fd)
    try:
        obj = json.loads(contents)
    except (ValueError, UnicodeError):
        raise GitHubActionsError('Release trust policy JSON is invalid') from None
    if not isinstance(obj, dict) or set(obj) != _POLICY_FIELDS or type(obj.get('schema')) is not int or obj['schema'] != 1:
        raise GitHubActionsError('Release trust policy fields/schema are invalid')
    if not isinstance(obj['repository'], str):
        raise GitHubActionsError('Release repository identity is invalid')
    obj['repository'] = _slug(obj['repository'])
    if obj['workflow'] != _WORKFLOW:
        raise GitHubActionsError('Release policy must pin the supported workflow path')
    if (not isinstance(obj['ref'], str) or
        not re.fullmatch(r'refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9_./-]{0,180}', obj['ref']) or
        '..' in obj['ref'] or obj['ref'].endswith('/') or '//' in obj['ref']):
        raise GitHubActionsError('Release policy must pin one safe full Git ref')
    if not isinstance(obj['source_sha'], str) or not _SHA40.fullmatch(obj['source_sha']):
        raise GitHubActionsError('Release policy requires an exact 40-hex source SHA')
    if not isinstance(obj['signer_sha256'], str) or not _SHA64.fullmatch(obj['signer_sha256']):
        raise GitHubActionsError('Release policy requires a pinned signing certificate SHA-256')
    if not isinstance(obj['application_id'], str) or not _PKG.fullmatch(obj['application_id']):
        raise GitHubActionsError('Release policy application ID is invalid')
    if obj['format'] not in ('apk', 'aab'):
        raise GitHubActionsError('Release policy package format is invalid')
    if type(obj['minimum_version_code']) is not int or obj['minimum_version_code'] < 1:
        raise GitHubActionsError('Release policy minimum version code must be positive')
    obj['_policy_sha256'] = hashlib.sha256(contents).hexdigest()
    return obj


def _attestation(package_file: Path, policy: dict[str, Any]) -> dict[str, Any]:
    gh = shutil.which('gh')
    if not gh:
        raise GitHubActionsError('Release provenance requires authenticated gh attestation verify')
    cmd = [gh, 'attestation', 'verify', str(package_file),
           '--repo', policy['repository'],
           '--signer-workflow', f"{policy['repository']}/{policy['workflow']}",
           '--source-digest', policy['source_sha'], '--source-ref', policy['ref'],
           '--predicate-type', 'https://slsa.dev/provenance/v1',
           '--deny-self-hosted-runners', '--format', 'json']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('Release provenance verification tool failed or timed out') from None
    if proc.returncode != 0:
        raise GitHubActionsError('Release provenance attestation could not be verified for exact source/workflow/ref')
    # Successful gh verification is the cryptographic authority; require an actual
    # result, not an empty response misinterpreted as success.
    try:
        statements = json.loads(proc.stdout)
    except (ValueError, UnicodeError):
        raise GitHubActionsError('Release provenance verifier produced invalid JSON') from None
    if not isinstance(statements, list) or not statements:
        raise GitHubActionsError('Release provenance verifier returned no signed attestations')
    return {'verified': True, 'verifier': 'gh attestation verify', 'predicate_type': 'https://slsa.dev/provenance/v1',
            'signer_workflow': f"{policy['repository']}/{policy['workflow']}",
            'source_sha': policy['source_sha'], 'source_ref': policy['ref'],
            'attestations': len(statements)}


def release_verify(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
                   artifact_id: int, policy_file: str, member: str = '') -> dict[str, Any]:
    """Return qualified_release=True only after every independent gate succeeds.

    Read-only: no remote mutation, registry promotion, downloads or overwrites.
    An ACTIONS-04 registered archive must already exist and pass live verification.
    """
    policy = release_policy(root, policy_file)
    slug = _slug(slug)
    if slug.lower() != policy['repository'].lower():
        raise GitHubActionsError('Release policy repository differs from GitHub origin')
    remote = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    if (not remote['completed'] or remote['conclusion'] != 'success' or
            remote.get('event') != 'workflow_dispatch'):
        raise GitHubActionsError('Release qualification requires completed successful workflow_dispatch')
    if (remote.get('head_sha') or '').lower() != policy['source_sha']:
        raise GitHubActionsError('Release workflow source SHA differs from independent policy')
    ref = policy['ref']
    if ref.startswith('refs/heads/') and remote.get('head_branch') != ref[len('refs/heads/'):]:
        raise GitHubActionsError('Release workflow branch differs from independent policy')
    selected = client.workflow(slug, policy['workflow'])
    if remote.get('workflow_id') != selected.get('id'):
        raise GitHubActionsError('Release workflow identity differs from pinned policy')
    checked = android_collect(client, slug, root, run_id=run_id, artifact_id=artifact_id,
                              fmt=policy['format'], variant='release',
                              expected_package=policy['application_id'], expected_cert=policy['signer_sha256'],
                              member=member, materialize=False, workflow=policy['workflow'],
                              expected_source_sha=policy['source_sha'],
                              expected_run_attempt=remote['run_attempt'])
    pkg = checked['android']
    if pkg.get('debug_signing') is not False:
        raise GitHubActionsError('Release package signing identity cannot be Android Debug or unknown')
    try:
        code = int(pkg['version_code'])
    except (ValueError, TypeError, KeyError):
        raise GitHubActionsError('Release package versionCode must be independently verified') from None
    if code < policy['minimum_version_code']:
        raise GitHubActionsError('Release package versionCode is below policy minimum')
    verified = verify_download(client, slug, run_id, artifact_id, root)
    source_archive = Path(verified['file'])
    with tempfile.TemporaryDirectory(prefix='devtool-release-verify-') as tmp:
        output = Path(tmp) / ('release.' + policy['format'])
        digest = hashlib.sha256()
        count = 0
        with zipfile.ZipFile(source_archive) as archive, archive.open(pkg['member']) as src, output.open('xb') as dest:
            os.fchmod(dest.fileno(), 0o600)
            while chunk := src.read(1024 * 1024):
                count += len(chunk)
                if count > 256 * 1024 * 1024:
                    raise GitHubActionsError('Release package exceeds inspection limit')
                digest.update(chunk)
                dest.write(chunk)
        if digest.hexdigest() != pkg['sha256'] or count != pkg['size']:
            raise GitHubActionsError('Release package bytes changed since Android signature inspection')
        provenance = _attestation(output, policy)
    return {'phase': 'ACTIONS-08', 'repository': slug, 'run_id': run_id,
            'artifact_id': artifact_id, 'source_sha': policy['source_sha'],
            'workflow': policy['workflow'], 'release': pkg,
            'archive_sha256': verified['sha256'], 'provenance': provenance,
            'policy_sha256': policy['_policy_sha256'],
            'qualified_release': True, 'scope': 'verified-package-provenance; no publishing or deployment',
            'read_only': True}
