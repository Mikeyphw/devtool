"""NEXT-02 cross-owner read-only lineage join, without authority promotion.

Inputs are outputs of existing R04 Actions and R06C federation projectors.
This combines *identity only*. Neither supplied JSON nor an offline journal is
accepted as independent CI, device or release proof.
"""
from __future__ import annotations

from typing import Any, Mapping
from .evidence_kernel import DOMAINS, EvidenceError, _sha


def project_lineage(actions: Mapping[str, Any], federation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(actions, dict) or actions.get('phase') != 'ACTIONS-R04' or actions.get('read_only') is not True:
        raise EvidenceError('lineage source must be the canonical read-only R04 projection')
    source = _sha(actions.get('source_sha'), 'actions source')
    if not isinstance(actions.get('repository'), str) or not actions['repository']:
        raise EvidenceError('Actions repository identity missing')
    rows = actions.get('rows')
    if not isinstance(rows, list) or len(rows) > 10000:
        raise EvidenceError('lineage rows missing or oversized')
    projected = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in seen:
            raise EvidenceError('lineage node identity duplicated')
        seen.add(row['id'])
        state = row.get('state')
        if state not in ('NOT_STARTED','LOCAL_OBSERVED','PREFLIGHT_BLOCKED','DISPATCH_UNCERTAIN',
                         'WAITING_REMOTE','CI_FAILED','CI_PASS_UNVERIFIED','CI_PASS_REMOTE_OBSERVED',
                         'ARCHIVE_NOT_VERIFIED','ARCHIVE_LOCAL_VERIFIED','UNKNOWN'):
            raise EvidenceError('unknown R04 lineage status')
        run = row.get('run_id')
        attempt = row.get('run_attempt')
        if run is not None and (type(run) is not int or run <= 0 or type(attempt) is not int or attempt <= 0):
            raise EvidenceError('lineage run missing correlated attempt')
        kind = row.get('kind')
        if kind not in ('local-git','local-invoke','github-dispatch','github-result','github-archive'):
            raise EvidenceError('unknown R04 node kind')
        # These labels describe observed evidence, not certified releases.
        domain = 'ci_presence' if kind == 'github-dispatch' else (
            'build' if kind == 'github-result' else 'artifact_integrity' if kind == 'github-archive' else 'source')
        observed = 'BLOCKED' if state in ('PREFLIGHT_BLOCKED','CI_FAILED') else (
            'NOT_RUN' if state == 'NOT_STARTED' else 'WAITING' if state in ('DISPATCH_UNCERTAIN','WAITING_REMOTE') else 'UNKNOWN')
        if state == 'ARCHIVE_LOCAL_VERIFIED' and domain == 'artifact_integrity':
            observed = 'PASS'  # existing R04 validated real local ZIP bytes, not producer authenticity
        projected.append({'id':row['id'], 'kind':kind, 'source_commit':source,
            'exo_run_id':row.get('exo_run_id'), 'request_id':row.get('request_id'),
            'run_id':run, 'attempt':attempt, 'observation':state,
            'evidence_domain':domain, 'domain_state':observed,
            'trusted_producer_verified':False})
    fed = {'state':'NOT_RUN','node_count':0,'cross_repository_edge_count':0,
           'source_lock_digest':None,'authority':'not supplied'}
    if federation is not None:
        if not isinstance(federation, dict) or federation.get('kind')!='federation_producer_consumer_dag_contract' or federation.get('schema_version')!=1:
            raise EvidenceError('federation requires canonical R06C DAG contract')
        nodes=federation.get('nodes'); edges=federation.get('cross_repository_edges')
        if not isinstance(nodes,list) or not isinstance(edges,list):
            raise EvidenceError('federation nodes/edges invalid')
        if len(nodes)>10000 or len(edges)>10000:
            raise EvidenceError('federation graph oversized')
        # Federation source lock is not necessarily identical to an Actions
        # source commit; do not invent a cross-repository identity join.
        fed = {'state':'UNKNOWN','node_count':len(nodes),'cross_repository_edge_count':len(edges),
               'source_lock_digest':federation.get('source_lock_digest'),
               'authority':'local declaration only; external source join unverified'}
    return {'schema_version':1,'kind':'devtool.readonly-cross-owner-lineage',
            'source':{'repository':actions['repository'],'commit':source},
            'actions_pipeline_id':actions.get('pipeline_id'),'rows':projected,
            'federation':fed,'domains':{d:{'state':'UNKNOWN','authority':''} for d in DOMAINS},
            'read_only':True,'remote_authenticated':False,'device_qualified':False,
            'formal_release_qualified':False}
