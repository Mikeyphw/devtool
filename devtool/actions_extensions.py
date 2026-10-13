"""G7 optional extensions: conservative runner discovery, CI metrics, GitLab view and
explicit unqualified GitHub draft-release asset upload.

Not a replacement GitHub provider, CI scheduler or source attestation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener

from .github_actions import (API_ORIGIN, API_VERSION, GitHubActionsClient,
                             GitHubActionsError, GitHubDispatchOutcomeUnknown,
                             _NoRedirect, _slug)

_SHA40=re.compile(r'[0-9a-f]{40}\Z')
_SHA64=re.compile(r'[0-9a-f]{64}\Z')
_TAG=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')
_ASSET=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:apk|aab)\Z')
_MAX_ASSET=32*1024*1024


def _utc(raw: Any) -> datetime | None:
    if not isinstance(raw,str) or len(raw)>40:return None
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if dt.tzinfo is None:return None
        return dt.astimezone(timezone.utc)
    except ValueError:return None


def ci_metrics(client: GitHubActionsClient, slug: str, *, limit: int=30) -> dict:
    """Observed history only. Unknown/missing durations never become zeros."""
    slug=_slug(slug)
    if type(limit) is not int or not 1<=limit<=100:
        raise GitHubActionsError('G7 metrics limit must be between 1 and 100')
    rows=client.runs(slug,limit=limit)
    if len(rows)>limit or any(not isinstance(row,dict) for row in rows):
        raise GitHubActionsError('G7 history contains unbounded or malformed entries')
    ids=set();results=Counter();times=[];attempts=[]
    for row in rows:
        identity=row.get('id'); repo=row.get('repository')
        if type(identity) is not int or identity<1 or identity in ids:
            raise GitHubActionsError('G7 historical run identity missing or duplicated')
        ids.add(identity)
        if isinstance(repo,dict) and isinstance(repo.get('full_name'),str) and repo['full_name'].lower()!=slug.lower():
            raise GitHubActionsError('G7 history contains foreign repository')
        state=row.get('status');conclusion=row.get('conclusion')
        results[conclusion if state=='completed' and conclusion in ('success','failure','cancelled','timed_out') else 'incomplete_or_other']+=1
        start=_utc(row.get('run_started_at')) or _utc(row.get('created_at'))
        end=_utc(row.get('updated_at'))
        if state=='completed' and start and end and 0<=(end-start).total_seconds()<=31*86400:
            times.append(round((end-start).total_seconds(),3))
        if type(row.get('run_attempt')) is int and row['run_attempt']>=1:
            attempts.append(row['run_attempt'])
    return {'phase':'ACTIONS-G7','operation':'metrics','repository':slug,'read_only':True,
            'observed_runs':len(rows),'outcomes':dict(results),'timing_sample_count':len(times),
            'mean_duration_seconds':round(sum(times)/len(times),3) if times else None,
            'min_duration_seconds':min(times) if times else None,
            'max_duration_seconds':max(times) if times else None,
            'observed_reruns':sum(n>1 for n in attempts),
            'rerun_sample_count':len(attempts),'observed_only':True,
            'qualified_release':False}


def trusted_runners(client:GitHubActionsClient,slug:str,*,policy_path:str='') -> dict:
    """A local allowlist NEVER makes a release runner eligible. Read-only inventory."""
    slug=_slug(slug)
    profiles=[]
    if policy_path:
        p=Path(policy_path).expanduser()
        if p.is_symlink() or not p.is_file() or p.stat().st_size>32768:
            raise GitHubActionsError('G7 runner trust policy missing, symlinked or too large')
        try:policy=json.loads(p.read_text('utf-8'))
        except (OSError,ValueError,UnicodeError):raise GitHubActionsError('G7 runner trust policy invalid') from None
        if not isinstance(policy,dict) or policy.get('schema_version')!=1 or policy.get('repository')!=slug or type(policy.get('profiles')) is not list or len(policy['profiles'])>24:
            raise GitHubActionsError('G7 runner trust policy repository or schema mismatch')
        profiles=policy['profiles']
        ids=set()
        for profile in profiles:
            if (not isinstance(profile,dict) or type(profile.get('id')) is not int or profile['id']<1
                    or profile['id'] in ids or not isinstance(profile.get('name'),str)
                    or not profile['name'] or not isinstance(profile.get('labels'),list)
                    or not profile['labels'] or len(profile['labels'])>12
                    or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',x) for x in profile['labels'])):
                raise GitHubActionsError('G7 trusted runner profile invalid')
            ids.add(profile['id'])
    data=client._get(f'/repos/{slug}/actions/runners?per_page=100')
    records=data.get('runners');total=data.get('total_count')
    if (type(total) is not int or total<0 or total>100 or not isinstance(records,list)
            or len(records)!=total or any(not isinstance(x,dict) for x in records)):
        raise GitHubActionsError('G7 runner inventory missing, malformed or truncated')
    out=[];seen=set()
    for raw in records:
        ident=raw.get('id')
        if type(ident) is not int or ident<=0 or ident in seen:raise GitHubActionsError('G7 duplicate runner identity')
        seen.add(ident)
        labels=raw.get('labels')
        if not isinstance(labels,list) or any(not isinstance(x,dict) or not isinstance(x.get('name'),str) for x in labels):
            raise GitHubActionsError('G7 runner labels malformed')
        names={x['name'] for x in labels}
        matching=[p for p in profiles if p['id']==ident and p['name']==raw.get('name') and set(p['labels']).issubset(names)]
        out.append({'id':ident,'name':raw.get('name') if isinstance(raw.get('name'),str) else None,
                    'online':raw.get('status')=='online','busy':raw.get('busy') is True,
                    'labels':sorted(names),'trust_profile_matched':len(matching)==1,
                    'eligible_for_release':False})
    return {'phase':'ACTIONS-G7','operation':'runners','repository':slug,'read_only':True,
            'runners':out,'policy_loaded':bool(policy_path),'trust_is_local_assertion_only':True,
            'release_runner_eligibility':False,'qualified_release':False}


def _asset_inventory(client:GitHubActionsClient,slug:str,release_id:int)->list[dict]:
    # GitHub's Releases assets endpoint returns a list; Actions _get expects an object.
    url=API_ORIGIN+f'/repos/{slug}/releases/{release_id}/assets?per_page=100'
    req=Request(url,method='GET',headers={
        'Accept':'application/vnd.github+json','X-GitHub-Api-Version':API_VERSION,
        'User-Agent':'devtool-g7-release-inventory/1',
        **({'Authorization':'Bearer '+client._token} if client._token else {})})
    try:
        with client._opener.open(req,timeout=client.timeout) as response:
            if response.status!=200:raise GitHubActionsError('G7 release inventory status invalid')
            raw=response.read(256*1024+1)
    except HTTPError as exc:
        raise GitHubActionsError(f'G7 release inventory HTTP {exc.code}') from None
    except (OSError,URLError,TimeoutError):
        raise GitHubActionsError('G7 release inventory unavailable') from None
    if len(raw)>256*1024:raise GitHubActionsError('G7 release inventory too large')
    try:data=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise GitHubActionsError('G7 release inventory malformed') from None
    if not isinstance(data,list) or len(data)>=100 or any(not isinstance(x,dict) for x in data):
        raise GitHubActionsError('G7 release inventory truncated or invalid')
    return data


def upload_plan(client:GitHubActionsClient,slug:str,root:Path,*,registry_id:str,tag:str,
                asset_name:str,expected_sha:str,fmt:str='apk') -> tuple[dict,Path]:
    from .actions_local_evidence import signed_local_evidence
    slug=_slug(slug)
    if not _ASSET.fullmatch(asset_name) or asset_name != Path(asset_name).name:
        raise GitHubActionsError('G7 asset name must be canonical APK/AAB basename')
    if not _SHA40.fullmatch(expected_sha):raise GitHubActionsError('G7 exact source SHA required')
    if fmt not in ('apk','aab') or not asset_name.endswith('.'+fmt):
        raise GitHubActionsError('G7 upload format does not match asset name')
    evidence=signed_local_evidence(root,registry_id=registry_id,fmt=fmt,expected_source_sha=expected_sha)
    if evidence.get('remote_repository','').lower()!=slug.lower():
        raise GitHubActionsError('G7 signed candidate belongs to different GitHub repository')
    release=client._get(f'/repos/{slug}/releases/tags/{tag}') if _TAG.fullmatch(tag) else {}
    rid=release.get('id')
    if (type(rid) is not int or rid<1 or release.get('tag_name')!=tag
            or release.get('draft') is not True or release.get('prerelease') is not True):
        raise GitHubActionsError('G7 upload requires existing draft prerelease tag')
    ref=client._get(f'/repos/{slug}/git/ref/tags/{tag}')
    commit=ref.get('object')
    if not isinstance(commit,dict) or commit.get('type')!='commit' or commit.get('sha')!=expected_sha:
        raise GitHubActionsError('G7 existing draft tag source commit mismatch')
    raw=_asset_inventory(client,slug,rid)
    if any(not isinstance(a,dict) for a in raw) or len(raw)>=100 or any(x.get('name')==asset_name for x in raw):
        raise GitHubActionsError('G7 release assets invalid, incomplete or duplicate')
    from .artifact_registry import load_registry
    records=load_registry(root).get('items',[])
    matches=[i for i in records if isinstance(i,dict) and i.get('id')==registry_id]
    if len(matches)!=1:raise GitHubActionsError('G7 registered signed artifact not unique')
    asset=root/matches[0]['path']
    if (asset.is_symlink() or not asset.is_file() or not 0<asset.stat().st_size<=_MAX_ASSET
            or hashlib.sha256(asset.read_bytes()).hexdigest()!=evidence['signed_sha256']):
        raise GitHubActionsError('G7 signed asset changed before upload')
    return ({'phase':'ACTIONS-G7','operation':'publish-plan','repository':slug,
             'draft_release_id':rid,'tag':tag,'asset_name':asset_name,
             'asset_sha256':evidence['signed_sha256'],'expected_source_sha':expected_sha,
             'signed_registry_id':registry_id,'read_only':True,
             'requires_yes':True,'requires_unqualified_upload_opt_in':True,
             'source_attested':False,'qualified_release':False,'published':False},asset)


def gitlab_pipelines(*,project_id:int,token:str,limit:int=20,opener:Any=None)->dict:
    """Read-only GitLab.com catalog, not a GitHub-source attestation."""
    if type(project_id) is not int or project_id<=0 or type(limit) is not int or not 1<=limit<=50:
        raise GitHubActionsError('G7 GitLab project ID/limit invalid')
    if not token or '\n' in token or '\r' in token:
        raise GitHubActionsError('G7 GitLab token missing or malformed')
    url=f'https://gitlab.com/api/v4/projects/{project_id}/pipelines?per_page={limit}'
    handler=opener or build_opener(_NoRedirect())
    req=Request(url,method='GET',headers={'PRIVATE-TOKEN':token,'User-Agent':'devtool-g7-gitlab-readonly/1'})
    try:
        with handler.open(req,timeout=15) as response:
            if response.status!=200:raise GitHubActionsError('GitLab API unexpected response status')
            data=response.read(256*1024+1)
    except HTTPError as exc:
        raise GitHubActionsError(f'GitLab API HTTP {exc.code}; check project access') from None
    except (OSError,URLError,TimeoutError):
        raise GitHubActionsError('GitLab API connection failed; no fallback to GitHub') from None
    if len(data)>256*1024:raise GitHubActionsError('G7 GitLab response too large')
    try:rows=json.loads(data)
    except (ValueError,UnicodeDecodeError):raise GitHubActionsError('GitLab API invalid JSON') from None
    if not isinstance(rows,list) or len(rows)>limit:raise GitHubActionsError('GitLab pipeline list malformed or unbounded')
    output=[]
    for row in rows:
        if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0
                or type(row.get('project_id')) is not int or row['project_id']!=project_id
                or not isinstance(row.get('sha'),str) or not _SHA40.fullmatch(row['sha'])):
            raise GitHubActionsError('G7 GitLab pipeline identity/source invalid')
        output.append({'id':row['id'],'status':row.get('status'),'sha':row['sha'],'ref':row.get('ref'),
                       'created_at':row.get('created_at'),'updated_at':row.get('updated_at')})
    return {'phase':'ACTIONS-G7','operation':'gitlab-pipelines','project_id':project_id,
            'read_only':True,'pipelines':output,'gitlab_provenance_verified':False,
            'github_provenance_verified':False,'qualified_release':False,
            'scope':'independent GitLab.com observation only; no GitHub identity mapping'}


def publish_asset(client:GitHubActionsClient,slug:str,root:Path,*,registry_id:str,tag:str,
                  asset_name:str,expected_sha:str,fmt:str='apk',yes:bool=False,
                  allow_unqualified:bool=False)->dict:
    if not (yes and allow_unqualified):
        raise GitHubActionsError('G7 publish requires --yes and --allow-unqualified-upload')
    if not client._token:
        raise GitHubActionsError('G7 GitHub write token required for explicit upload')
    plan,asset=upload_plan(client,slug,root,registry_id=registry_id,tag=tag,
                           asset_name=asset_name,expected_sha=expected_sha,fmt=fmt)
    content=asset.read_bytes()
    if len(content)>_MAX_ASSET or hashlib.sha256(content).hexdigest()!=plan['asset_sha256']:
        raise GitHubActionsError('G7 signed artifact changed before publishing')
    url=('https://uploads.github.com/repos/'+_slug(slug)+
         '/releases/'+str(plan['draft_release_id'])+'/assets?name='+quote(asset_name,safe=''))
    req=Request(url,data=content,method='POST',headers={
        'Accept':'application/vnd.github+json','X-GitHub-Api-Version':API_VERSION,
        'Authorization':'Bearer '+client._token,
        'User-Agent':'devtool-g7-opt-in-asset-upload/1',
        'Content-Type':'application/octet-stream'})
    try:
        with client._opener.open(req,timeout=max(client.timeout,30.0)) as response:
            if response.status!=201:
                raise GitHubDispatchOutcomeUnknown('G7 upload returned unexpected status; outcome unknown, inspect draft before retry')
            raw=response.read(65537)
    except HTTPError as exc:
        if exc.code in (401,403,404,422):
            raise GitHubActionsError(f'G7 upload rejected (HTTP {exc.code}); inspect draft release') from None
        raise GitHubDispatchOutcomeUnknown(f'G7 upload HTTP {exc.code}; outcome uncertain, inspect release') from None
    except (OSError,URLError,TimeoutError):
        raise GitHubDispatchOutcomeUnknown('G7 upload transport failed; outcome unknown, inspect release before retry') from None
    if len(raw)>65536:
        raise GitHubDispatchOutcomeUnknown('G7 upload response too large; inspect remote draft before retry')
    try:obj=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        raise GitHubDispatchOutcomeUnknown('G7 upload response invalid; inspect draft before retry') from None
    if (not isinstance(obj,dict) or type(obj.get('id')) is not int or obj['id']<=0
            or obj.get('name')!=asset_name or obj.get('size')!=len(content)):
        raise GitHubDispatchOutcomeUnknown('G7 upload receipt mismatch; inspect draft before retry')
    digest=obj.get('digest')
    if digest is not None and digest!='sha256:'+plan['asset_sha256']:
        raise GitHubDispatchOutcomeUnknown('G7 uploaded asset digest mismatch; inspect remote before retry')
    return {**plan,'operation':'publish','read_only':False,'published':False,
            'upload_accepted':True,'asset_id':obj['id'],'remote_digest_verified':digest is not None,
            'upload_result':'accepted; independent remote re-download verification not performed',
            'qualified_release':False}
