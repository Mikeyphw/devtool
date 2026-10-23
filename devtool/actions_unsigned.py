"""ACTIONS-10: GitHub unsigned release candidate, immutable receipt, native LIFE signing.

No CI candidate is a qualified release. All mutations require explicit caller consent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import zipfile
from typing import Any

from .actions_android import (_git, _gradle_bootstrap, _gradle_modules, _project_root,
                              _tool, _run, _SHA40, _MAX_PACKAGE)
from .actions_dispatch import dispatch_workflow
from .actions_artifacts import download_artifact, verify_download
from .actions_runs import _normalize_run
from .artifact_registry import load_registry, register_paths
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug
from .actions_android_contracts import select_consumer, check_packaging, contract_sha256, check_native_library_pages

WORKFLOW = 'devtool-android-unsigned-release.yml'
WORKFLOW_PATH = '.github/workflows/' + WORKFLOW
ARTIFACT_NAME = 'devtool-android-unsigned-release'
APK_NAME = 'unsigned-release.apk'
MANIFEST_NAME = 'manifest.json'
_EXPECTED_APPLICATION = 'io.github.mikeceead.devtoolhud'
_MAX_MANIFEST = 16 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_BADGING = re.compile(r"^package:\s+name='([^']+)'\s+versionCode='([0-9]+)'\s+versionName='([^']+)'", re.M)


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _publish_exclusive(source: Path, destination: Path, expected_sha256: str) -> None:
    """Publish complete verified bytes without hard links or replacement.

    Android/Termux Python builds may not expose os.link. O_EXCL provides a
    portable destination no-clobber guarantee; on any failure the incomplete
    destination is removed. The file is NOT registered until this returns.
    """
    if not _SHA.fullmatch(expected_sha256):
        raise GitHubActionsError('Published APK requires an exact SHA-256 digest')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(destination, flags, 0o600)
    except FileExistsError:
        raise GitHubActionsError('APK destination already exists; refusing overwrite') from None
    except OSError:
        raise GitHubActionsError('Unable to create exclusive APK destination') from None
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, 'wb') as sink:
            with source.open('rb') as inp:
                while block := inp.read(1024 * 1024):
                    sink.write(block)
                    digest.update(block)
            sink.flush()
            os.fsync(sink.fileno())
        if digest.hexdigest() != expected_sha256 or _hash(destination) != expected_sha256:
            raise GitHubActionsError('Published APK checksum differs from verified source')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _regular(path: Path, max_size: int) -> int:
    if path.is_symlink():
        raise GitHubActionsError('Unsigned candidate must not be a symlink')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > max_size:
                raise GitHubActionsError('Unsigned candidate file has invalid type or size')
            return info.st_size
        finally:
            os.close(fd)
    except OSError:
        raise GitHubActionsError('Cannot access a regular unsigned candidate file') from None


def _no_apk_signatures(apk: Path) -> None:
    """Reject JAR signatures and APK signing blocks before calling apksigner.

    Do not interpret 'apksigner verify failed' alone as proof of unsignedness:
    damaged signatures would also fail verification.
    """
    try:
        with zipfile.ZipFile(apk) as z:
            entries = z.infolist()
            names = [item.filename for item in entries]
            if not names:
                raise GitHubActionsError('Unsigned APK ZIP has no members')
            # ZIP and Android asset/resource paths are case-sensitive. A global
            # casefold() uniqueness requirement rejects legitimate AGP output
            # when two distinct files differ only in case. Exact duplicate
            # entries, however, have ambiguous lookup semantics and remain
            # unconditionally forbidden.
            seen_exact: set[str] = set()
            seen_sensitive: dict[str, str] = {}
            for info in entries:
                name = info.filename
                parts = name.rstrip('/').split('/')
                if (not name or name.startswith('/') or '\\' in name or ':' in name
                    or any(part in ('', '.', '..') for part in parts)):
                    raise GitHubActionsError('Unsigned APK ZIP contains an unsafe member path')
                if name in seen_exact:
                    raise GitHubActionsError('Unsigned APK ZIP has duplicate exact member names')
                seen_exact.add(name)
                # Entries that Android signing and package identity consumers
                # inspect must remain unique even under case-insensitive lookup.
                folded = name.casefold()
                critical = (folded == 'androidmanifest.xml'
                            or folded == 'resources.arsc'
                            or folded.startswith('meta-inf/')
                            or re.fullmatch(r'classes[0-9]*\.dex', folded) is not None)
                if critical and folded in seen_sensitive:
                    raise GitHubActionsError('Unsigned APK ZIP has ambiguous security-sensitive member names')
                if critical:
                    seen_sensitive[folded] = name
                # APK payloads must not contain symlink-shaped members, even
                # though no ZIP member is extracted on the runner.
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise GitHubActionsError('Unsigned APK ZIP contains a symlink member')
            if 'AndroidManifest.xml' not in seen_exact:
                raise GitHubActionsError('Unsigned APK lacks AndroidManifest.xml')
            for name in names:
                upper = name.upper()
                if (upper.startswith('META-INF/') and upper.count('/') == 1 and
                    (upper.endswith(('.SF', '.RSA', '.DSA', '.EC')) or
                     upper.split('/')[-1].startswith('SIG-'))):
                    raise GitHubActionsError('APK contains a JAR signing signature')
        # APK Signing Block v2+ footer is 24 bytes before Central Directory.
        with apk.open('rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65557))
            tail = f.read()
            offset = tail.rfind(b'PK\x05\x06')
            if offset < 0 or len(tail) - offset < 22:
                raise GitHubActionsError('APK ZIP central directory cannot be inspected')
            end = tail[offset:]
            if end[4:8] != b'\x00\x00\x00\x00':
                raise GitHubActionsError('Multi-disk APK ZIP is unsupported')
            central = struct.unpack_from('<I', end, 16)[0]
            if central == 0xffffffff or central > size:
                raise GitHubActionsError('APK ZIP64/central directory is unsupported')
            if central >= 24:
                f.seek(central - 16)
                if f.read(16) == b'APK Sig Block 42':
                    raise GitHubActionsError('APK contains a v2/v3 signing block')
    except (OSError, zipfile.BadZipFile, ValueError, struct.error):
        raise GitHubActionsError('Unsigned APK is not a valid inspectable archive') from None


def inspect_unsigned(apk: Path, *, expected_application: str = _EXPECTED_APPLICATION) -> dict[str, Any]:
    """Native Android tools establish release identity and lack of signature."""
    apk = Path(apk)
    size = _regular(apk, _MAX_PACKAGE)
    _no_apk_signatures(apk)
    native_pages = check_native_library_pages(apk)
    try:
        signer = subprocess.run([_tool('apksigner'), 'verify', str(apk)],
                                capture_output=True, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('apksigner is required to validate unsigned candidate') from None
    if signer.returncode == 0:
        raise GitHubActionsError('Expected unsigned APK, but Android signature verification passed')
    try:
        dump = _run([_tool('aapt'), 'dump', 'badging', str(apk)],
                    purpose='Unsigned APK manifest inspection')
    except GitHubActionsError:
        raise  # never accept an APK without independent Android badging
    match = _BADGING.search(dump)
    if not match or match.group(1) != expected_application:
        raise GitHubActionsError('Unsigned candidate package identity does not match registered Android application')
    if re.search(r'^application-debuggable\b', dump, re.M):
        raise GitHubActionsError('Unsigned release candidate must not be debuggable')
    if int(match.group(2)) < 1:
        raise GitHubActionsError('Unsigned release APK has invalid versionCode')
    return {'package': match.group(1), 'version_code': int(match.group(2)),
            'version_name': match.group(3), 'debuggable': False, 'signing_state': 'unsigned',
            'sha256': _hash(apk), 'size_bytes': size,
            'qualified_release': False, 'signature_verified': False,
            'native_page_alignment': native_pages}


def _inspect_consumer(apk: Path, consumer: dict) -> dict[str, Any]:
    if consumer['id'] == 'devtool-hud':
        return inspect_unsigned(apk)
    return inspect_unsigned(apk, expected_application=consumer['application_id'])


def emit_manifest(apk: Path, output: Path, env: dict[str, str], *, consumer: dict | None = None) -> dict[str, Any]:
    """Run on GitHub runner only, never claim attestation/qualification."""
    if consumer is None:
        try:
            consumer = select_consumer(Path.cwd(), consumer_id=env.get('DEVTOOL_ANDROID_CONSUMER_ID', ''), repository=env.get('GITHUB_REPOSITORY', ''))
        except GitHubActionsError:
            raise GitHubActionsError('GitHub workflow environment has invalid source identity') from None
    # Caller-supplied consumer objects are never authority: resolve the exact
    # registered identity from tracked local policy before signing/attesting.
    consumer_id = consumer.get('id', '') if isinstance(consumer, dict) else ''
    reviewed = select_consumer(Path.cwd(), consumer_id=consumer_id or env.get('DEVTOOL_ANDROID_CONSUMER_ID', ''), repository=env.get('GITHUB_REPOSITORY', ''))
    if consumer is not None and contract_sha256(consumer) != contract_sha256(reviewed):
        raise GitHubActionsError('Unsigned producer supplied unregistered Android consumer contract')
    consumer = reviewed
    details = _inspect_consumer(apk, consumer)
    check_packaging(apk, consumer)
    sha = env.get('GITHUB_SHA', '')
    repo = env.get('GITHUB_REPOSITORY', '')
    if repo.casefold() != consumer['repository'].casefold() or not _SHA40.fullmatch(sha):
        raise GitHubActionsError('GitHub workflow environment has invalid source identity')
    try:
        run = int(env['GITHUB_RUN_ID']); attempt = int(env['GITHUB_RUN_ATTEMPT'])
    except (KeyError, ValueError):
        raise GitHubActionsError('GitHub workflow run identity is unavailable') from None
    if run < 1 or attempt < 1:
        raise GitHubActionsError('GitHub workflow run identity is invalid')
    manifest = {'schema_version': 1, 'kind': 'devtool.unsigned-android-apk',
                'repository': repo, 'head_sha': sha.lower(), 'run_id': run,
                'run_attempt': attempt, 'ref': env.get('GITHUB_REF', ''),
                'workflow_path': '.github/workflows/' + consumer['workflow'], 'artifact_name': consumer['artifact'],
                'apk_file': consumer['apk'], 'apk_sha256': details['sha256'],
                'apk_size_bytes': details['size_bytes'],
                'application_id': details['package'], 'version_code': details['version_code'],
                'version_name': details['version_name'], 'variant': consumer['variant'],
                'format': 'apk', 'debuggable': False, 'signing_state': 'unsigned',
                'qualified_release': False, 'source_attested': False}
    Path(output).write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return manifest


def unsigned_dispatch(client: GitHubActionsClient, slug: str, root: Path, *, ref: str,
                      execute: bool = False, consumer_id: str = '',
                      source_root: Path | None = None,
                      resume_if_existing: bool = False) -> dict[str, Any]:
    slug = _slug(slug)
    consumer = select_consumer(root, consumer_id=consumer_id, repository=slug)
    if consumer['id'] == 'devtool-hud':
        project, relative = _project_root(root, consumer['project_dir'])
        if relative != consumer['project_dir'] or consumer['module'] not in _gradle_modules(project):
            raise GitHubActionsError('Unsigned lane requires the known Devtool HUD application')
        _gradle_bootstrap(project)
    source = Path(source_root).resolve() if source_root is not None else Path(root).resolve()
    if source.is_symlink() or not source.is_dir():
        raise GitHubActionsError('Unsigned dispatch needs a safe local producer checkout')
    sha = _git(source, 'rev-parse', 'HEAD')
    if not _SHA40.fullmatch(sha) or _git(source, 'status', '--porcelain', '--untracked-files=normal'):
        raise GitHubActionsError('Unsigned CI dispatch requires clean committed local source')
    dispatch_opts = ({'expected_source_sha':sha,'resume_if_existing':True}
                     if resume_if_existing else {})
    workflow = dispatch_workflow(client, slug, consumer['workflow'], ref, {},
                                 execute=False, **dispatch_opts)
    if workflow.get('workflow_path') != '.github/workflows/' + consumer['workflow'] or workflow['pre_dispatch_sha'].lower() != sha.lower():
        raise GitHubActionsError('Unsigned CI workflow or remote Git SHA differs from local HEAD')
    report = {'phase': 'ACTIONS-10', 'operation': 'unsigned-build',
              'repository': slug, 'workflow': consumer['workflow'], 'source_sha': sha, 'consumer_id': consumer['id'], 'variant': consumer['variant'],
              'ref': ref, 'dispatched': False, 'qualified_release': False,
              'workflow_plan': workflow}
    if execute:
        result = dispatch_workflow(client, slug, consumer['workflow'], ref, {},
                                   execute=True, **dispatch_opts)
        report['dispatch'] = result
        # RM-16 resume does not issue a new GitHub dispatch. Carry through the
        # dispatcher's actual POST outcome instead of treating execute=True as
        # evidence that a POST was sent.
        if type(result.get('dispatched')) is not bool:
            raise GitHubActionsError('Unsigned dispatch result lacks verified POST outcome')
        report['dispatched'] = result['dispatched']
        report['resume_existing'] = result.get('resume_existing') is True
        if result.get('pre_dispatch_sha', '').lower() != sha.lower():
            report['warning'] = 'Remote source moved between checks; do not import until source is reconciled'
    return report


def _manifest(archive: Path, remote: dict[str, Any], artifact: dict[str, Any],
              *, expected_sha: str, consumer: dict | None = None) -> tuple[dict[str, Any], bytes]:
    if not _SHA40.fullmatch(expected_sha):
        raise GitHubActionsError('An exact 40-character expected source SHA is required')
    consumer = consumer or select_consumer(Path.cwd(), consumer_id='devtool-hud')
    from .actions_artifacts import check_zip
    check_zip(archive)
    with zipfile.ZipFile(archive) as z:
        infos = {x.filename: x for x in z.infolist() if not x.is_dir()}
        if set(infos) != {MANIFEST_NAME, consumer['apk']} or len(z.infolist()) != 2:
            raise GitHubActionsError('Unsigned candidate artifact must contain exactly APK and manifest')
        if infos[MANIFEST_NAME].file_size > _MAX_MANIFEST:
            raise GitHubActionsError('Unsigned candidate manifest is too large')
        if infos[consumer['apk']].file_size > _MAX_PACKAGE:
            raise GitHubActionsError('Unsigned candidate APK exceeds size limit')
        try:
            data = json.loads(z.read(MANIFEST_NAME).decode('utf-8'))
        except (UnicodeError, ValueError):
            raise GitHubActionsError('Unsigned candidate manifest is invalid JSON') from None
        if not isinstance(data, dict):
            raise GitHubActionsError('Unsigned candidate manifest must be an object')
        required = {'schema_version': 1, 'kind': 'devtool.unsigned-android-apk',
                    'repository': consumer['repository'], 'head_sha': expected_sha.lower(),
                    'run_id': remote['id'], 'run_attempt': remote['run_attempt'],
                    'workflow_path': '.github/workflows/' + consumer['workflow'], 'artifact_name': consumer['artifact'],
                    'apk_file': consumer['apk'], 'application_id': consumer['application_id'],
                    'variant': consumer['variant'], 'format': 'apk', 'debuggable': False,
                    'signing_state': 'unsigned', 'qualified_release': False,
                    'source_attested': False}
        for key, expected in required.items():
            value = data.get(key)
            if type(value) is not type(expected) or value != expected:
                raise GitHubActionsError(f'Unsigned candidate manifest {key} fails source-bound contract')
        if (not isinstance(data.get('ref'), str) or not data['ref'].startswith(('refs/heads/', 'refs/tags/')) or
                not isinstance(data.get('version_name'), str) or not data['version_name'] or
                type(data.get('version_code')) is not int or data['version_code'] < 1 or
                type(data.get('apk_size_bytes')) is not int or
                data['apk_size_bytes'] != infos[consumer['apk']].file_size or
                not isinstance(data.get('apk_sha256'), str) or
                not _SHA.fullmatch(data['apk_sha256'])):
            raise GitHubActionsError('Unsigned candidate manifest has invalid package identity or checksum')
        branch = remote.get('head_branch')
        if isinstance(branch, str) and branch and data['ref'] not in ('refs/heads/' + branch, 'refs/tags/' + branch):
            raise GitHubActionsError('Unsigned candidate manifest ref differs from remote workflow branch')
        if remote['head_sha'].lower() != expected_sha.lower() or artifact.get('name') != consumer['artifact']:
            raise GitHubActionsError('Unsigned candidate run source or artifact name differs from expectation')
        # ZIP member contents are hashed before any extraction or mutation.
        sha = hashlib.sha256()
        with z.open(consumer['apk']) as apk:
            while chunk := apk.read(1024 * 1024):
                sha.update(chunk)
        if sha.hexdigest() != data['apk_sha256']:
            raise GitHubActionsError('Unsigned APK bytes differ from manifest SHA-256')
        return data, z.read(MANIFEST_NAME)


def unsigned_import(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
                    artifact_id: int, expected_sha: str, download: bool = False,
                    materialize: bool = True, consumer_id: str = '') -> dict[str, Any]:
    slug = _slug(slug)
    run = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    if (run['status'], run['conclusion'], run['event']) != ('completed', 'success', 'workflow_dispatch'):
        raise GitHubActionsError('Unsigned import requires successful workflow_dispatch completion')
    from .actions_artifacts import _metadata
    art = _metadata(client.get_artifact(slug, artifact_id), slug, run, exact_id=artifact_id)
    consumer = select_consumer(root, consumer_id=consumer_id, repository=slug, artifact=art.get('name', ''))
    workflow_path = '.github/workflows/' + consumer['workflow']
    workflow = client.workflow(slug, consumer['workflow'])
    if workflow.get('path') != workflow_path or run['workflow_id'] != workflow.get('id'):
        raise GitHubActionsError('Unsigned import workflow identity mismatch')
    if type(run.get('run_attempt')) is not int or run['run_attempt'] < 1:
        raise GitHubActionsError('Unsigned import requires a known run attempt')
    if download:
        result = download_artifact(client, slug, run_id, artifact_id, root)
    verified = verify_download(client, slug, run_id, artifact_id, root)
    archive = Path(verified['file'])
    man, _ = _manifest(archive, run, art, expected_sha=expected_sha, consumer=consumer)
    with tempfile.TemporaryDirectory(prefix='devtool-unsigned-inspect-') as temp:
        candidate = Path(temp) / consumer['apk']
        with zipfile.ZipFile(archive) as z, z.open(consumer['apk']) as source, candidate.open('xb') as dest:
            copied = 0
            while block := source.read(1024 * 1024):
                copied += len(block)
                if copied > _MAX_PACKAGE:
                    raise GitHubActionsError('Unsigned package exceeds extraction bound')
                dest.write(block)
        details = _inspect_consumer(candidate, consumer)
        check_packaging(candidate, consumer)
        if any((details['sha256'] != man['apk_sha256'],
                details['size_bytes'] != man['apk_size_bytes'],
                details['package'] != man['application_id'],
                details['version_code'] != man['version_code'],
                details['version_name'] != man['version_name'])):
            raise GitHubActionsError('Unsigned candidate APK does not match verified manifest')
        result = {'phase': 'ACTIONS-10', 'operation': 'unsigned-import',
                  'repository': slug, 'run_id': run_id, 'run_attempt': run['run_attempt'],
                  'artifact_id': artifact_id, 'source_sha': expected_sha.lower(),
                  'package': details, 'consumer_id': consumer['id'], 'variant': consumer['variant'],
                  'consumer_contract_sha256': contract_sha256(consumer), 'archive_verified': True,
                  'unsigned_verified': True, 'qualified_release': False,
                  'locally_signed': False}
        if not materialize:
            return result
        storage = root.resolve() / '.devtool' / 'artifacts' / 'android-unsigned'
        for parent in (root.resolve()/'.devtool', root.resolve()/'.devtool'/'artifacts', storage):
            if parent.is_symlink():
                raise GitHubActionsError('Unsigned import destination includes a symlink')
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = storage / f'run{run_id}-attempt{run["run_attempt"]}-artifact{artifact_id}-{details["sha256"][:16]}.apk'
        if target.exists() or target.is_symlink():
            raise GitHubActionsError('Unsigned APK already imported; refusing overwrite')
        _publish_exclusive(candidate, target, details['sha256'])
        metadata = {'producer': 'github-actions', 'state': 'unsigned-release-unqualified',
                    'remote_repository': slug, 'remote_run_id': run_id,
                    'remote_run_attempt': run['run_attempt'], 'remote_workflow_id': run['workflow_id'],
                    'remote_workflow_path': workflow_path, 'remote_head_sha': expected_sha.lower(),
                    'consumer_id': consumer['id'], 'variant': consumer['variant'],
                    'consumer_contract_sha256': contract_sha256(consumer),
                    'remote_artifact_name': consumer['artifact'], 'signing_target': consumer['target'],
                    'remote_artifact_id': artifact_id, 'package_sha256': details['sha256'],
                    'package': details['package'], 'version_code': details['version_code'],
                    'signing_state': 'unsigned', 'signature_verified': False,
                    'qualified_release': False, 'locally_signed': False,
                    'archive_verified': True}
        records = register_paths(root, [target], metadata=metadata)
        if not records:
            raise GitHubActionsError('Unsigned APK saved but canonical registry refused registration')
        result.update({'package_file': str(target), 'registry_id': records[0]['id']})
        return result


def _imported(root: Path, registry_id: str) -> tuple[Path, dict[str, Any]]:
    """Find one immutable, previously verified source-bound unsigned APK."""
    if not isinstance(registry_id, str) or not registry_id or len(registry_id) > 128:
        raise GitHubActionsError('Specify an exact unsigned APK registry ID')
    items = [item for item in load_registry(root).get('items', [])
             if isinstance(item, dict) and item.get('id') == registry_id]
    if len(items) != 1:
        raise GitHubActionsError('Unsigned candidate registry ID is missing or ambiguous')
    item = items[0]
    meta = item.get('metadata')
    consumer = select_consumer(root, consumer_id=str(meta.get('consumer_id') or 'devtool-hud') if isinstance(meta,dict) else 'devtool-hud')
    if isinstance(meta, dict) and meta.get('consumer_id') and (meta.get('variant') != consumer['variant'] or meta.get('remote_artifact_name') != consumer['artifact'] or meta.get('consumer_contract_sha256') != contract_sha256(consumer)):
        raise GitHubActionsError('Imported Android consumer/variant receipt does not match registration')
    if not isinstance(meta, dict) or any((
            meta.get('producer') != 'github-actions',
            meta.get('state') != 'unsigned-release-unqualified',
            meta.get('remote_repository', '').casefold() != consumer['repository'].casefold(),
            meta.get('remote_workflow_path') != '.github/workflows/' + consumer['workflow'],
            not _SHA40.fullmatch(str(meta.get('remote_head_sha', ''))),
            type(meta.get('remote_run_id')) is not int,
            type(meta.get('remote_run_attempt')) is not int,
            type(meta.get('remote_artifact_id')) is not int,
            meta.get('signing_state') != 'unsigned',
            meta.get('archive_verified') is not True,
            meta.get('signature_verified') is not False,
            meta.get('qualified_release') is not False,
            meta.get('package') != consumer['application_id'],
            not isinstance(meta.get('package_sha256'), str),
            not _SHA.fullmatch(meta.get('package_sha256', '')))):
        raise GitHubActionsError('Registered unsigned candidate has invalid or insufficient provenance')
    folder = root.resolve()/'.devtool'/'artifacts'/'android-unsigned'
    path = root.resolve() / str(item.get('path', ''))
    if path.is_symlink() or path.resolve().parent != folder or folder.is_symlink():
        raise GitHubActionsError('Registered unsigned APK escapes trusted import storage')
    if _regular(path, _MAX_PACKAGE) != item.get('size') and item.get('size') is not None:
        raise GitHubActionsError('Registered unsigned candidate size changed')
    digest = _hash(path)
    if digest != item.get('sha256') or digest != meta['package_sha256']:
        raise GitHubActionsError('Registered unsigned candidate bytes changed')
    return path, meta


def unsigned_local_sign(root: Path, *, registry_id: str, identity_id: str = '',
                        target: str = 'android_hud', execute: bool = False) -> dict[str, Any]:
    """Only local LIFE signer is allowed. Never rebuild or promote to protected release."""
    from .actions_setup import _signing_profile, _keystore, _signer_material
    from .actions_local_release import _local_signing_password, local_verify
    root = root.resolve()
    candidate, metadata = _imported(root, registry_id)
    consumer = select_consumer(root, consumer_id=str(metadata.get('consumer_id') or 'devtool-hud'))
    if consumer['id'] != 'devtool-hud' and target not in ('android_hud', consumer['target']):
        raise GitHubActionsError('Selected signing target differs from registered Android consumer')
    if consumer['id'] != 'devtool-hud':
        target = consumer['target']
        if identity_id and identity_id != consumer['signing_identity_id']:
            raise GitHubActionsError('Requested LIFE identity is not authorized for consumer')
        identity_id = consumer['signing_identity_id']
    unsigned = _inspect_consumer(candidate, consumer)
    check_packaging(candidate, consumer)
    if unsigned['sha256'] != metadata['package_sha256'] or unsigned['version_code'] != metadata['version_code']:
        raise GitHubActionsError('Candidate changed or no longer matches import metadata')
    profile = _signing_profile(root, identity_id=identity_id, target=target, variant=consumer['variant'])
    if profile.mode != 'devtool' or not profile.expected_cert_sha256 or not profile.alias:
        raise GitHubActionsError('Signing requires a registered canonical LIFE release identity')
    if consumer['signer_sha256'] and profile.expected_cert_sha256.lower() != consumer['signer_sha256']:
        raise GitHubActionsError('Registered consumer LIFE certificate authorization differs from selected signer')
    _keystore(profile.keystore, root)
    receipt = {'phase': 'ACTIONS-10', 'operation': 'local-sign',
               'mode': 'apply' if execute else 'plan', 'registry_id': registry_id,
               'unsigned_sha256': unsigned['sha256'], 'unsigned_package': str(candidate),
               'remote_repository': metadata['remote_repository'],
               'remote_run_id': metadata['remote_run_id'],
               'remote_run_attempt': metadata['remote_run_attempt'],
               'remote_head_sha': metadata['remote_head_sha'],
               'consumer_id': consumer['id'], 'variant': consumer['variant'],
               'consumer_contract_sha256': contract_sha256(consumer),
               'remote_artifact_id': metadata.get('remote_artifact_id'),
               'packaging': check_packaging(candidate, consumer),
               'signing_identity_id': profile.identity_id,
               'expected_signer_sha256': profile.expected_cert_sha256.lower(),
               'qualified_release': False, 'locally_signed': False,
               'github_provenance_verified': True,
               'source_attested': False}
    if not execute:
        return receipt
    storage = root/'.devtool'/'artifacts'/'android-locally-signed'
    for part in (root/'.devtool', root/'.devtool'/'artifacts', storage):
        if part.is_symlink():
            raise GitHubActionsError('Signing destination uses a symbolic link')
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = storage / f'run{metadata["remote_run_id"]}-{consumer["variant"]}-signed-{unsigned["sha256"][:16]}.apk'
    if destination.is_symlink() or destination.exists():
        raise GitHubActionsError('Signed output already exists; refusing overwrite')
    with _local_signing_password(profile):
        keystore, secrets, verified_fingerprint = _signer_material(root, profile)
        if verified_fingerprint != profile.expected_cert_sha256.lower():
            raise GitHubActionsError('LIFE certificate changed during signer selection')
        with tempfile.TemporaryDirectory(prefix='.devtool-sign-', dir=storage) as folder:
            aligned = Path(folder)/'aligned.apk'
            signed = Path(folder)/'signed.apk'
            try:
                zipalign = _tool('zipalign')
                for command, label in (
                    ([zipalign, '-P', '16', '-f', '4', str(candidate), str(aligned)], 'zipalign'),
                ):
                    try:
                        proc = subprocess.run(command, capture_output=True, timeout=90, check=False)
                    except (OSError, subprocess.TimeoutExpired):
                        raise GitHubActionsError(f'Local {label} unavailable or timed out') from None
                    if proc.returncode:
                        raise GitHubActionsError(f'Local {label} failed; raw output withheld')
                # Revalidate the registered bytes immediately before signing (TOCTOU).
                verified, _ = _imported(root, registry_id)
                if verified != candidate or _hash(candidate) != unsigned['sha256']:
                    raise GitHubActionsError('Unsigned candidate changed before local signing')
                environment = dict(os.environ)
                environment.update(secrets)
                args = [_tool('apksigner'), 'sign', '--ks', str(keystore),
                        '--ks-key-alias', profile.alias,
                        '--ks-pass', 'env:DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD',
                        '--key-pass', 'env:DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD',
                        '--out', str(signed), str(aligned)]
                try:
                    proc = subprocess.run(args, env=environment, capture_output=True,
                                          timeout=120, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    raise GitHubActionsError('Local apksigner unavailable or timed out') from None
                finally:
                    environment.clear()
                    secrets.clear()
                if proc.returncode:
                    raise GitHubActionsError('Local apksigner failed; private output withheld')
                try:
                    check = subprocess.run([zipalign, '-c', '-P', '16', '4', str(signed)],
                                           capture_output=True, timeout=45, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    raise GitHubActionsError('Local signed APK alignment verification unavailable') from None
                if check.returncode:
                    raise GitHubActionsError('Local signed APK alignment verification failed')
                if consumer['required_files'] or consumer['forbidden_files']:
                    check_packaging(signed, consumer)
                signed_native_pages = check_native_library_pages(signed)
                verification = local_verify(root, artifact=str(signed), fmt='apk',
                                            identity_id=profile.identity_id, target=target,
                                            application_id=unsigned['package'],
                                            registered_application_id=(consumer['application_id'] if consumer['id'] != 'devtool-hud' else ''),
                                            variant=consumer['variant'],
                                            minimum_version_code=unsigned['version_code'])
                if verification['artifact_sha256'] != _hash(signed):
                    raise GitHubActionsError('Signed APK checksum changed during verification')
                # Recheck and preserve candidate identity before publishing signed bytes.
                _imported(root, registry_id)
                _publish_exclusive(signed, destination, verification['artifact_sha256'])
            finally:
                secrets.clear()
    metadata_signed = {'producer': 'devtool-native-life', 'state': 'locally-signed-unqualified',
                       'qualified_release': False, 'locally_signed': True,
                       'signature_verified': True, 'remote_repository': metadata['remote_repository'],
                       'remote_run_id': metadata['remote_run_id'],
                       'remote_run_attempt': metadata['remote_run_attempt'],
                       'remote_workflow_path': '.github/workflows/' + consumer['workflow'],
                       'consumer_id': consumer['id'], 'variant': consumer['variant'],
                       'consumer_contract_sha256': contract_sha256(consumer),
                       'remote_artifact_id': metadata.get('remote_artifact_id'),
                       'remote_head_sha': metadata['remote_head_sha'],
                       'unsigned_registry_id': registry_id, 'unsigned_sha256': unsigned['sha256'],
                       'signing_identity_id': profile.identity_id,
                       'signer_sha256': profile.expected_cert_sha256.lower(),
                       'package': unsigned['package'], 'version_code': unsigned['version_code'],
                       'signed_sha256': verification['artifact_sha256'],
                       'apk_16k_zipalign_verified': True,
                       'native_page_alignment': signed_native_pages}
    records = register_paths(root, [destination], metadata=metadata_signed)
    if not records:
        raise GitHubActionsError('Locally signed APK produced but canonical registry did not accept it')
    receipt.update({'locally_signed': True, 'signature_verified': True,
                    'signed_package': str(destination),
                    'signed_sha256': verification['artifact_sha256'],
                    'apk_16k_zipalign_verified': True,
                    'native_page_alignment': signed_native_pages,
                    'signed_registry_id': records[0]['id'],
                    'verification': verification})
    return receipt
