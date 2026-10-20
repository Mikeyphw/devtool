"""A08 explicit local transfer of an already LIFE-verified, unqualified artifact.

No GitHub auth, remote actions, release publication, automatic overwrite, automatic
uninstall, or deployment without two independent consent flags. The source SHA
comes from an explicit external input, not a mutable branch/ref name.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any

from .actions_local_evidence import signed_local_evidence
from .github_actions import GitHubActionsError

_SHA40 = re.compile(r'[0-9a-f]{40}\Z')
_SERIAL = re.compile(r'[A-Za-z0-9._:-]{1,100}\Z')


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _stored_file(root: Path, registry_id: str, fmt: str) -> Path:
    # signed_local_evidence() verifies the complete registry/parent chain before
    # this is called. Re-derive its fixed layout, never take a path from a caller.
    directory = 'android-locally-signed' if fmt == 'apk' else 'android-aab-signed'
    from .artifact_registry import load_registry
    rows = load_registry(root).get('items', [])
    matches = [x for x in rows if isinstance(x, dict) and x.get('id') == registry_id]
    if len(matches) != 1 or not isinstance(matches[0].get('path'), str):
        raise GitHubActionsError('A08 verified signed registry record is unavailable')
    name = Path(matches[0]['path'])
    storage = root / '.devtool' / 'artifacts' / directory
    path = root / name
    if (name.is_absolute() or path.is_symlink() or storage.is_symlink() or
            path.resolve().parent != storage.resolve() or path.suffix != '.' + fmt):
        raise GitHubActionsError('A08 signed package path is unsafe')
    return path


def _destination(value: str, fmt: str) -> Path:
    p = Path(value).expanduser()
    if not value or not p.is_absolute() or p.suffix.lower() != '.' + fmt or not p.name or p.is_symlink():
        raise GitHubActionsError('A08 export needs an absolute new .apk/.aab destination')
    parent = p.parent
    if parent.is_symlink() or not parent.is_dir() or p.exists():
        raise GitHubActionsError('A08 export parent is unsafe or destination already exists')
    return p


def _export(package: Path, destination: Path, digest: str) -> None:
    # O_EXCL + O_NOFOLLOW means caller's existing file is never overwritten.
    # A failed copy removes only the inode we just created (never a preexisting path).
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output, package.open('rb') as source:
            fd = -1
            shutil.copyfileobj(source, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if _sha256(destination) != digest:
            raise GitHubActionsError('A08 export checksum differs from verified signed package')
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def private_handoff(root: Path, *, registry_id: str, fmt: str = 'apk', identity_id: str = '',
                    target: str = 'android_hud', expected_source_sha: str = '',
                    operation: str, destination: str = '', serial: str = '',
                    allow_unqualified: bool = False, yes: bool = False) -> dict[str, Any]:
    """Preview by default. Mutation requires both --yes and --allow-unqualified.

    AAB is export-only; installing requires an explicit connected adb serial and
    does not perform replace/uninstall/reboot or any test that claims device qualified.
    """
    root = Path(root).resolve()
    if not isinstance(expected_source_sha, str) or not _SHA40.fullmatch(expected_source_sha):
        raise GitHubActionsError('A08 requires an independent exact 40-hex --expected-sha')
    if operation not in ('export', 'install') or fmt not in ('apk', 'aab'):
        raise GitHubActionsError('A08 handoff operation/format is unsupported')
    if operation == 'install':
        if fmt != 'apk' or destination or not _SERIAL.fullmatch(serial or ''):
            raise GitHubActionsError('A08 install requires only APK and an exact --serial')
    elif serial:
        raise GitHubActionsError('A08 export never accepts a device serial')
    output = _destination(destination, fmt) if operation == 'export' else None
    proof = signed_local_evidence(root, registry_id=registry_id, fmt=fmt,
                                  identity_id=identity_id, target=target,
                                  expected_source_sha=expected_source_sha)
    digest = proof['signed_sha256']
    result = {**proof, 'phase':'ACTIONS-A08', 'operation': operation,
              'status':'signed-but-unqualified', 'consent_required':['--yes','--allow-unqualified'],
              'requested_destination':str(output) if output else None,
              'requested_serial':serial if operation == 'install' else None,
              'executed':False, 'device_qualified':False, 'published':False,
              'source_attested':False, 'qualified_release':False}
    if not yes:
        return result  # NEVER mutate in a preview, even if allow flag is set.
    if not allow_unqualified:
        raise GitHubActionsError('A08 requires explicit --allow-unqualified before local mutation')
    package = _stored_file(root, registry_id, fmt)
    with tempfile.TemporaryDirectory(prefix='devtool-a08-local-') as d:
        frozen = Path(d) / ('verified.' + fmt)
        with package.open('rb') as source, frozen.open('xb') as target_file:
            os.fchmod(target_file.fileno(), 0o600)
            shutil.copyfileobj(source, target_file, 1024 * 1024)
        if _sha256(frozen) != digest or _sha256(package) != digest:
            raise GitHubActionsError('A08 signed package changed before mutation')
        if operation == 'export':
            assert output is not None
            _export(frozen, output, digest)
            result['destination'] = str(output)
        else:
            adb = shutil.which('adb')
            if not adb:
                raise GitHubActionsError('A08 installation requires an installed adb executable')
            try:
                check = subprocess.run([adb,'-s',serial,'get-state'],capture_output=True,
                                       text=True,timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                raise GitHubActionsError('A08 adb device preflight unavailable') from None
            if check.returncode != 0 or check.stdout.strip() != 'device':
                raise GitHubActionsError('A08 exact selected device is unavailable or unauthorized')
            try:
                install = subprocess.run([adb,'-s',serial,'install',str(frozen)],
                                         capture_output=True,text=True,timeout=180)
            except (OSError, subprocess.TimeoutExpired):
                raise GitHubActionsError('A08 adb install failed or timed out; device state is not qualified') from None
            if install.returncode != 0 or 'Success' not in install.stdout.splitlines():
                raise GitHubActionsError('A08 adb install did not report success; device state is not qualified')
            result['device_install_reported'] = True  # NOT application launch/device qualification.
        result.update(executed=True,consent_recorded=True)
    return result
