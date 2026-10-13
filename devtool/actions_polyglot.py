"""Original ACTIONS-09 / G5: provider-native polyglot CI matrix assurance.

No second GitHub client, network downloader, scheduler or artifact registry.  Each
matrix cell is test evidence only, not source attestation or a signed release.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .actions_artifacts import _metadata, download_artifact, verify_download, check_zip
from .actions_dispatch import dispatch_workflow
from .actions_runs import _normalize_run
from .artifact_registry import register_paths
from .github_actions import GitHubActionsError, GitHubActionsClient, _slug

WORKFLOW='devtool-polyglot-ci.yml'
WORKFLOW_PATH='.github/workflows/'+WORKFLOW
LANGUAGES=('python','go','rust','java')
SYSTEMS=('ubuntu-latest','windows-latest')
SHA40=re.compile(r'[0-9a-fA-F]{40}\Z')
SHA64=re.compile(r'[0-9a-f]{64}\Z')

def dimensions(raw: str, options: tuple[str,...]) -> list[str]:
    """Only fixed strings in canonical order; no expression injection into matrix."""
    if not isinstance(raw,str) or not raw.strip():
        raise GitHubActionsError('CI matrix dimension must be provided')
    entries=[s.strip() for s in raw.split(',')]
    if (not entries or any(s not in options for s in entries)
            or len(entries)!=len(set(entries)) or len(entries)>len(options)):
        raise GitHubActionsError('CI matrix has unsupported or duplicate values')
    return [x for x in options if x in entries]

def expected_cells(languages: list[str], systems: list[str]) -> set[tuple[str,str]]:
    return {(a,b) for a in languages for b in systems}

def artifact_name(language: str, system: str, attempt: int) -> str:
    return f'devtool-g5-{language}-{system}-attempt{attempt}'

def ci_dispatch(client: GitHubActionsClient, slug: str, root: Path, *, ref: str,
                languages: str, systems: str, execute: bool=False) -> dict:
    from .actions_android import _git
    slug=_slug(slug)
    lang=dimensions(languages,LANGUAGES)
    oses=dimensions(systems,SYSTEMS)
    sha=_git(root,'rev-parse','HEAD')
    if not SHA40.fullmatch(sha) or _git(root,'status','--porcelain','--untracked-files=normal'):
        raise GitHubActionsError('G5 CI requires a clean committed local checkout')
    inputs={'languages':json.dumps(lang,separators=(',',':')),
            'operating_systems':json.dumps(oses,separators=(',',':'))}
    preview=dispatch_workflow(client,slug,WORKFLOW,ref,inputs,execute=False)
    if (preview.get('workflow_path')!=WORKFLOW_PATH or
            str(preview.get('pre_dispatch_sha','')).lower()!=sha.lower()):
        raise GitHubActionsError('G5 CI remote source/workflow differs from local HEAD')
    payload={'phase':'ACTIONS-G5','operation':'polyglot-ci',
             'languages':lang,'operating_systems':oses,'matrix_cells':len(lang)*len(oses),
             'repository':slug,'ref':ref,'source_sha':sha.lower(),
             'plan':preview,'dispatched':False,'qualified_release':False}
    if execute:
        receipt=dispatch_workflow(client,slug,WORKFLOW,ref,inputs,execute=True)
        payload.update(dispatch=receipt,dispatched=True)
        if str(receipt.get('pre_dispatch_sha','')).lower()!=sha.lower():
            payload['source_binding']='not-confirmed'
            payload['warning']='Source ref moved before dispatch; no CI qualification'
    return payload

def _remote(client: GitHubActionsClient, slug: str, run_id: int, expected_sha: str) -> dict:
    slug=_slug(slug)
    if not SHA40.fullmatch(expected_sha):
        raise GitHubActionsError('G5 requires exact expected 40-character commit SHA')
    run=_normalize_run(client.get_run(slug,run_id),slug,run_id)
    wf=client.workflow(slug,WORKFLOW)
    if (run.get('workflow_id')!=wf.get('id') or wf.get('path')!=WORKFLOW_PATH
            or str(run.get('head_sha','')).lower()!=expected_sha.lower()
            or type(run.get('run_attempt')) is not int or run['run_attempt']<1
            or run.get('event') not in ('workflow_dispatch','workflow_call')):
        raise GitHubActionsError('G5 run workflow, attempt, event or source does not match')
    return run

def _jobs(client: GitHubActionsClient, slug: str, run: dict, cells: set[tuple[str,str]]) -> dict:
    jobs=client.run_attempt_jobs(slug,run['id'],run['run_attempt'])
    if not jobs or len(jobs)>=200:
        raise GitHubActionsError('G5 jobs missing or attempt job list truncated')
    by_cell={}
    for job in jobs:
        if type(job.get('run_attempt')) is not int or job['run_attempt']!=run['run_attempt']:
            raise GitHubActionsError('G5 job belongs to another attempt')
        title=job.get('name')
        if not isinstance(title,str):raise GitHubActionsError('Invalid G5 matrix job name')
        match=re.fullmatch(r'polyglot \((python|go|rust|java), (ubuntu-latest|windows-latest)\)',title)
        if not match:raise GitHubActionsError('Unexpected job in G5 matrix workflow')
        cell=match.group(1),match.group(2)
        if cell not in cells or cell in by_cell:
            raise GitHubActionsError('Duplicate or unexpected G5 job')
        by_cell[cell]=job
    return by_cell

def ci_check(client: GitHubActionsClient, slug: str, *, run_id: int,
             expected_sha: str, languages: str, systems: str) -> dict:
    slug=_slug(slug)
    lang=dimensions(languages,LANGUAGES); oses=dimensions(systems,SYSTEMS)
    cells=expected_cells(lang,oses)
    run=_remote(client,slug,run_id,expected_sha)
    by_cell=_jobs(client,slug,run,cells)
    statuses={f'{a}/{b}':by_cell[(a,b)].get('conclusion') if (a,b) in by_cell
              else 'missing' for a,b in sorted(cells)}
    done=(run.get('status')=='completed' and run.get('conclusion')=='success'
          and set(by_cell)==cells and all(job.get('status')=='completed' and
            job.get('conclusion')=='success' for job in by_cell.values()))
    return {'phase':'ACTIONS-G5','operation':'ci-check','repository':slug,
            'run_id':run_id,'run_attempt':run['run_attempt'],
            'source_sha':expected_sha.lower(),'languages':lang,'operating_systems':oses,
            'cells':statuses,'matrix_complete':done,'qualified_release':False,
            'source_attested':False,'run_conclusion':run.get('conclusion')}

def _cell_receipt(archive: Path, *, slug: str, run: dict, language: str, system: str,
                  languages: list[str], systems: list[str]) -> dict:
    check_zip(archive)
    with zipfile.ZipFile(archive) as z:
        members=[v.filename for v in z.infolist() if not v.is_dir()]
        if members!=['receipt.json']:
            raise GitHubActionsError('G5 artifact must contain exactly one receipt.json')
        data=z.read('receipt.json')
    if len(data)>16*1024:raise GitHubActionsError('G5 CI receipt exceeds size limit')
    try:obj=json.loads(data)
    except (ValueError,UnicodeError):raise GitHubActionsError('G5 CI receipt is malformed') from None
    if not isinstance(obj,dict):raise GitHubActionsError('G5 CI receipt must be object')
    must={'schema_version':1,'kind':'devtool.actions.g5.cell','repository':slug,
          'head_sha':str(run['head_sha']).lower(),'run_id':run['id'],
          'run_attempt':run['run_attempt'],'language':language,'operating_system':system,
          'languages':languages,'operating_systems':systems,'tests_passed':1,
          'tests_failed':0,'status':'passed','qualified_release':False,
          'source_attested':False}
    for key,value in must.items():
        if type(obj.get(key)) is not type(value) or obj[key]!=value:
            raise GitHubActionsError('G5 CI receipt run, attempt, matrix or outcome mismatch')
    if not SHA64.fullmatch(str(obj.get('fixture_sha256',''))):
        raise GitHubActionsError('G5 CI receipt fixture checksum missing')
    return obj

def ci_collect(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
               expected_sha: str, languages: str, systems: str, download: bool=False,
               materialize: bool=False) -> dict:
    # Check *all* expected successful jobs before trusting any archived cell.
    check=ci_check(client,slug,run_id=run_id,expected_sha=expected_sha,
                   languages=languages,systems=systems)
    if not check['matrix_complete']:
        raise GitHubActionsError('G5 incomplete, failed or unqualified matrix cannot be collected')
    slug=_slug(slug);run=_remote(client,slug,run_id,expected_sha)
    lang=check['languages'];oses=check['operating_systems'];cells=expected_cells(lang,oses)
    arts=client.run_artifacts(slug,run_id)
    if len(arts)>=1000:raise GitHubActionsError('G5 artifact listing may be truncated')
    keyed={}
    for raw in arts:
        name=str(raw.get('name',''))
        if not name.startswith('devtool-g5-'):continue
        for a,b in cells:
            if name==artifact_name(a,b,run['run_attempt']):
                if (a,b) in keyed:raise GitHubActionsError('Duplicate G5 cell artifact')
                keyed[(a,b)]=_metadata(raw,slug,run)
                break
        else:
            raise GitHubActionsError('G5 archive contains a foreign or stale-attempt cell')
    if set(keyed)!=cells:
        raise GitHubActionsError('G5 matrix artifact set is missing or incomplete')
    from .actions_artifacts import _digest
    import hashlib
    selected=[]
    for a,b in sorted(cells):
        art=keyed[(a,b)]
        if not art['digest'] or not _digest(art['digest']):
            raise GitHubActionsError('G5 cell lacks GitHub SHA-256 digest')
        if download:
            download_artifact(client,slug,run_id,art['id'],root)
        verified=verify_download(client,slug,run_id,art['id'],root)
        receipt=_cell_receipt(Path(verified['file']),slug=slug,run=run,
                              language=a,system=b,languages=lang,systems=oses)
        selected.append({'language':a,'operating_system':b,
                         'artifact_id':art['id'],'sha256':verified['sha256'],
                         'receipt':receipt,'file':verified['file']})
    output={'phase':'ACTIONS-G5','operation':'ci-collect','repository':slug,
            'source_sha':expected_sha.lower(),'run_id':run_id,'run_attempt':run['run_attempt'],
            'matrix_complete':True,'cells':selected,'verified_artifacts':len(selected),
            'qualified_release':False,'source_attested':False,'registered':False}
    if materialize:
        # Register the original GitHub archive bytes through canonical registry;
        # no extraction, no binary execution, no release promotion.
        paths=[Path(item['file']) for item in selected]
        # Each download is already in artifact_registry via ACTIONS-04; this is
        # a second immutable CI-evidence registration, not a replacement.
        records=register_paths(root,paths,metadata={
            'producer':'github-actions','state':'polyglot-ci-verified-unqualified',
            'remote_repository':slug,'remote_run_id':run_id,'remote_run_attempt':run['run_attempt'],
            'remote_head_sha':expected_sha.lower(),'remote_workflow_path':WORKFLOW_PATH,
            'matrix_languages':lang,'matrix_operating_systems':oses,
            'matrix_complete':True,'qualified_release':False,'source_attested':False})
        if len(records)!=len(selected):
            raise GitHubActionsError('G5 CI evidence registry failed')
        output.update(registered=True,registry_ids=[r['id'] for r in records])
    return output
