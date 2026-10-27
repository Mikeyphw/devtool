"""NEXT-02 read-only typed artifact integrity projection over Devtool registry.

Never calls artifact_registry.verify_record (which appends audit events). A
verified digest is integrity, not authorship, identity, a signature or release.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from .actions_lineage import _safe_registry

MAX_SIZE = 512 * 1024 * 1024

class ArtifactEvidenceError(ValueError):
    pass


def _checked_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ArtifactEvidenceError('artifact path is missing or unsafe')
    candidate = Path(relative)
    if candidate.is_absolute() or any(x in ('..', '.') for x in candidate.parts):
        raise ArtifactEvidenceError('artifact must be inside this repository')
    cur = root
    for part in candidate.parts:
        cur = cur / part
        if cur.is_symlink():
            raise ArtifactEvidenceError('artifact path contains a symlink')
    if not cur.is_file():
        raise ArtifactEvidenceError('artifact file is unavailable')
    if cur.stat().st_size > MAX_SIZE:
        raise ArtifactEvidenceError('artifact exceeds verification size limit')
    return cur


def typed_artifact_verify(root: Path, selector: str, *, repository: str = '',
                          commit: str = '', run_id: int | None = None,
                          attempt: int | None = None) -> dict[str, Any]:
    """Read-only existing-record selection and exact metadata matching."""
    root = Path(root).resolve()
    if (run_id is None) != (attempt is None) or (run_id is not None and
        (type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0)):
        raise ArtifactEvidenceError('remote run and attempt must be supplied together and positive')
    try:
        rows = _safe_registry(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactEvidenceError('registered artifact inventory cannot be trusted: '+str(exc)) from None
    found = [item for item in rows if isinstance(item, dict) and
             (item.get('id') == selector or item.get('path') == selector)]
    if len(found) != 1:
        raise ArtifactEvidenceError('artifact must resolve to exactly one existing registry record')
    item = found[0]
    digest = item.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest):
        raise ArtifactEvidenceError('record lacks an exact producer digest')
    metadata = item.get('metadata') or {}
    if not isinstance(metadata, dict):
        raise ArtifactEvidenceError('artifact producer metadata is invalid')
    checks = (('remote_repository', repository), ('remote_head_sha', commit),
              ('remote_run_id', run_id), ('remote_run_attempt', attempt))
    for key, expected in checks:
        if expected not in ('', None) and metadata.get(key) != expected:
            raise ArtifactEvidenceError('artifact producer identity mismatch: '+ key)
    path = _checked_path(root, item.get('path'))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    actual = h.hexdigest()
    if actual != digest:
        raise ArtifactEvidenceError('artifact digest mismatch')
    # Never run destructive/extracting verification in the aggregator.
    # Existing producer-specific verifiers remain authoritative for packages.
    return {'schema_version': 1, 'kind':'devtool.artifact-integrity-projection',
            'registry_id':item.get('id'), 'path':item['path'],
            'artifact_type':item.get('type') or path.suffix.lstrip('.'),
            'role':metadata.get('role') or 'unspecified',
            'sha256':digest, 'integrity':{'state':'PASS', 'sha256_verified':True},
            'producer_identity_matched':True if any(v not in ('',None) for _,v in checks) else None,
            'authenticity':{'state':'UNKNOWN','reason':'registry digest does not authenticate the producer'},
            'signature':{'state':'UNKNOWN','reason':'signer identity not verified by this reader'},
            'remote_verified':False, 'device_qualified':False,
            'formal_release_qualified':False, 'read_only':True}
