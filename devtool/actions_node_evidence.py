"""NEXT-02/03-R1: typed, bounded per-node hosted DAG evidence.

Projection of the RM-06 native executor and NEXT-02 evidence kernel, NOT a
second provider/scheduler or independent GitHub/provenance attestation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .evidence_kernel import DOMAINS, EvidenceError, validate_evidence

SCHEMA = 1
KIND = 'devtool.actions.node-typed-evidence'
_SHA40 = re.compile(r'[a-f0-9]{40}\Z')
_SHA64 = re.compile(r'[a-f0-9]{64}\Z')
_IDENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,255}\Z')
MAX_BYTES = 128 * 1024

class NodeEvidenceError(ValueError):
    """Malformed or falsely correlated node evidence; never auto-trust."""


def _assert_ident(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise NodeEvidenceError(f'{label} must be a bounded safe identifier')
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def _outputs(staging: Path, graph: Mapping[str, Any], job: Mapping[str, Any],
             source: str, run_id: int, attempt: int) -> list[dict[str, Any]]:
    paths = job['outputs']
    if not isinstance(paths, list) or len(paths) > 64 or len(paths) != len(set(paths)):
        raise NodeEvidenceError('Node declared outputs invalid or duplicated')
    if not paths:
        return []
    from .actions_graph_export import _load_manifest
    try:
        checked = _load_manifest(staging / job['id'], graph=graph, parent=job,
                                 source=source, run=str(run_id), attempt=attempt)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise NodeEvidenceError('Native handoff source/attempt/bytes rejected: ' + str(exc)[:120]) from None
    results=[]
    for rel,file in checked:
        from .actions_graph_export import _hash_file
        results.append({'role': 'node-output', 'type': 'unverified-bytes', 'path': rel,
                        'sha256': _hash_file(file), 'signature_state': 'UNKNOWN',
                        'producer': _assert_ident(job['provider'], 'provider')})
    return results


def build_node_evidence(graph: Mapping[str, Any], job: Mapping[str, Any], *,
                        source: str, source_tree: str, run_id: int, attempt: int, staging: Path,
                        repository: str = 'local/unverified') -> dict[str, Any]:
    """Project a successfully completed native node; absence is never a PASS.

    Tests are NOT_RUN unless the existing normalized-report owner separately
    produces evidence. A zero-exit test command is not a parsed test receipt.
    """
    _assert_ident(repository, 'repository')
    if not isinstance(source, str) or not _SHA40.fullmatch(source):
        raise NodeEvidenceError('Node source must be a full lowercase commit SHA')
    if not isinstance(source_tree, str) or not _SHA40.fullmatch(source_tree):
        raise NodeEvidenceError('Node source tree must be a full lowercase Git tree SHA')
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise NodeEvidenceError('Run and attempt must be positive integers')
    if job not in graph['jobs'] or sum(x['id'] == job['id'] for x in graph['jobs']) != 1:
        raise NodeEvidenceError('Unowned or ambiguous graph node')
    if not _SHA64.fullmatch(graph['graph_sha256']):
        raise NodeEvidenceError('Malformed frozen graph digest')
    from .actions_graph_export import workflow_yaml
    workflow_sha = hashlib.sha256(workflow_yaml(graph).encode()).hexdigest()
    gates = {domain: {'state': 'UNKNOWN', 'reason': 'not independently verified', 'authority': ''}
             for domain in DOMAINS}
    gates['source'] = {'state': 'PASS', 'reason': 'local Git HEAD matched runner source', 'authority': 'native_git_checkout'}
    gates['build'] = {'state': 'PASS', 'reason': 'native target-local command exited successfully', 'authority': 'native_job_runner'}
    from .actions_trx_evidence import verified_native_trx, TrxEvidenceError
    typed_artifacts = _outputs(staging, graph, job, source, run_id, attempt)
    try:
        trx_summary = verified_native_trx(staging, graph, job, source=source,
                                          run_id=run_id, attempt=attempt,
                                          artifacts=typed_artifacts)
    except TrxEvidenceError as exc:
        raise NodeEvidenceError('Native typed TRX verification refused: ' + str(exc)) from exc
    gates['test'] = ({'state': 'PASS', 'reason': 'RM-09 TRX independently parsed and staged bytes rehashed',
                      'authority': 'local_staged_trx_reparsed'} if trx_summary is not None else
                     {'state': 'NOT_RUN', 'reason': 'no parsed test report supplied for this node', 'authority': ''})
    gates['artifact_integrity'] = ({'state': 'PASS', 'reason': 'staged output bytes rehashed against native handoff', 'authority': 'local_handoff_digest'}
                                   if job['outputs'] else
                                   {'state': 'NOT_RUN', 'reason': 'no declared outputs', 'authority': ''})
    for name in ('artifact_authenticity', 'signature', 'instrumentation', 'native_device', 'consumer', 'release'):
        gates[name] = {'state': 'UNKNOWN', 'reason': 'not independently authenticated or qualified', 'authority': ''}
    identity = {'schema_version': 1, 'kind': 'devtool.typed-evidence',
       'source': {'repository': repository, 'commit': source, 'tree': source_tree,
                  'target': graph['target'], 'job': job['job'], 'node': job['node']},
       'execution': {'request_id': '', 'workflow': graph['workflow'], 'run_id': run_id,
                     'attempt': attempt, 'exo_run_id': ''},
       'host': {'os': _assert_ident(job['profile_id'], 'profile'), 'arch': '',
                'toolchain': _assert_ident(job['provider'], 'provider'), 'provisioned': False, 'authority': ''},
       'artifacts': typed_artifacts,
       'tests': (trx_summary['tests'] if trx_summary is not None else
                 {'format': 'none', 'total': 0, 'failed': 0, 'skipped': 0,
                  'report_sha256': '', 'required': False}),
       'gates': gates}
    try:
        normalized = validate_evidence(identity, expected={
            'source.commit': source, 'source.tree': source_tree,
            'execution.run_id': run_id, 'execution.attempt': attempt})['evidence']
    except EvidenceError as exc:
        raise NodeEvidenceError('Canonical evidence validation rejected node: ' + str(exc)[:150]) from None
    content = {'schema_version': SCHEMA, 'kind': KIND,
               'source_commit': source, 'source_tree': source_tree, 'review_tree': graph['consumer_tree'],
               'graph_sha256': graph['graph_sha256'], 'workflow_sha256': workflow_sha,
               'run_id': run_id, 'attempt': attempt, 'job_id': job['id'],
               'node': job['node'], 'provider': job['provider'],
               'typed_evidence': normalized, 'claimed_remote_authority': False,
               'authenticated_remote': False, 'release_qualified': False}
    return {**content, 'receipt_sha256': _digest(content)}


def verify_node_evidence(record: Any, graph: Mapping[str, Any], job: Mapping[str, Any], *,
                         source: str, source_tree: str, run_id: int, attempt: int,
                         staging: Path | None = None) -> dict[str, Any]:
    """Exact correlation; reported local PASS never upgrades trust authority."""
    if not isinstance(record, dict) or set(record) != {
        'schema_version','kind','source_commit','graph_sha256',
        'workflow_sha256','run_id','attempt','job_id','node','provider','source_tree','review_tree',
        'typed_evidence','claimed_remote_authority','authenticated_remote',
        'release_qualified','receipt_sha256'}:
        raise NodeEvidenceError('Node receipt keys are incomplete or ambiguous')
    content = {k:v for k,v in record.items() if k != 'receipt_sha256'}
    if record['receipt_sha256'] != _digest(content):
        raise NodeEvidenceError('Node receipt canonical digest mismatch')
    if job not in graph['jobs'] or sum(x['id'] == job['id'] for x in graph['jobs']) != 1:
        raise NodeEvidenceError('Node does not belong to frozen graph')
    from .actions_graph_export import workflow_yaml
    exact = {'schema_version': SCHEMA, 'kind': KIND, 'source_commit': source,
             'source_tree': source_tree, 'review_tree': graph['consumer_tree'], 'graph_sha256': graph['graph_sha256'],
             'workflow_sha256': hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(),
             'run_id': run_id, 'attempt': attempt, 'job_id': job['id'],
             'node': job['node'], 'provider': job['provider'],
             'claimed_remote_authority': False, 'authenticated_remote': False,
             'release_qualified': False}
    if any(type(record.get(k)) is not type(v) or record.get(k) != v for k,v in exact.items()):
        raise NodeEvidenceError('Node source/run/attempt/provider/graph identity mismatch')
    try:
        check = validate_evidence(record['typed_evidence'], expected={
            'source.commit': source, 'source.tree': source_tree,
            'source.target': graph['target'], 'source.job': job['job'],
            'source.node': job['node'], 'execution.workflow': graph['workflow'],
            'execution.run_id': run_id, 'execution.attempt': attempt,
            'host.toolchain': job['provider']})
    except EvidenceError as exc:
        raise NodeEvidenceError('Typed evidence identity mismatch: ' + str(exc)[:150]) from None
    canonical = check['evidence']
    from .actions_trx_evidence import verified_native_trx, TrxEvidenceError
    if (job.get('provider') == 'dotnet' and job.get('dotnet_command') == 'test'
            and staging is None):
        raise NodeEvidenceError('Native .NET test report verification requires staging')
    try:
        trx_summary = (verified_native_trx(staging, graph, job, source=source,
                         run_id=run_id, attempt=attempt,
                         artifacts=canonical['artifacts']) if staging is not None else None)
    except TrxEvidenceError as exc:
        raise NodeEvidenceError('Reparsed native TRX was refused: ' + str(exc)) from exc
    expected_tests = (trx_summary['tests'] if trx_summary is not None else
                      {'format': 'none', 'total': 0, 'failed': 0,
                       'skipped': 0, 'report_sha256': '', 'required': False})
    if canonical['tests'] != expected_tests:
        raise NodeEvidenceError('Typed test report digest/counts differ from staged TRX bytes')
    if trx_summary is not None and canonical['gates']['test'] != {
            'state': 'PASS', 'reason': 'RM-09 TRX independently parsed and staged bytes rehashed',
            'authority': 'local_staged_trx_reparsed'}:
        raise NodeEvidenceError('Typed test gate differs from reviewed TRX authority')
    required_states = {'source': 'PASS', 'build': 'PASS',
                       'test': 'PASS' if trx_summary is not None else 'NOT_RUN',
                       'artifact_integrity': 'PASS' if job['outputs'] else 'NOT_RUN'}
    for domain in DOMAINS:
        required = required_states.get(domain, 'UNKNOWN')
        if canonical['gates'][domain]['state'] != required:
            raise NodeEvidenceError('Node cannot promote unreviewed gate: ' + domain)
    if (canonical['host']['toolchain'] != job['provider'] or
            canonical['host']['os'] != job['profile_id'] or
            canonical['host']['provisioned'] is not False):
        raise NodeEvidenceError('Unreviewed host authority claim')
    arts = canonical['artifacts']
    if [a['path'] for a in arts] != job['outputs'] or any(
            a['role'] != 'node-output' or a['type'] != 'unverified-bytes' or
            a['signature_state'] != 'UNKNOWN' or a['producer'] != job['provider']
            for a in arts):
        raise NodeEvidenceError('Typed artifact list does not match frozen DAG outputs')
    if staging is not None:
        expected = _outputs(staging, graph, job, source, run_id, attempt)
        if expected != arts:
            raise NodeEvidenceError('Frozen output bytes differ from typed node evidence')
    return {'schema_version': SCHEMA, 'kind': 'devtool.actions.node-evidence-verification',
            'identity_matched': True, 'structure_valid': True,
            'trusted_producer_verified': False, 'effective_gates': check['effective_gates'],
            'remote_authenticated': False, 'release_qualified': False,
            'verified_trx': trx_summary}


def write_node_evidence(staging: Path, job_id: str, record: Mapping[str, Any]) -> Path:
    """Write only inside dedicated sibling evidence directory; never overwrite."""
    _assert_ident(job_id, 'job_id')
    folder = staging.parent / 'rm06-evidence'
    target = folder / job_id
    if staging.parent.is_symlink() or folder.is_symlink() or target.is_symlink():
        raise NodeEvidenceError('Symlink node evidence directory refused')
    target.mkdir(parents=True, exist_ok=True)
    dest = target / 'node-evidence.json'
    data = (json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    if len(data) > MAX_BYTES:
        raise NodeEvidenceError('Node evidence exceeds byte limit')
    with dest.open('xb') as fp:
        os.fchmod(fp.fileno(), stat.S_IRUSR | stat.S_IWUSR)
        fp.write(data)
    return dest
