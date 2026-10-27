"""RM-03 local, read-only, journal-reconciled EXO / remote lineage.

No HTTP calls, mutation, new dispatch, event synthesis or trust promotion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .remote_evidence_jobs import RemoteEvidenceError, verify_local_evidence

RUN_ID = re.compile(r'[A-Za-z0-9_.-]{1,160}\Z')
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'blocked', 'skipped'})
MAX_SNAPSHOT = 16 * 1024 * 1024
MAX_JOURNAL = 64 * 1024 * 1024


class LineageError(ValueError):
    pass


def _json_file(path: Path, limit: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise LineageError('execution snapshot is missing, symlinked or oversized')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise LineageError('execution snapshot is unreadable') from exc
    if not isinstance(data, dict):
        raise LineageError('execution snapshot is not an object')
    return data


def _local_journal(path: Path, run_id: str) -> tuple[dict[str, dict[str, Any]], bool, int]:
    """Return unique observed terminal events; reject duplicates/conflicts."""
    if not path.exists():
        return {}, False, 0
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JOURNAL:
        raise LineageError('unsafe EXO event journal')
    terminals: dict[str, dict[str, Any]] = {}
    last_seq = -1
    try:
        with path.open(encoding='utf-8') as reader:
            for number, line in enumerate(reader, 1):
                if number > 100000:
                    raise LineageError('EXO event journal too long')
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or event.get('run_id') != run_id:
                    raise LineageError('EXO journal contains wrong run or event')
                seq = event.get('sequence')
                if type(seq) is not int or seq <= last_seq:
                    raise LineageError('EXO journal sequence is not strictly increasing')
                last_seq = seq
                if event.get('event_type') == 'node.finished':
                    node_id = event.get('node_id')
                    if not isinstance(node_id, str) or not node_id:
                        raise LineageError('EXO terminal missing node ID')
                    if node_id in terminals:
                        raise LineageError('duplicate EXO node terminal event')
                    terminals[node_id] = event
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LineageError('EXO journal interrupted or malformed; no terminal inferred') from exc
    return terminals, True, last_seq


def _identity(source: str, target: str, workflow: str, node: str, attempt: int) -> str:
    data = json.dumps([source, target, workflow, node, attempt], separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def project_execution_lineage(repo: Path, run_id: str, *,
                              receipt_paths: tuple[str, ...] = (),
                              projection_root: Path | None = None) -> dict[str, Any]:
    """Query persisted canonical EXO state; unknown remains unknown."""
    if not RUN_ID.fullmatch(run_id):
        raise LineageError('invalid EXO run ID')
    repo = Path(repo).resolve()
    if projection_root is None:
        from .execution_scheduler_truth import run_projection_directory, execution_history_root
        live = run_projection_directory(run_id)
        historical = execution_history_root() / run_id
        directory = live if (live / 'execution.json').is_file() else historical
    else:
        directory = Path(projection_root) / run_id
    if directory.is_symlink():
        raise LineageError('execution directory is symlinked')
    data = _json_file(directory / 'execution.json', MAX_SNAPSHOT)
    if data.get('kind') != 'devtool.execution.snapshot' or data.get('run_id') != run_id or data.get('schema_version') != 1:
        raise LineageError('execution snapshot identity/schema mismatch')
    execution = data.get('execution')
    if not isinstance(execution, dict) or execution.get('run_id') != run_id:
        raise LineageError('EXO snapshot does not bind run identity')
    nodes = execution.get('nodes')
    if not isinstance(nodes, list) or len(nodes) > 10000:
        raise LineageError('EXO nodes invalid or oversized')
    terminals, journal_present, last_seq = _local_journal(directory / 'execution-events.jsonl', run_id)
    snapshot_seq = data.get('sequence')
    journal_current = journal_present and last_seq == snapshot_seq
    seen: set[str] = set()
    projected = []
    for item in nodes:
        if not isinstance(item, dict) or not isinstance(item.get('node_id'), str):
            raise LineageError('invalid EXO node')
        key = item['node_id']
        if not key or key in seen:
            raise LineageError('duplicated EXO node ID')
        seen.add(key)
        meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        repository_identity = execution.get('repository_identity') or {}
        source = str(meta.get('source_commit') or meta.get('workflow_source_commit') or repository_identity.get('commit') or repository_identity.get('head') or '')
        target = str(meta.get('workflow_target') or meta.get('target') or '')
        workflow = str(meta.get('workflow_name') or meta.get('workflow') or '')
        attempt = meta.get('workflow_attempt', 1)
        if type(attempt) is not int or attempt < 1:
            raise LineageError('invalid node attempt')
        observed_terminal = terminals.get(key)
        status = str(item.get('status') or 'unknown')
        if status not in TERMINAL | {'planned', 'pending', 'ready', 'running', 'unknown'}:
            raise LineageError('unsupported EXO node state')
        if observed_terminal and status in TERMINAL and observed_terminal.get('status') != status:
            raise LineageError('EXO snapshot/journal terminal contradiction')
        # A snapshot may be ahead of its journal (interrupted flush); do not
        # claim a terminal was observed if journal is missing or incomplete.
        lifecycle = 'OBSERVED_TERMINAL' if journal_current and observed_terminal else (
                    'SNAPSHOT_ONLY' if status in TERMINAL else 'UNRESOLVED')
        projected.append({
            'node_id': key,
            'canonical_identity': _identity(source, target, workflow, key, attempt),
            'source_commit': source or None, 'target': target or None, 'workflow': workflow or None,
            'attempt': attempt, 'status': status,
            'lifecycle_evidence': lifecycle, 'duration_seconds': item.get('duration_seconds'),
            'started_at': item.get('started_at') or None, 'finished_at': item.get('finished_at') or None,
            'depends_on': list(item.get('depends_on') or []),
            'remote': {'state': 'UNKNOWN', 'reason': 'no correlated evidence'},
            'artifacts': list(item.get('artifact_ids') or []),
        })
    # Reuse the existing persisted federation/pipeline reference when present.
    # Identity edges are observations, not verification of the other owner.
    inherited = execution.get('run_lineage')
    inherited = inherited if isinstance(inherited, dict) else {}
    refs = {
        key: (str(inherited[key]) if isinstance(inherited.get(key), (str, int))
              and len(str(inherited[key])) <= 160 else None)
        for key in ('pipeline_id', 'federation_ref', 'parent_run_id', 'source_lock_digest')
    }
    by_id = {n['node_id']: n for n in projected}
    for receipt_path in receipt_paths:
        # Never promote arbitrary supplied JSON to remote truth: run the same
        # source/run/attempt/byte verifier used by native evidence jobs.
        from .remote_evidence_jobs import _read
        receipt = _read(repo, receipt_path)
        node_id = receipt.get('exo_node_id')
        if node_id not in by_id:
            raise LineageError('remote receipt references unknown EXO node')
        node = by_id[node_id]
        if not node['source_commit'] or node['source_commit'] != receipt.get('source_commit'):
            raise LineageError('remote receipt source unknown or differs from EXO source')
        job_kind = str(receipt.get('job_kind') or 'remote-run-correlated')
        try:
            bound = verify_local_evidence(repo, job_kind, receipt_path,
                                          expected_run=run_id, expected_node=node_id)
        except RemoteEvidenceError as exc:
            raise LineageError(str(exc)) from exc
        if node['remote']['state'] != 'UNKNOWN':
            raise LineageError('duplicate remote evidence for EXO node')
        node['remote'] = {
            'state': 'LOCAL_CORRELATION_ONLY', 'request_id': bound['request_id'],
            'repository': bound['repository'], 'run_id': bound['remote_run_id'],
            'attempt': bound['remote_attempt'], 'observation': bound['remote_observation'],
            'artifact_sha256_verified_locally': bound['sha256_verified_locally'],
            'remote_authenticated': False, 'trusted_producer_verified': False,
        }
    return {
        'schema_version': 1, 'kind': 'devtool.execution-lineage.v2',
        'read_only': True, 'exo_run_id': run_id,
        'execution_status': execution.get('status') or 'unknown',
        'journal_present': journal_present, 'journal_current': journal_current,
        'snapshot_sequence': snapshot_seq, 'journal_sequence': last_seq if journal_present else None,
        'nodes': projected, 'pipeline_federation_refs': refs, 'remote_authenticated': False,
        'trusted_producer_verified': False, 'device_qualified': False,
        'release_qualified': False,
    }
