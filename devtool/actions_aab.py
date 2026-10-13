"""ACTIONS G4: source-bound, unsigned Android App Bundle candidate and local LIFE signing.

This is *not* a production release.  AAB collection requires bundletool; local
signing uses the existing LIFE identity.  The dedicated CI lane never receives
signing credentials, and protected signed-release workflows remain untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .actions_android import (_git, _gradle_bootstrap, _gradle_modules, _project_root,
                              _tool, _run, _MAX_PACKAGE, _SHA40, _PACKAGE)
from .actions_dispatch import dispatch_workflow
from .actions_artifacts import download_artifact, verify_download, _metadata, check_zip
from .actions_runs import _normalize_run
from .artifact_registry import register_paths, load_registry
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug
from .actions_unsigned import _hash, _regular, _publish_exclusive

WORKFLOW = 'devtool-android-aab-candidate.yml'
WORKFLOW_PATH = '.github/workflows/' + WORKFLOW
ARTIFACT_NAME = 'devtool-android-unsigned-aab'
BUNDLE = 'unsigned-release.aab'
MANIFEST = 'manifest.json'
TESTS = 'unit-tests.json'
LINT = 'lint-results-release.xml'
FILES = frozenset((BUNDLE, MANIFEST, TESTS, LINT))
APP = 'io.github.mikeceead.devtoolhud'
SHA64 = re.compile(r'[0-9a-f]{64}\Z')
MAX_RECEIPT = 32 * 1024


def _owner_only_directory(root: Path, leaf: str) -> Path:
    root = root.resolve()
    path = root
    for part in ('.devtool', 'artifacts', leaf):
        path = path / part
        if path.is_symlink():
            raise GitHubActionsError('Android AAB destination contains a symlink')
        if path.exists() and not path.is_dir():
            raise GitHubActionsError('Android AAB destination has an invalid file type')
        path.mkdir(exist_ok=True, mode=0o700)
    return path


def _bundle_zip(path: Path) -> dict:
    """Strict structural check, not a substitute for bundletool verification."""
    size = _regular(path, _MAX_PACKAGE)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 4096:
                raise GitHubActionsError('AAB has missing or excessive ZIP entries')
            names = set()
            total = 0
            for entry in entries:
                name = entry.filename
                parts = name.rstrip('/').split('/')
                mode = stat.S_IFMT((entry.external_attr >> 16) & 0xffff)
                if (not name or name.startswith('/') or '\\' in name or ':' in name or
                    any(p in ('', '.', '..') for p in parts) or name in names or
                    any(ord(char) < 32 for char in name) or mode not in (0, stat.S_IFDIR, stat.S_IFREG)
                    or entry.flag_bits & 1):
                    raise GitHubActionsError('AAB ZIP contains an unsafe or ambiguous entry')
                names.add(name)
                total += entry.file_size
                if total > _MAX_PACKAGE * 2:
                    raise GitHubActionsError('AAB ZIP exceeds expanded-size safety bound')
                # AAB candidates must be unsigned, not malformed signatures.
                if name.upper().startswith('META-INF/') and name.count('/') == 1 and (
                    name.upper().endswith(('.SF', '.RSA', '.DSA', '.EC')) or
                    name.upper().split('/')[-1].startswith('SIG-')):
                    raise GitHubActionsError('Unsigned AAB contains a JAR signature')
            if 'base/manifest/AndroidManifest.xml' not in names or 'BundleConfig.pb' not in names:
                raise GitHubActionsError('AAB lacks required Android bundle members')
            if archive.testzip() is not None:
                raise GitHubActionsError('AAB ZIP CRC verification failed')
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise GitHubActionsError('AAB is not a valid bounded ZIP') from None
    return {'sha256': _hash(path), 'size_bytes': size}


def _unsigned_bundle(path: Path) -> dict:
    state = _bundle_zip(path)
    try:
        proc = subprocess.run([_tool('jarsigner'), '-verify', '-strict', str(path)],
                              capture_output=True, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('jarsigner required to inspect unsigned AAB') from None
    if proc.returncode == 0:
        raise GitHubActionsError('Expected unsigned AAB but JAR verification passed')
    # Negative jarsigner status alone cannot prove unsignedness; the structural
    # signature-entry check above rejects damaged or incomplete JAR signatures.
    jar = os.environ.get('DEVTOOL_BUNDLETOOL_JAR', '')
    if not jar or not Path(jar).is_file() or Path(jar).is_symlink():
        raise GitHubActionsError('Unsigned AAB inspection requires DEVTOOL_BUNDLETOOL_JAR')
    java = _tool('java')
    _run([java, '-jar', jar, 'validate', '--bundle', str(path)], purpose='Unsigned AAB bundletool validation')
    package = _run([java, '-jar', jar, 'dump', 'manifest', '--bundle', str(path),
                    '--xpath', '/manifest/@package'], purpose='Unsigned AAB package inspection').strip().strip('"\'').strip()
    code = _run([java, '-jar', jar, 'dump', 'manifest', '--bundle', str(path),
                 '--xpath', '/manifest/@android:versionCode'], purpose='Unsigned AAB versionCode inspection').strip()
    if package != APP or not code.isdecimal() or int(code) < 1:
        raise GitHubActionsError('Unsigned AAB Android package/version identity mismatch')
    return {**state, 'package': package, 'version_code': int(code),
            'signing_state': 'unsigned', 'signature_verified': False,
            'qualified_release': False}


def _test_receipt(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {'tests', 'failures', 'errors', 'skipped', 'gradle_task'}:
        raise GitHubActionsError('AAB unit-test receipt has an invalid schema')
    if data['gradle_task'] != ':app:testDebugUnitTest':
        raise GitHubActionsError('AAB unit-test task identity mismatch')
    for key in ('tests', 'failures', 'errors', 'skipped'):
        if type(data[key]) is not int or data[key] < 0:
            raise GitHubActionsError('AAB unit-test receipt count is invalid')
    if data['tests'] < 1 or data['failures'] or data['errors'] or data['skipped'] >= data['tests']:
        raise GitHubActionsError('AAB unit-test receipt has no passing test set')
    return data


def _lint_receipt(data: bytes) -> dict:
    if len(data) > 4 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise GitHubActionsError('AAB lint report is too large or has external XML declarations')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise GitHubActionsError('AAB lint report is malformed') from None
    if root.tag != 'issues':
        raise GitHubActionsError('AAB lint XML has unexpected root')
    blocking = 0
    for node in root:
        if node.tag != 'issue':
            continue
        if node.attrib.get('severity') in ('Error', 'Fatal'):
            blocking += 1
    if blocking:
        raise GitHubActionsError('AAB release lint contains blocking errors')
    return {'blocking_errors': 0, 'sha256': hashlib.sha256(data).hexdigest()}


def emit_aab_manifest(bundle: Path, tests: Path, lint: Path, output: Path, env: dict[str, str]) -> dict:
    """GitHub-runner emitter called only after Gradle bundle, lint and tests succeeded."""
    details = _bundle_zip(bundle)
    unit = _test_receipt(json.loads(tests.read_text('utf-8')))
    lint_evidence = _lint_receipt(lint.read_bytes())
    sha = env.get('GITHUB_SHA', '')
    if env.get('GITHUB_REPOSITORY') != 'Mikeyphw/devtool' or not _SHA40.fullmatch(sha):
        raise GitHubActionsError('Unsigned AAB runner has invalid source identity')
    try:
        run = int(env['GITHUB_RUN_ID']); attempt = int(env['GITHUB_RUN_ATTEMPT'])
    except (ValueError, KeyError):
        raise GitHubActionsError('Unsigned AAB runner lacks run identity') from None
    if run <= 0 or attempt <= 0 or not env.get('GITHUB_REF', '').startswith(('refs/heads/', 'refs/tags/')):
        raise GitHubActionsError('Unsigned AAB runner has invalid attempt or source ref')
    report = {'schema_version': 1, 'kind': 'devtool.unsigned-android-aab',
              'repository': 'Mikeyphw/devtool', 'head_sha': sha.lower(),
              'run_id': run, 'run_attempt': attempt, 'ref': env['GITHUB_REF'],
              'workflow_path': WORKFLOW_PATH, 'artifact_name': ARTIFACT_NAME,
              'aab_file': BUNDLE, 'aab_sha256': details['sha256'],
              'aab_size_bytes': details['size_bytes'], 'application_id': APP,
              'variant': 'release', 'format': 'aab', 'signing_state': 'unsigned',
              'unit_tests_file': TESTS, 'unit_tests_sha256': _hash(tests),
              'lint_file': LINT, 'lint_sha256': lint_evidence['sha256'],
              'unit_tests': unit, 'lint': {'blocking_errors': 0},
              'qualified_release': False, 'source_attested': False}
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', 'utf-8')
    return report


def aab_dispatch(client: GitHubActionsClient, slug: str, root: Path, *, ref: str,
                 execute: bool = False) -> dict:
    slug = _slug(slug)
    if slug.casefold() != 'mikeyphw/devtool':
        raise GitHubActionsError('G4 AAB lane is restricted to expected repository')
    project, relative = _project_root(root, 'android/devtool-live-hud')
    if relative != 'android/devtool-live-hud' or 'app' not in _gradle_modules(project):
        raise GitHubActionsError('G4 AAB requires known Android HUD module')
    _gradle_bootstrap(project)
    head = _git(root, 'rev-parse', 'HEAD')
    if not _SHA40.fullmatch(head) or _git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise GitHubActionsError('AAB dispatch requires a clean committed checkout')
    preview = dispatch_workflow(client, slug, WORKFLOW, ref, {}, execute=False)
    if preview.get('workflow_path') != WORKFLOW_PATH or preview.get('pre_dispatch_sha', '').lower() != head.lower():
        raise GitHubActionsError('AAB workflow source does not match local committed checkout')
    result = {'phase': 'ACTIONS-G4', 'operation': 'unsigned-aab-build',
              'repository': slug, 'workflow': WORKFLOW, 'source_sha': head,
              'ref': ref, 'workflow_plan': preview, 'dispatched': False,
              'qualified_release': False}
    if execute:
        dispatch = dispatch_workflow(client, slug, WORKFLOW, ref, {}, execute=True)
        result.update(dispatched=True, dispatch=dispatch)
        if dispatch.get('pre_dispatch_sha', '').lower() != head.lower():
            result['warning'] = 'AAB source moved before dispatch; do not import until reconciled'
    return result


def _archive_manifest(archive: Path, remote: dict, art: dict, sha: str) -> dict:
    if not _SHA40.fullmatch(sha):
        raise GitHubActionsError('AAB import requires exact expected SHA')
    check_zip(archive)
    with zipfile.ZipFile(archive) as z:
        info = {i.filename: i for i in z.infolist() if not i.is_dir()}
        if set(info) != FILES or len(z.infolist()) != len(FILES):
            raise GitHubActionsError('AAB artifact must contain exactly bundle and three evidence files')
        if any(info[n].file_size > MAX_RECEIPT for n in (MANIFEST, TESTS)) or info[LINT].file_size > 4 * 1024 * 1024:
            raise GitHubActionsError('AAB artifact evidence exceeds bound')
        try:
            data = json.loads(z.read(MANIFEST).decode('utf-8'))
            tests = json.loads(z.read(TESTS).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise GitHubActionsError('AAB evidence JSON is invalid') from None
        required = {'schema_version': 1, 'kind': 'devtool.unsigned-android-aab',
                    'repository': 'Mikeyphw/devtool', 'head_sha': sha.lower(),
                    'run_id': remote['id'], 'run_attempt': remote['run_attempt'],
                    'workflow_path': WORKFLOW_PATH, 'artifact_name': ARTIFACT_NAME,
                    'aab_file': BUNDLE, 'application_id': APP, 'variant': 'release',
                    'format': 'aab', 'signing_state': 'unsigned',
                    'unit_tests_file': TESTS, 'lint_file': LINT,
                    'qualified_release': False, 'source_attested': False}
        if not isinstance(data, dict) or any(type(data.get(k)) is not type(v) or data[k] != v for k,v in required.items()):
            raise GitHubActionsError('AAB manifest source/run/format identity mismatch')
        if (remote['head_sha'].lower() != sha.lower() or art['name'] != ARTIFACT_NAME or
            type(data.get('aab_size_bytes')) is not int or data['aab_size_bytes'] != info[BUNDLE].file_size or
            not SHA64.fullmatch(str(data.get('aab_sha256', ''))) or
            any(not SHA64.fullmatch(str(data.get(key, ''))) for key in ('unit_tests_sha256','lint_sha256'))):
            raise GitHubActionsError('AAB manifest size, source or checksum invalid')
        branch = remote.get('head_branch')
        if not isinstance(data.get('ref'), str) or not data['ref'].startswith(('refs/heads/', 'refs/tags/')):
            raise GitHubActionsError('AAB manifest has invalid ref')
        if branch and data['ref'] not in ('refs/heads/' + branch, 'refs/tags/' + branch):
            raise GitHubActionsError('AAB manifest source ref differs from remote branch')
        for name, field in ((BUNDLE,'aab_sha256'), (TESTS,'unit_tests_sha256'), (LINT,'lint_sha256')):
            digest = hashlib.sha256()
            with z.open(name) as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != data[field]:
                raise GitHubActionsError('AAB evidence or package digest mismatch')
        _test_receipt(tests)
        _lint_receipt(z.read(LINT))
        if data.get('unit_tests') != tests or data.get('lint') != {'blocking_errors': 0}:
            raise GitHubActionsError('AAB evidence counts mismatch')
        return data


def aab_import(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
               artifact_id: int, expected_sha: str, download: bool = False,
               materialize: bool = True) -> dict:
    slug = _slug(slug)
    if slug.casefold() != 'mikeyphw/devtool':
        raise GitHubActionsError('G4 AAB import has unexpected repository')
    remote = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    if (remote['status'], remote['conclusion'], remote['event']) != ('completed','success','workflow_dispatch'):
        raise GitHubActionsError('AAB requires successful workflow_dispatch run')
    workflow = client.workflow(slug, WORKFLOW)
    if workflow.get('path') != WORKFLOW_PATH or workflow['id'] != remote['workflow_id']:
        raise GitHubActionsError('AAB workflow identity mismatch')
    if type(remote.get('run_attempt')) is not int or remote['run_attempt'] <= 0:
        raise GitHubActionsError('AAB run attempt unavailable')
    if download:
        download_artifact(client, slug, run_id, artifact_id, root)
    verified = verify_download(client, slug, run_id, artifact_id, root)
    art = _metadata(client.get_artifact(slug, artifact_id), slug, remote, exact_id=artifact_id)
    with zipfile.ZipFile(verified['file']) as z:
        man = _archive_manifest(Path(verified['file']), remote, art, expected_sha)
        with tempfile.TemporaryDirectory(prefix='devtool-aab-check-') as folder:
            aab = Path(folder)/BUNDLE
            with z.open(BUNDLE) as source, aab.open('xb') as dest:
                size = 0
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    size += len(block)
                    if size > _MAX_PACKAGE:
                        raise GitHubActionsError('AAB extracted size exceeds 256 MiB bound')
                    dest.write(block)
            result = _unsigned_bundle(aab)
            if result['sha256'] != man['aab_sha256'] or result['size_bytes'] != man['aab_size_bytes']:
                raise GitHubActionsError('Unsigned AAB differs from source-bound manifest')
            receipt = {'phase':'ACTIONS-G4', 'operation': 'aab-import', 'repository': slug,
                       'run_id': run_id, 'run_attempt': remote['run_attempt'],
                       'source_sha': expected_sha.lower(), 'artifact_id': artifact_id,
                       'package': result, 'unit_tests': man['unit_tests'],
                       'lint': man['lint'], 'archive_verified': True,
                       'bundletool_verified': True, 'qualified_release': False,
                       'source_attested': False, 'locally_signed': False}
            if not materialize:
                return receipt
            storage = _owner_only_directory(root, 'android-aab-unsigned')
            target = storage / f'run{run_id}-attempt{remote["run_attempt"]}-artifact{artifact_id}-{result["sha256"][:16]}.aab'
            if target.exists() or target.is_symlink():
                raise GitHubActionsError('Unsigned AAB already imported; refusing overwrite')
            _publish_exclusive(aab, target, result['sha256'])
            meta = {'producer':'github-actions', 'state':'unsigned-aab-unqualified',
                    'remote_repository':slug, 'remote_run_id':run_id,
                    'remote_run_attempt': remote['run_attempt'], 'remote_workflow_id':remote['workflow_id'],
                    'remote_workflow_path':WORKFLOW_PATH, 'remote_head_sha':expected_sha.lower(),
                    'remote_artifact_id':artifact_id, 'package_sha256':result['sha256'],
                    'package':APP, 'version_code':result['version_code'], 'format':'aab',
                    'signing_state':'unsigned', 'signature_verified':False,
                    'qualified_release':False, 'locally_signed':False, 'archive_verified':True,
                    'lint_verified':True, 'unit_tests_verified':True}
            records = register_paths(root, [target], metadata=meta)
            if not records:
                raise GitHubActionsError('AAB artifact registry registration failed')
            receipt.update(package_file=str(target), registry_id=records[0]['id'])
            return receipt


def _imported_bundle(root: Path, registry_id: str) -> tuple[Path,dict]:
    if not isinstance(registry_id, str) or not re.fullmatch(r'[0-9a-f]{12}', registry_id):
        raise GitHubActionsError('Invalid unsigned AAB registry ID')
    matches = [v for v in load_registry(root).get('items', []) if v.get('id') == registry_id]
    if len(matches) != 1:
        raise GitHubActionsError('Unsigned AAB registry record unavailable or ambiguous')
    row = matches[0]
    meta = row.get('metadata', {})
    if not isinstance(meta, dict) or any((meta.get('state') != 'unsigned-aab-unqualified', meta.get('format') != 'aab',
            meta.get('remote_repository','').casefold() != 'mikeyphw/devtool',
            meta.get('remote_workflow_path') != WORKFLOW_PATH,
            meta.get('qualified_release') is not False, meta.get('archive_verified') is not True,
            meta.get('lint_verified') is not True, meta.get('unit_tests_verified') is not True,
            type(meta.get('remote_run_id')) is not int, type(meta.get('remote_run_attempt')) is not int,
            type(meta.get('remote_artifact_id')) is not int,
            not _SHA40.fullmatch(str(meta.get('remote_head_sha', ''))),
            meta.get('signature_verified') is not False,
            not SHA64.fullmatch(str(meta.get('package_sha256',''))))):
        raise GitHubActionsError('Unsigned AAB registry provenance is invalid')
    path = root.resolve()/str(row.get('path', ''))
    directory = root.resolve()/'.devtool'/'artifacts'/'android-aab-unsigned'
    if path.is_symlink() or path.resolve().parent != directory or directory.is_symlink():
        raise GitHubActionsError('Unsigned AAB registry path escapes storage')
    if _regular(path, _MAX_PACKAGE) != row.get('size') or _hash(path) != meta['package_sha256'] or _hash(path) != row.get('sha256'):
        raise GitHubActionsError('Unsigned AAB bytes changed since import')
    return path, meta


def aab_local_sign(root: Path, *, registry_id: str, identity_id: str = '',
                   target: str = 'android_hud', execute: bool = False) -> dict:
    from .actions_setup import _signing_profile, _signer_material, _keystore
    from .actions_local_release import _local_signing_password, local_verify
    root = root.resolve()
    aab, meta = _imported_bundle(root, registry_id)
    details = _unsigned_bundle(aab)
    profile = _signing_profile(root, identity_id=identity_id, target=target)
    if profile.mode != 'devtool' or not profile.expected_cert_sha256 or not profile.alias:
        raise GitHubActionsError('AAB signing needs canonical LIFE identity')
    _keystore(profile.keystore, root)
    receipt = {'phase':'ACTIONS-G4', 'operation':'aab-local-sign',
               'mode':'apply' if execute else 'plan', 'unsigned_registry_id':registry_id,
               'source_sha':meta['remote_head_sha'], 'remote_run_id':meta['remote_run_id'],
               'remote_run_attempt':meta['remote_run_attempt'],
               'signing_identity_id':profile.identity_id,
               'expected_signer_sha256':profile.expected_cert_sha256.lower(),
               'qualified_release':False, 'locally_signed':False, 'source_attested':False}
    if not execute:
        return receipt
    storage = _owner_only_directory(root, 'android-aab-signed')
    destination = storage / f'run{meta["remote_run_id"]}-signed-{details["sha256"][:16]}.aab'
    if destination.exists() or destination.is_symlink():
        raise GitHubActionsError('AAB signed output already exists')
    with _local_signing_password(profile):
        store, secrets, fingerprint = _signer_material(root, profile)
        if fingerprint != profile.expected_cert_sha256.lower():
            raise GitHubActionsError('LIFE signing identity changed')
        with tempfile.TemporaryDirectory(prefix='.aab-sign-', dir=storage) as folder:
            signed = Path(folder)/'signed.aab'
            if _imported_bundle(root, registry_id)[0] != aab:
                raise GitHubActionsError('AAB candidate changed before local signing')
            environment = dict(os.environ)
            environment.update(secrets)
            cmd = [_tool('jarsigner'), '-keystore', str(store), '-storepass:env',
                   'DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD', '-keypass:env',
                   'DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD', '-signedjar', str(signed),
                   str(aab), profile.alias]
            try:
                proc = subprocess.run(cmd, env=environment, capture_output=True,
                                      timeout=120, check=False)
            except (OSError, subprocess.TimeoutExpired):
                raise GitHubActionsError('AAB jarsigner unavailable or timed out') from None
            finally:
                environment.clear()
                secrets.clear()
            if proc.returncode:
                raise GitHubActionsError('AAB jarsigner signing failed; private output withheld')
            # Verify with independent bundletool + JAR certificate inspection.
            check = local_verify(root, artifact=str(signed), fmt='aab',
                                 identity_id=profile.identity_id, target=target,
                                 application_id=APP, minimum_version_code=details['version_code'])
            _imported_bundle(root, registry_id)
            if check['artifact_sha256'] != _hash(signed):
                raise GitHubActionsError('Signed AAB verification digest changed')
            _publish_exclusive(signed, destination, check['artifact_sha256'])
    records = register_paths(root, [destination], metadata={
        'producer':'devtool-native-life','state':'locally-signed-aab-unqualified',
        'qualified_release':False,'locally_signed':True, 'signature_verified':True,
        'remote_repository':meta['remote_repository'], 'remote_run_id':meta['remote_run_id'],
        'remote_run_attempt':meta['remote_run_attempt'], 'remote_head_sha':meta['remote_head_sha'],
        'remote_workflow_path':WORKFLOW_PATH, 'unsigned_registry_id':registry_id,
        'signing_identity_id':profile.identity_id,
        'signer_sha256':profile.expected_cert_sha256.lower(), 'format':'aab',
        'signed_sha256':check['artifact_sha256']})
    if not records:
        raise GitHubActionsError('Signed AAB registry registration failed')
    receipt.update(locally_signed=True, signature_verified=True,
                   signed_package=str(destination), signed_registry_id=records[0]['id'],
                   signed_sha256=check['artifact_sha256'], verification=check)
    return receipt
