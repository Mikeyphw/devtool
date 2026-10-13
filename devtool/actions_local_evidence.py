"""G6: read-only local signed-APK/AAB lineage and LIFE identity verification.

A verified GitHub *import* is not a GitHub cryptographic source attestation.
A local signature is not ACTIONS-08 qualified_release. All results say so.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .artifact_registry import load_registry
from .github_actions import GitHubActionsError
from .actions_unsigned import _hash, _regular, _imported as imported_apk, WORKFLOW_PATH as APK_WORKFLOW
from .actions_aab import _imported_bundle as imported_aab, WORKFLOW_PATH as AAB_WORKFLOW, APP as AAB_APP
from .actions_android import _MAX_PACKAGE

_ID = re.compile(r'[0-9a-f]{12}\Z')
_SHA40 = re.compile(r'[0-9a-f]{40}\Z')
_SHA64 = re.compile(r'[0-9a-f]{64}\Z')


def _require(value: Any, name: str, kind: type) -> Any:
    if type(value) is not kind:
        raise GitHubActionsError(f'G6 signed evidence has invalid {name}')
    if kind is int and value < 1:
        raise GitHubActionsError(f'G6 signed evidence has invalid {name}')
    return value


def _record(items: list, registry_id: str) -> dict:
    matches = [r for r in items if isinstance(r, dict) and r.get('id') == registry_id]
    if len(matches) != 1:
        raise GitHubActionsError('G6 signed evidence registry ID missing or ambiguous')
    return matches[0]


def signed_local_evidence(root: Path, *, registry_id: str, fmt: str = 'apk',
                          identity_id: str = '', target: str = 'android_hud',
                          expected_source_sha: str = '') -> dict[str, Any]:
    """Read-only re-verification of signed -> unsigned -> imported GitHub receipt.

    Independently verify the signed file via local_verify (apksigner/bundletool,
    pinned LIFE certificate). Never infer protected ref, GitHub attestations,
    provenance renewal or qualified release from this registry chain.
    """
    root = root.resolve()
    if not isinstance(registry_id, str) or not _ID.fullmatch(registry_id):
        raise GitHubActionsError('G6 requires a canonical 12-hex signed registry ID')
    if fmt not in ('apk', 'aab'):
        raise GitHubActionsError('G6 supports only APK or AAB signed evidence')
    if expected_source_sha and (not isinstance(expected_source_sha, str) or
                                not _SHA40.fullmatch(expected_source_sha)):
        raise GitHubActionsError('G6 expected source SHA must be exact lowercase 40-hex')
    rows = load_registry(root).get('items', [])
    if not isinstance(rows, list):
        raise GitHubActionsError('G6 artifact registry has invalid items')
    signed = _record(rows, registry_id)
    meta = signed.get('metadata')
    if not isinstance(meta, dict):
        raise GitHubActionsError('G6 signed artifact metadata missing')
    workflow = APK_WORKFLOW if fmt == 'apk' else AAB_WORKFLOW
    state = 'locally-signed-unqualified' if fmt == 'apk' else 'locally-signed-aab-unqualified'
    if (not isinstance(meta.get('remote_repository'), str) or
            meta.get('producer') != 'devtool-native-life' or
            meta.get('state') != state or meta.get('signature_verified') is not True or
            meta.get('locally_signed') is not True or meta.get('qualified_release') is not False or
            meta.get('remote_workflow_path') != workflow or
            meta.get('remote_repository', '').casefold() != 'mikeyphw/devtool' or
            (fmt == 'aab' and meta.get('format') != 'aab')):
        raise GitHubActionsError('G6 signed metadata lacks canonical local unqualified provenance')
    source_sha = _require(meta.get('remote_head_sha'), 'source SHA', str)
    if not _SHA40.fullmatch(source_sha) or (expected_source_sha and source_sha != expected_source_sha):
        raise GitHubActionsError('G6 signed evidence source SHA differs from pinned source')
    signer = _require(meta.get('signer_sha256'), 'signer SHA-256', str)
    if not _SHA64.fullmatch(signer):
        raise GitHubActionsError('G6 signed signer certificate SHA-256 is invalid')
    for field in ('remote_run_id', 'remote_run_attempt'):
        _require(meta.get(field), field, int)
    parent_id = _require(meta.get('unsigned_registry_id'), 'unsigned registry ID', str)
    if not _ID.fullmatch(parent_id) or parent_id == registry_id:
        raise GitHubActionsError('G6 signed evidence has invalid parent ID')
    parent_row = _record(rows, parent_id)
    if not isinstance(parent_row.get('metadata'), dict):
        raise GitHubActionsError('G6 unsigned parent metadata missing')
    if fmt == 'apk':
        unsigned, original = imported_apk(root, parent_id)
        expected_package = _require(original.get('package'), 'package', str)
        if (meta.get('unsigned_sha256') != _hash(unsigned) or
            meta.get('package') != expected_package or
            meta.get('version_code') != original.get('version_code')):
            raise GitHubActionsError('G6 signed APK is not bound to its original unsigned package')
    else:
        unsigned, original = imported_aab(root, parent_id)
        expected_package = AAB_APP
    if (not isinstance(original.get('remote_repository'), str) or
            original.get('remote_repository', '').casefold() != meta['remote_repository'].casefold() or
            original.get('remote_workflow_path') != workflow or
            original.get('remote_head_sha') != source_sha or
            original.get('remote_run_id') != meta['remote_run_id'] or
            original.get('remote_run_attempt') != meta['remote_run_attempt'] or
            original.get('archive_verified') is not True or
            original.get('qualified_release') is not False or
            original.get('signature_verified') is not False):
        raise GitHubActionsError('G6 unsigned parent and signed package have conflicting provenance')
    if fmt == 'aab' and (original.get('unit_tests_verified') is not True or
                         original.get('lint_verified') is not True):
        raise GitHubActionsError('G6 AAB original test/lint evidence is incomplete')
    storage = root / '.devtool' / 'artifacts'
    for directory in (root / '.devtool', storage, storage / ('android-locally-signed' if fmt == 'apk' else 'android-aab-signed')):
        if directory.is_symlink():
            raise GitHubActionsError('G6 signed evidence path uses a symlinked storage directory')
    directory = storage / ('android-locally-signed' if fmt == 'apk' else 'android-aab-signed')
    relative = _require(signed.get('path'), 'signed path', str)
    package = root / relative
    if (Path(relative).is_absolute() or package.is_symlink() or
            package.resolve().parent != directory.resolve() or
            package.suffix != '.' + fmt):
        raise GitHubActionsError('G6 signed evidence path escapes canonical storage')
    digest = _hash(package) if _regular(package, _MAX_PACKAGE) == signed.get('size') else ''
    if (not _SHA64.fullmatch(str(signed.get('sha256', ''))) or
            not _SHA64.fullmatch(str(meta.get('signed_sha256', ''))) or
            digest != signed['sha256'] or digest != meta['signed_sha256']):
        raise GitHubActionsError('G6 signed artifact bytes differ from registry receipt')
    if identity_id and identity_id != meta.get('signing_identity_id'):
        raise GitHubActionsError('G6 selected LIFE signing identity differs from recorded signer')
    from .actions_local_release import local_verify
    # Use the exact registered identity, not the caller's currently selected default.
    inspected = local_verify(root, artifact=str(package), fmt=fmt,
                             identity_id=str(meta.get('signing_identity_id') or ''),
                             target=target, application_id=expected_package,
                             minimum_version_code=int(original['version_code']))
    if (inspected.get('artifact_sha256') != digest or
            inspected.get('signer_sha256') != signer or
            inspected.get('application_id') != expected_package or
            inspected.get('signing_identity_id') != meta.get('signing_identity_id') or
            inspected.get('local_signature_verified') is not True or
            inspected.get('qualified_release') is not False):
        raise GitHubActionsError('G6 independent LIFE signing inspection conflicts with registry identity')
    if _hash(package) != digest:
        raise GitHubActionsError('G6 signed artifact changed during verification')
    # Repeat the immutable parent check after independent signature tooling runs.
    parent_again, _ = (imported_apk(root, parent_id) if fmt == 'apk'
                       else imported_aab(root, parent_id))
    if parent_again != unsigned or _hash(unsigned) != _hash(parent_again):
        raise GitHubActionsError('G6 unsigned parent changed during independent inspection')
    return {'phase': 'ACTIONS-G6', 'operation': 'local-evidence', 'read_only': True,
            'signed_registry_id': registry_id, 'unsigned_registry_id': parent_id,
            'signed_sha256': digest, 'unsigned_sha256': _hash(unsigned),
            'format': fmt, 'application_id': expected_package,
            'version_code': int(inspected['version_code']),
            'signing_identity_id': inspected['signing_identity_id'],
            'signer_sha256': signer, 'remote_repository': meta['remote_repository'],
            'remote_run_id': meta['remote_run_id'],
            'remote_run_attempt': meta['remote_run_attempt'],
            'remote_source_sha': source_sha, 'remote_workflow_path': workflow,
            'registered_artifact_lineage_verified': True,
            'local_signature_verified': True,
            'github_artifact_import_verified': True,
            'github_provenance_verified': False, 'source_attested': False,
            'qualified_release': False, 'published': False,
            'scope': 'offline registry lineage + LIFE signature only; no online attestation, protected-ref qualification or publishing'}
