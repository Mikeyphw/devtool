"""RM-03 conservative, local-only verification for typed remote evidence nodes.

A successful verifier means that declared *local evidence* is internally
consistent. It NEVER claims a remote GitHub run, build or producer was authenticated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

KINDS = frozenset({"remote-run-correlated", "remote-build-verified", "artifact-verified"})
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECEIPT = 1024 * 1024
MAX_ARTIFACT = 256 * 1024 * 1024


class RemoteEvidenceError(ValueError):
    pass


def _positive(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise RemoteEvidenceError(f"{label} must be a positive integer")
    return value


def _str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise RemoteEvidenceError(f"{label} missing or invalid")
    return value.strip()


def _safe_file(root: Path, raw: str, *, limit: int) -> Path:
    relative = Path(_str(raw, 'receipt/artifact path'))
    if relative.is_absolute() or any(part in ('..', '') for part in relative.parts):
        raise RemoteEvidenceError('absolute/traversal evidence path forbidden')
    root = root.resolve()
    file = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RemoteEvidenceError('evidence path symlink forbidden')
    if not file.is_file() or not file.resolve().is_relative_to(root):
        raise RemoteEvidenceError('evidence path unavailable or escaping repository')
    if file.stat().st_size > limit:
        raise RemoteEvidenceError('evidence file exceeds size limit')
    return file


def _read(root: Path, raw: str) -> dict[str, Any]:
    file = _safe_file(root, raw, limit=MAX_RECEIPT)
    try:
        value = json.loads(file.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError, OSError) as exc:
        raise RemoteEvidenceError('evidence receipt is not valid JSON') from exc
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or value.get('kind') != 'devtool.remote-observation':
        raise RemoteEvidenceError('unsupported remote observation contract')
    return value


def verify_local_evidence(root: Path, kind: str, receipt_path: str, *,
                          expected_run: str = '', expected_node: str = '') -> dict[str, Any]:
    """Validate declarations; never dispatch or infer remote authentication."""
    if kind not in KINDS:
        raise RemoteEvidenceError('unknown typed evidence job')
    root = Path(root).resolve()
    receipt = _read(root, receipt_path)
    source = _str(receipt.get('source_commit'), 'source_commit').lower()
    if not SHA40.fullmatch(source):
        raise RemoteEvidenceError('source_commit must be SHA-1 hexadecimal')
    repo = _str(receipt.get('repository'), 'repository')
    request = _str(receipt.get('request_id'), 'request_id')
    github_run = _positive(receipt.get('remote_run_id'), 'remote_run_id')
    attempt = _positive(receipt.get('remote_attempt'), 'remote_attempt')
    exo_run = _str(receipt.get('exo_run_id'), 'exo_run_id')
    node = _str(receipt.get('exo_node_id'), 'exo_node_id')
    if expected_run and exo_run != expected_run:
        raise RemoteEvidenceError('EXO run ID mismatch')
    if expected_node and node != expected_node:
        raise RemoteEvidenceError('EXO node ID mismatch')
    remote = receipt.get('remote')
    if not isinstance(remote, dict):
        raise RemoteEvidenceError('remote observation missing')
    # Only independently fetched/reviewed evidence can eventually carry authority.
    # This local contract does not implement that authentication at RM-03.
    if remote.get('repository') != repo or remote.get('source_commit') != source or remote.get('run_id') != github_run or remote.get('attempt') != attempt:
        raise RemoteEvidenceError('remote observation source/run/attempt mismatch')
    observation = str(remote.get('state') or 'UNKNOWN')
    if observation not in ('UNKNOWN', 'INACCESSIBLE', 'WAITING', 'DISPATCH_UNCERTAIN', 'FAILED', 'SUCCEEDED_OBSERVED'):
        raise RemoteEvidenceError('invalid remote observation state')
    if kind != 'remote-run-correlated' and observation != 'SUCCEEDED_OBSERVED':
        raise RemoteEvidenceError('build/artifact proof requires successful remote observation')
    digest = None
    if kind in {'remote-build-verified', 'artifact-verified'}:
        build = receipt.get('build')
        if not isinstance(build, dict) or build.get('repository') != repo or build.get('source_commit') != source or build.get('run_id') != github_run or build.get('attempt') != attempt or build.get('request_id') != request:
            raise RemoteEvidenceError('build source/run/attempt/request identity mismatch')
        if build.get('outcome') != 'success':
            raise RemoteEvidenceError('build outcome not observed successful')
    if kind == 'artifact-verified':
        artifact = receipt.get('artifact')
        if not isinstance(artifact, dict) or artifact.get('repository') != repo or artifact.get('source_commit') != source or artifact.get('run_id') != github_run or artifact.get('attempt') != attempt:
            raise RemoteEvidenceError('artifact source/run/attempt identity mismatch')
        digest = _str(artifact.get('sha256'), 'artifact sha256').lower()
        if not SHA64.fullmatch(digest):
            raise RemoteEvidenceError('invalid artifact digest')
        file = _safe_file(root, str(artifact.get('path') or ''), limit=MAX_ARTIFACT)
        calculated = hashlib.sha256()
        with file.open('rb') as reader:
            for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                calculated.update(chunk)
        if calculated.hexdigest() != digest:
            raise RemoteEvidenceError('actual artifact bytes do not match digest')
    return {
        'schema_version': 1, 'kind': 'devtool.remote-evidence-verification',
        'job_kind': kind, 'ok': True, 'state': 'LOCAL_EVIDENCE_CONSISTENT',
        'exo_run_id': exo_run, 'exo_node_id': node,
        'repository': repo, 'source_commit': source, 'request_id': request,
        'remote_run_id': github_run, 'remote_attempt': attempt, 'remote_observation': observation,
        'sha256_verified_locally': digest,
        'remote_authenticated': False, 'trusted_producer_verified': False,
        'release_qualified': False,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Read-only typed remote evidence verification')
    parser.add_argument('job_kind', choices=sorted(KINDS))
    parser.add_argument('receipt')
    parser.add_argument('--root', required=True)
    parser.add_argument('--run-id', default='')
    parser.add_argument('--node-id', default='')
    args = parser.parse_args(argv)
    try:
        result = verify_local_evidence(Path(args.root), args.job_kind, args.receipt,
                                       expected_run=args.run_id, expected_node=args.node_id)
    except (RemoteEvidenceError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'state': 'BLOCKED', 'reason': str(exc),
                          'remote_authenticated': False, 'release_qualified': False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
