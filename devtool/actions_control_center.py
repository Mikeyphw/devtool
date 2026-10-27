"""RM-16: read-only joined Actions operator / capabilities projections.

Never dispatch, download, mutate journals or claim release authority. A live
observation is explicit, remote errors remain distinguishable from local state.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import shutil
import subprocess
import textwrap
import tomllib
from typing import Any, Mapping

from .github_actions import GitHubActionsClient, GitHubActionsError, _slug, resolve_repository, resolve_token


ERROR_CODES = {
    'AUTH_MISSING': 'Authenticate to GitHub, then repeat the same read-only command',
    'WORKFLOW_MISSING': 'Inspect actions workflows; select a reviewed workflow ID',
    'REMOTE_UNAVAILABLE': 'Check connectivity and GitHub access; do not redispatch an uncertain request',
    'REQUEST_UNCERTAIN': 'Inspect the exact dispatch request ID; do not dispatch again',
    'SOURCE_DRIFT': 'Review the source commit and regenerate the plan',
    'ARTIFACT_UNVERIFIED': 'Verify the exact artifact ID, run and source SHA before using it',
    'INVALID_SCOPE': 'Select an explicitly registered repository and target',
    'UNKNOWN': 'Inspect the receipt and run a read-only diagnosis',
}


def error_info(message: str, *, target: str = '', node: str = '', receipt: str = '', path: str = '') -> dict[str, Any]:
    """Stable ErrorInfoV2 classification. Does not echo credentials or raw paths."""
    value = str(message).lower()
    if any(x in value for x in ('requires github authentication', 'authentication failed', 'credentialed read-only', 'requires authenticated')):
        code = 'AUTH_MISSING'
    elif any(x in value for x in ('workflow is not active', 'workflow not found', 'no workflow', 'does not declare workflow_dispatch')):
        code = 'WORKFLOW_MISSING'
    elif any(x in value for x in ('connection failed', 'timed out', 'not accessible', 'http 429', 'rate limit')):
        code = 'REMOTE_UNAVAILABLE'
    elif any(x in value for x in ('outcome uncertain', 'uncorrelated', 'second post', 'matching uncertain', 'do not retry')):
        code = 'REQUEST_UNCERTAIN'
    elif any(x in value for x in ('source sha', 'source commit', 'source drift', 'source mismatch')):
        code = 'SOURCE_DRIFT'
    elif any(x in value for x in ('artifact', 'archive sha', 'digest')):
        code = 'ARTIFACT_UNVERIFIED'
    elif any(x in value for x in ('target', 'repository scope', 'another repository', 'not registered')):
        code = 'INVALID_SCOPE'
    else:
        code = 'UNKNOWN'
    safe_receipt = receipt if re.fullmatch(r'[a-f0-9]{32}',receipt) else None
    safe_path = path if re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,15}',path) and '..' not in path.split('/') else None
    return {'schema':'ErrorInfoV2','code':code,'affected_target':str(target)[:100],
            'affected_node':str(node)[:100], 'affected_path':safe_path, 'receipt_id':safe_receipt,
            'safe_next_step':ERROR_CODES[code], 'retry_safe':False,
            'qualified_release':False}


def _git(root: Path, *args: str) -> str | None:
    try:
        p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.TimeoutExpired):return None
    return p.stdout.strip() if p.returncode == 0 else None


def capability_inventory(root: Path) -> dict[str, Any]:
    root=root.resolve()
    config=root/'.devtool.toml'
    if config.is_symlink() or not config.is_file() or config.stat().st_size > 2*1024*1024:
        raise GitHubActionsError('Capability inventory requires a safe, bounded .devtool.toml')
    try: raw=tomllib.loads(config.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        raise GitHubActionsError('Cannot parse repository configuration for capability inventory') from None
    if not isinstance(raw,dict):raise GitHubActionsError('Invalid Devtool configuration')
    project=raw.get('project') or {}
    targets=raw.get('targets') or raw.get('target') or {}
    if not isinstance(targets,dict):targets={}
    if not targets and project: targets={'default':project}
    workflows=set()
    jobs=set()
    for target in targets.values():
        if isinstance(target,dict):
            for key in ('workflows','workflow'):
                item=target.get(key)
                if isinstance(item,dict):workflows.update(str(k) for k in item)
            for key in ('jobs','job'):
                item=target.get(key)
                if isinstance(item,dict):jobs.update(str(k) for k in item)
    wrapper=raw.get('wrapper') or {}
    configured=wrapper.get('commands') if isinstance(wrapper,dict) else {}
    wrapper_names=sorted(str(k) for k in configured) if isinstance(configured,dict) else []
    from .core_registry import build_core_registry
    registry=build_core_registry()
    tools={t:{'available':shutil.which(t) is not None} for t in ('git','gh','python3','node','npm','go','cargo','java','gradle','shellcheck','bats')}
    return {'schema':'DevtoolCapabilitiesV1','repository_root':str(root),
            'branch':_git(root,'symbolic-ref','--short','HEAD'),
            'source_sha':_git(root,'rev-parse','HEAD'),
            'targets':sorted(str(k) for k in targets),
            'jobs':sorted(jobs),'workflows':sorted(workflows),
            'wrappers':wrapper_names,
            'actions_commands':sorted('/'.join(c.canonical_path) for c in registry.commands if c.canonical_path[0]=='actions'),
            'prerequisites':tools,'mutation_performed':False,'qualified_release':False}


def overview(root: Path, repository: str = '', *, live: bool = False,
             client: Any = None, dispatches: Path | None = None, pipelines: Path | None = None) -> dict[str, Any]:
    root=root.resolve()
    branch=_git(root,'symbolic-ref','--short','HEAD')
    sha=_git(root,'rev-parse','HEAD')
    slug=resolve_repository(repository,root)
    # Offline projection never invokes gh auth discovery: no hidden token or network work.
    import os
    has_env_auth=bool(os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'))
    from .actions_workspace import local_recent
    history=local_recent(root,slug,limit=15,dispatches=dispatches,pipelines=pipelines)
    latest=history['items'][0] if history['items'] else None
    candidates=[x for x in history['items'] if x['kind']=='dispatch' and x['run_id'] is None]
    if candidates:
        status='uncorrelated'
        code='REQUEST_UNCERTAIN'
        next_cmd='devtool actions detail --kind dispatch --identity '+candidates[0]['id']+' --json'
    elif not has_env_auth and not live:
        status='auth-not-observed'
        code='AUTH_MISSING'
        next_cmd='devtool actions diagnose --github-repo '+slug+' --auth auto'
    else:
        status='local-only'
        code=None
        next_cmd='devtool actions runs --github-repo '+slug+' --limit 10'
    remote={'state':'not-queried','run':None,'workflow_count':None,'artifact_verification':'not-queried'}
    if live:
        if client is None:
            token, _=resolve_token('auto')
            if not token:raise GitHubActionsError('Remote overview requires GitHub authentication')
            client=GitHubActionsClient(token)
        try:
            all_workflows=client.workflows(slug)
            runs=client.runs(slug,limit=5)
            remote={'state':'observed','run':runs[0] if runs else None,
                    'workflow_count':len(all_workflows),
                    'artifact_verification':'not-verified; use exact actions verify'}
        except GitHubActionsError as e:
            remote={'state':'error','error':error_info(str(e)),'run':None,
                    'workflow_count':None,'artifact_verification':'not-verified'}
            code=remote['error']['code']
            status='remote-error'
            next_cmd='devtool actions diagnose --github-repo '+slug+' --auth auto'
    return {'schema':'ActionsOverviewV1','repository':slug,'branch':branch,'source_sha':sha,
            'authentication':{'environment_token_present':has_env_auth,'session_verified':live and remote['state']=='observed'},
            'local':{'recent_count':history['total_matching'],'latest':latest,
                     'uncorrelated_request_ids':[x['id'] for x in candidates]},
            'remote':remote,'status':status,'blocker_code':code,'safe_next_command':next_cmd,
            'read_only':True,'qualified_release':False}


def overview_text(payload: Mapping[str,Any], *, width:int=40)->str:
    if type(width) is not int or not 40 <= width <= 160: raise ValueError('width must be 40..160')
    chunks=['ACTIONS OVERVIEW',
            'Repo: '+str(payload.get('repository')),
            'Branch: '+str(payload.get('branch') or 'unknown'),
            'Auth: '+('remote-verified' if payload['authentication']['session_verified'] else 'unverified'),
            'Status: '+str(payload['status']),
            'Remote: '+str(payload['remote']['state']),
            'Artifact: '+str(payload['remote']['artifact_verification']),
            'Blocker: '+str(payload.get('blocker_code') or 'none'),
            'Next: '+str(payload['safe_next_command']),
            'Release: NOT QUALIFIED']
    return '\n'.join(line for item in chunks for line in
                     textwrap.wrap(item,width=width,break_long_words=True,break_on_hyphens=False))
