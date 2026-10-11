"""ACTIONS-07: side-effect-free, cross-surface pipeline snapshot and text rendering.

This is a projection of ACTIONS-06 evidence, not an executor or a second run store.
TUI, desktop, Android's machine response and shell consumers use the same states.
"""
from __future__ import annotations

from typing import Any, Mapping

_ALLOWED = frozenset({'pending','running','waiting','success','failure','blocked','uncertain'})


def project_pipeline(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get('phase') != 'ACTIONS-06':
        raise ValueError('Not an ACTIONS-06 pipeline result')
    nodes = payload.get('nodes', payload.get('graph', []))
    if not isinstance(nodes, list):
        raise ValueError('Pipeline nodes must be a list')
    rows = []
    for node in nodes:
        if not isinstance(node, Mapping) or not isinstance(node.get('id'), str):
            raise ValueError('Invalid pipeline node')
        status = str(node.get('state') or 'pending')
        if status not in _ALLOWED:
            raise ValueError('Unknown pipeline state')
        row = {'id':node['id'], 'kind':str(node.get('kind') or ''),
               'state':status, 'detail':str(node.get('detail') or ''),
               'run_id':node.get('run_id'), 'artifact_id':node.get('artifact_id')}
        rows.append(row)
    success = bool(payload.get('completed_successfully')) and bool(rows) and all(n['state']=='success' for n in rows)
    blocked = any(n['state'] in ('blocked','failure','uncertain') for n in rows)
    waiting = any(n['state']=='waiting' for n in rows)
    # No auto-resume when a crash left an uncertain outgoing POST or a failed node.
    resumable = bool(payload.get('pipeline_id')) and not success and not blocked and not any(n['state']=='running' for n in rows)
    counts = {s: sum(n['state']==s for n in rows) for s in sorted(_ALLOWED)}
    return {'schema_version':1, 'phase':'ACTIONS-07', 'name':str(payload.get('name') or ''),
            'pipeline_id':str(payload.get('pipeline_id') or ''),
            'repository':str(payload.get('repository') or ''),
            'source_sha':str(payload.get('source_sha') or ''),
            'state':'success' if success else 'blocked' if blocked else 'waiting' if waiting else 'incomplete',
            'completed_successfully':success, 'qualified_release':False,
            'resume_enabled':resumable, 'dispatch_enabled':False,
            'nodes':rows, 'counts':counts, 'read_only':True}


def pipeline_text(payload: Mapping[str, Any]) -> str:
    view=project_pipeline(payload)
    parts=[f"Pipeline: {view['name'] or '(unnamed)'} [{view['state']}]",
           f"ID: {view['pipeline_id'] or '(plan; not dispatched)'}",
           f"Source: {view['source_sha'] or '(unavailable)'}",
           'Release qualification: NOT QUALIFIED']
    for node in view['nodes']:
        item=f"  {node['state'].upper():9s} {node['id']} ({node['kind']})"
        if node['detail']:
            item += ' — ' + node['detail'].replace('\n',' ')[:200]
        parts.append(item)
    if view['state']=='blocked':
        parts.append('Blocked/uncertain: manual reconciliation required; no automatic redispatch.')
    elif view['resume_enabled']:
        parts.append('Resume requires explicit --yes; pending remote work is not success.')
    return '\n'.join(parts)
