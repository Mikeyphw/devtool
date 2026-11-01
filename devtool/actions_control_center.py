"""RM-16: read-only joined Actions operator / capabilities projections.

Never dispatch, download, mutate journals or claim release authority. A live
observation is explicit, remote errors remain distinguishable from local state.
"""
from __future__ import annotations

from pathlib import Path
import os
import unicodedata
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


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_SAFE_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,15}\Z")
_EXPLICIT_CODES = frozenset(ERROR_CODES) | {
    'TOOL_MISSING', 'PROVIDER_INELIGIBLE', 'HOSTED_ONLY', 'WRAPPER_INVALID',
    'TEST_RECEIPT_MISSING', 'NODE_FAILED', 'DISPATCH_REJECTED',
}
_RECOVERY = {**ERROR_CODES,
    'TOOL_MISSING': 'Inspect capabilities; install the reviewed local tool or select an eligible hosted operation',
    'PROVIDER_INELIGIBLE': 'Inspect the target provider doctor and its prerequisites; do not execute automatically',
    'HOSTED_ONLY': 'Select an explicitly reviewed hosted workflow; do not invoke the local build',
    'WRAPPER_INVALID': 'Inspect the declared wrapper command and its typed binding',
    'TEST_RECEIPT_MISSING': 'Inspect the exact test receipt; NOT_RUN is not PASS',
    'NODE_FAILED': 'Inspect the failed node and its immutable evidence; do not assume other nodes failed',
    'DISPATCH_REJECTED': 'Review the API rejection; a new POST requires a separate explicit request',
}


def _safe_identity(value: object, *, repository: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    pattern = _SAFE_REPOSITORY if repository else _SAFE_ID
    return value if pattern.fullmatch(value) and '..' not in value else None


def error_info(message: str = '', *, code: str | None = None, target: str = '', node: str = '',
               receipt: str = '', path: str = '', repository: str = '', operation: str = '',
               state: str = 'UNKNOWN', evidence_scope: str = 'local-unverified') -> dict[str, Any]:
    """ErrorInfoV2-compatible read-only diagnostic with opaque classified identities.

    Explicit codes are preferred at typed call sites. Legacy text classification is a
    compatibility fallback; the raw text, credentials and absolute paths are never emitted.
    """
    value = str(message).lower()
    if code not in _EXPLICIT_CODES:
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
    if state not in ('UNKNOWN','NOT_RUN','BLOCKED','FAIL','PASS'):
        state = 'UNKNOWN'
    safe_receipt = receipt if isinstance(receipt,str) and re.fullmatch(r'[a-f0-9]{32}',receipt) else None
    safe_path = path if isinstance(path,str) and _SAFE_PATH.fullmatch(path) and '..' not in path.split('/') else None
    scope = evidence_scope if evidence_scope in ('local-unverified','source','hosted','consumer','device','release') else 'local-unverified'
    return {'schema':'ErrorInfoV2','code':code, 'severity':'error',
            'affected_repository':_safe_identity(repository,repository=True),
            'affected_target':_safe_identity(target), 'affected_node':_safe_identity(node),
            'affected_path':safe_path, 'operation':_safe_identity(operation),
            'receipt_id':safe_receipt,'receipt_path':safe_path,
            'state':state,'evidence_scope':scope,
            'documentation_anchor':'docs/NEXT10_R1_OPERATOR_CONTRACT.md#'+code.lower().replace('_','-'),
            'safe_next_step':_RECOVERY[code], 'safe_next_command':None,
            'retry_safe':False,'qualified_release':False}


def _width(width: int) -> int:
    if type(width) is not int or not 40 <= width <= 160:
        raise ValueError('width must be 40..160')
    return width


def _screen_text(value: object) -> str:
    """Opaque display value; never render free-form untrusted errors or control codes."""
    clean = ''.join(c for c in str(value) if unicodedata.category(c)[0]!='C')
    return clean if re.fullmatch(r'[A-Za-z0-9_.:/ -]{1,160}',clean) and '..' not in clean else '[redacted]'


def _wrap_lines(items: list[str], width: int) -> str:
    width=_width(width)
    return '\n'.join(line for item in items for line in
        (textwrap.wrap(item,width=width,break_long_words=True,break_on_hyphens=False) or ['']))


def diagnostic_text(info: Mapping[str,Any], *, width: int = 44) -> str:
    """Same diagnostic identity as JSON; bounded 40-column, no unsafe raw message."""
    code = info.get('code') if info.get('code') in _RECOVERY else 'UNKNOWN'
    return _wrap_lines(['DIAGNOSTIC '+code,
        'Target: '+_screen_text(info.get('affected_target') or 'unknown'),
        'State: '+_screen_text(info.get('state') or 'UNKNOWN'),
        'Receipt: '+_screen_text(info.get('receipt_id') or 'none'),
        'Next: '+_RECOVERY[code], 'Release: NOT QUALIFIED'], width)


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
    # NEXT-10-R1: independent observations. No network, version processes, builds,
    # credential materialization, journal writes or qualification inference.
    toolchains={name:{'state':'installed' if row['available'] else 'missing',
                      'source':'PATH-presence-only', 'version':None,
                      'qualified':False} for name,row in sorted(tools.items())}
    operations=[]
    for target_name, declaration in sorted(targets.items()):
        if not isinstance(declaration,dict):
            continue
        runner=str(declaration.get('runner') or project.get('runner') or 'unknown').lower()
        platform=str(declaration.get('platform') or project.get('platform') or '')
        is_android=runner in ('android','gradle') or platform=='android'
        is_hosted_only=(is_android and (
            declaration.get('actions_only') is True or
            declaration.get('gradle_execution') in ('hosted','github-actions') or
            declaration.get('build_execution') in ('hosted','github-actions')))
        required={'python':'python3','go':'go','node':'node','rust':'cargo',
                  'java':'java','gradle':'gradle','android':'gradle','shell':'sh',
                  'dotnet':'dotnet','cpp':'c++'}.get(runner)
        installed = (shutil.which(required) is not None) if required else None
        if is_hosted_only:
            local='blocked';reason='Configured GitHub-only Android build; local Gradle is not authorized'
        elif not required:
            local='unknown';reason='Runner prerequisites require owner-specific inspection'
        elif installed:
            local='unknown';reason='Tool present; provider policy and actual execution not qualified'
        else:
            local='blocked';reason='Declared runner tool is not installed locally'
        hosted='unknown' if is_android else 'not_applicable'
        hosted_reason=('Local configuration does not attest remote workflow, runner, permissions or secrets'
                       if is_android else 'No hosted operation declared for this target')
        operations.append({'target':str(target_name), 'runner':runner,
            'configuration_source':'.devtool.toml', 'configured':True,
            'toolchain':{'command':required,'state':('installed' if installed else 'missing') if installed is not None else 'unprobed',
                         'version':None,'qualified':False},
            'local':{'status':local,'reason':reason},
            'hosted':{'status':hosted,'reason':hosted_reason,
                      'configured':is_hosted_only},
            'policy':{'github_actions_only':is_hosted_only},
            'evidence':{'status':'NOT_RUN','source_sha':None,'run_id':None,
                        'run_attempt':None,'scope':'not-observed','expires_at':None},
            'authorization':{'device':'NOT_RUN','signing':'NOT_RUN','release':'NOT_RUN'},
            'qualified_release':False})
    declared_operations=[]
    for name in sorted(workflows):
        declared_operations.append({'kind':'workflow','name':name,
            'configuration_source':'.devtool.toml', 'configured':True,
            'local':{'status':'not_applicable'},'hosted':{'status':'unknown'},
            'evidence':{'status':'NOT_RUN','run_id':None,'run_attempt':None},
            'authorization':{'device':'NOT_RUN','signing':'NOT_RUN','release':'NOT_RUN'}})
    for name in wrapper_names:
        declared_operations.append({'kind':'wrapper','name':name,
            'configuration_source':'.devtool.toml', 'configured':True,
            'local':{'status':'unknown'},'hosted':{'status':'unknown'},
            'evidence':{'status':'NOT_RUN','run_id':None,'run_attempt':None},
            'authorization':{'device':'NOT_RUN','signing':'NOT_RUN','release':'NOT_RUN'}})
    inventory_v2={'schema':'DevtoolCapabilitiesV2',
        'declared_operations':declared_operations,
        'configured_providers':sorted({op['runner'] for op in operations}),
        'operations':operations,
        'toolchains':toolchains,'hosted_status':'not-queried',
        'evidence_status':'NOT_RUN','read_only':True,'qualified_release':False}
    return {'schema':'DevtoolCapabilitiesV1','capabilities_v2':inventory_v2,'repository_root':str(root),
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


def capabilities_text(payload: Mapping[str,Any], *, width: int = 44) -> str:
    """Compact projection preserving JSON state and identities without raw paths."""
    v2=payload.get('capabilities_v2') or {}
    lines=['DEVTOOL CAPABILITIES',
           'Source: '+_screen_text(str(payload.get('source_sha') or 'unknown')[:12]),
           'Evidence: '+_screen_text(v2.get('evidence_status') or 'UNKNOWN')]
    for op in v2.get('operations', []):
        lines.extend(['Target: '+_screen_text(op.get('target')),
                      'Runner: '+_screen_text(op.get('runner')),
                      'Tool: '+_screen_text(op.get('toolchain',{}).get('state')),
                      'Local: '+_screen_text(op.get('local',{}).get('status')),
                      'Hosted: '+_screen_text(op.get('hosted',{}).get('status')),
                      'Policy: '+('GitHub only' if op.get('policy',{}).get('github_actions_only') else 'target-defined'),
                      'Next: '+('Use a reviewed GitHub Actions workflow, not local Gradle' if op.get('policy',{}).get('github_actions_only') else
                               'Inspect target provider doctor before executing')])
    lines.append('Release: NOT QUALIFIED')
    return _wrap_lines(lines,width)
