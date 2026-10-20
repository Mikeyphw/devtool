"""R05 operator actions over canonical UX01/UX02/R04 receipts.

No second journal, implicit run selection, POST or release qualification. Network
is requested explicitly by the UI; collection additionally requires a typed
artifact name and a second explicit confirmation from the selected request.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .github_actions import GitHubActionsError, GitHubActionsClient, resolve_token, _slug
from .actions_pipeline_textual import _safe_text

_REQUEST = re.compile(r'[0-9a-f]{32}\Z')
_ARTIFACT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def selected_identity(row: Mapping[str, Any], repository: str, *, kind: str) -> str:
    """Never reuse an ID from another kind/repository, even after UI refresh."""
    if not isinstance(row, Mapping) or row.get('kind') != kind:
        raise GitHubActionsError('Select an exact '+kind+' record')
    if _slug(str(row.get('repository') or '')).casefold() != _slug(repository).casefold():
        raise GitHubActionsError('Actions selection belongs to another repository')
    ident = row.get('id')
    if not isinstance(ident, str) or not _REQUEST.fullmatch(ident):
        raise GitHubActionsError('Selected Actions identity is malformed')
    if row.get('qualified_release') is True:
        raise GitHubActionsError('Untrusted local selection claims release qualification')
    return ident


def operator_review(row: Mapping[str, Any], repository: str, *, client: Any = None) -> dict[str, Any]:
    request = selected_identity(row, repository, kind='dispatch')
    # An uncorrelated request NEVER triggers a network call or redispatch.
    if row.get('run_id') is None:
        return {'kind':'review', 'request_id':request, 'state':'uncorrelated',
                'remote_verified':False, 'qualified_release':False,
                'message':'No correlated run: inspect request manually; do not redispatch.'}
    if client is None:
        token, _ = resolve_token('auto')
        if not token:
            raise GitHubActionsError('Remote review requires GitHub authentication')
        client = GitHubActionsClient(token)
    from .actions_handoff import review_run
    result = review_run(client, request, repository=repository)
    if result.get('request_id') != request or _slug(result.get('repository','')).casefold() != _slug(repository).casefold():
        raise GitHubActionsError('Review result changed selected request or repository')
    if result.get('qualified_release') is not False:
        raise GitHubActionsError('Review must not assert release qualification')
    return {'kind':'review', **result}


def operator_lineage(row: Mapping[str, Any], root: Path, repository: str, *, client: Any = None) -> dict[str, Any]:
    identity = selected_identity(row, repository, kind='pipeline')
    from .actions_lineage import lineage_snapshot
    result = lineage_snapshot(root, repository, identity, live_client=client)
    if (result.get('pipeline_id') != identity or
            _slug(result.get('repository','')).casefold() != _slug(repository).casefold()):
        raise GitHubActionsError('Lineage view changed selected pipeline or repository')
    return {'kind':'lineage', **result}


def operator_collect(row: Mapping[str, Any], root: Path, repository: str,
                     artifact_name: str, *, confirmed: bool = False,
                     client: Any = None) -> dict[str, Any]:
    request = selected_identity(row, repository, kind='dispatch')
    if not isinstance(artifact_name,str) or not _ARTIFACT.fullmatch(artifact_name):
        raise GitHubActionsError('Collection requires an exact safe artifact name')
    if not confirmed:
        raise GitHubActionsError('Collection requires separate interactive confirmation')
    if row.get('run_id') is None:
        raise GitHubActionsError('Cannot collect from an uncorrelated dispatch request')
    if client is None:
        token, _ = resolve_token('auto')
        if not token:
            raise GitHubActionsError('Collection requires GitHub authentication')
        client = GitHubActionsClient(token)
    from .actions_handoff import collect_verified
    # Canonical collector independently re-reviews the exact request and
    # archive digest after the UI confirmation; no cached review authorizes it.
    result = collect_verified(client, request, artifact_name, root, yes=True, repository=repository)
    if result.get('qualified_release') is not False:
        raise GitHubActionsError('Collection is not release qualification')
    return {'kind':'collect', **result}


def operator_workflows(repository: str, *, client: Any = None) -> dict[str, Any]:
    repository = _slug(repository)
    if client is None:
        token, _ = resolve_token('auto')
        if not token:
            raise GitHubActionsError('Workflow discovery requires GitHub authentication')
        client = GitHubActionsClient(token)
    raw = client.workflows(repository)
    if not isinstance(raw,list) or len(raw)>1000:
        raise GitHubActionsError('GitHub workflow inventory is malformed or unbounded')
    output=[]
    seen=set()
    for workflow in raw:
        if not isinstance(workflow,dict) or type(workflow.get('id')) is not int or workflow['id']<=0 or workflow['id'] in seen:
            raise GitHubActionsError('Workflow identity invalid or duplicated')
        seen.add(workflow['id'])
        output.append({'id':workflow['id'], 'name':_safe_text(workflow.get('name'),90),
                       'path':_safe_text(workflow.get('path'),150),
                       'state':_safe_text(workflow.get('state'),40)})
    return {'kind':'workflows','repository':repository,'items':output,
            'read_only':True,'remote_observed':True,'qualified_release':False}


def operator_lines(result: Mapping[str, Any], *, width: int = 44) -> str:
    """Untrusted output sanitized and bounded for 40-column Termux screens."""
    kind = result.get('kind')
    if kind=='review':
        return ('REMOTE REVIEW (GET only)\n'
                f"Request: {_safe_text(result.get('request_id'),32)}\n"
                f"State: {_safe_text(result.get('state'),32)}\n"
                f"Run: {_safe_text(result.get('run_id') or 'unbound',32)}\n"
                f"Attempt: {_safe_text(result.get('run_attempt') or 'unknown',20)}\n"
                f"Exact remote proof: {'YES' if result.get('remote_verified') is True else 'NO'}\n"
                'Release: NOT QUALIFIED\n'+_safe_text(result.get('message'),180))
    if kind=='collect':
        return ('ARCHIVE HANDOFF\n'
                f"Archive checked: {'YES' if result.get('archive_verified') is True else 'NO'}\n"
                f"SHA: {_safe_text(result.get('sha256') or result.get('archive_sha256') or 'not available',64)}\n"
                'No installation or release qualification')
    if kind=='lineage':
        from .actions_lineage import lineage_text
        return _safe_text(lineage_text(result, width=min(60,max(40,width))),3000)
    if kind=='workflows':
        lines=['GITHUB WORKFLOWS · READ ONLY']
        for item in result.get('items',[])[:30]:
            lines.append(f"{item['id']} {_safe_text(item['name'],45)}")
        if len(result.get('items',[]))>30:lines.append('Additional workflows omitted in compact view')
        return '\n'.join(lines)+'\nNo dispatch performed'
    raise GitHubActionsError('Unsupported operator display result')
