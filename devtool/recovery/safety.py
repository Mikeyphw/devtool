"""Conservative, dependency-aware recovery support for RepoRebuild 1.3.1.

Source analysis is deliberately read-only. No plaintext secret values are persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

FILE_LIMIT = 512 * 1024
MAX_FILES = 10000
MAX_FINDINGS = 20000
SHA = re.compile(r'^[0-9a-f]{40}$', re.I)
WORKFLOW = re.compile(r'^\.github/workflows/[^\s]+\.ya?ml$')
REPO_USE = re.compile(r'(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?P<workflow>/\.github/workflows/[^\s@\"\']+)?@(?P<ref>[0-9a-f]{40}|[A-Za-z0-9_./-]+)', re.I)
PR_REF = re.compile(r'refs/pull/(?P<id>\d+)/(?:head|merge)')
PR_CMD = re.compile(r'\bgh\s+pr\s+(?:view|checks|checkout|close|merge|edit|reopen|ready|review)\s+(?P<id>\d+)\b')
RUN_URL = re.compile(r'(?:(?P<url>https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)))?/(?:actions/runs|actions/workflows|releases/assets)/(?P<id>\d+)')
ISSUE_CMD = re.compile(r'\bgh\s+issue\s+(?:view|edit|close|reopen)\s+(?P<id>\d+)\b')
PR_URL = re.compile(r'/pull/(?P<id>\d+)\b|/pulls/(?P<id2>\d+)\b')
REPO_ID = re.compile(r'\b(?:repository_id|repo_id|repositoryId|repoId)\s*[=:]\s*[\"\']?(?P<id>\d+)')
OAUTH = re.compile(r'(?i)(?:sub\s*:\s*repo:|repo:[\w.-]+/[\w.-]+:|repository_id|job_workflow_ref|environment:|ref:refs/)')
SECRET_EXPR = re.compile(r'\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)')
GITHUB_EXPR = re.compile(r'\$\{\{[^\n]*?(?:github\.(?:event\.pull_request|run_id|repository_id)|secrets\.|vars\.)[^\n]*?\}\}')

class SafetyError(RuntimeError):
    pass


def secret_requirements(config):
    """Return opaque names/scopes only, never actual values."""
    names=[]
    for app in ('actions','dependabot','codespaces'):
        for item in config.get('secrets',{}).get(app,[]):
            names.append((app, '', item['name']))
    for env, item in sorted(config.get('environments',{}).items()):
        for secret in item.get('secrets',[]):
            names.append(('environment', env, secret['name']))
    return sorted(set(names))


def validate_vault(vault, repo, requirements):
    """Require explicitly named JSON mappings. Check presence, never return values."""
    if not isinstance(vault,dict) or vault.get('schema')!=1 or vault.get('repo','').lower()!=repo.lower():
        raise SafetyError('vault schema/repository mismatch')
    secrets=vault.get('secrets',{})
    if not isinstance(secrets,dict):raise SafetyError('vault secrets must be an object')
    missing=[]
    for app,env,name in requirements:
        values=(secrets.get('environments',{}).get(env,{}) if app=='environment'
                else secrets.get(app,{}))
        if not isinstance(values,dict) or name not in values or not isinstance(values[name],str) or not values[name]:
            missing.append({'app':app,'environment':env,'name':name})
    return missing


def vault_value(vault,app,env,name):
    if app=='environment':return vault['secrets']['environments'][env][name]
    return vault['secrets'][app][name]


def parse_age_vault(path:Path, identity:Path, repo:str, run):
    """Decrypt age only in RAM, with no plaintext in argv/temporary files/journal."""
    for p in (path,identity):
        if not p or not p.is_file() or p.is_symlink():
            raise SafetyError('encrypted vault and age identity must be regular files')
        if p.stat().st_uid!=os.getuid() or p.stat().st_mode & 0o077:
            raise SafetyError('vault/identity must be owned by caller and mode 0600')
    if path.resolve()==identity.resolve():raise SafetyError('vault and identity paths identical')
    output=run(['age','--decrypt','--identity',str(identity),str(path)],timeout=45)
    try:
        def no_duplicates(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise SafetyError('duplicate JSON key in decrypted vault')
                d[k]=v
            return d
        obj=json.loads(output.stdout,object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError,ValueError) as exc:
        raise SafetyError('decrypted vault is not valid JSON') from exc
    if not isinstance(obj,dict) or obj.get('schema')!=1 or obj.get('repo','').lower()!=repo.lower():
        raise SafetyError('decrypted vault is bound to a different repository')
    return obj


def _git(run, mirror, *args, timeout=60):
    return run(['git','--git-dir',mirror,*args],timeout=timeout).stdout


def _reachable_commits(run, mirror, refs):
    """A SHA is safe to pin only when a restored branch/tag reaches the commit."""
    heads=[]
    for name,sha in refs.items():
        try:
            resolved=_git(run,mirror,'rev-parse',f'{sha}^{{commit}}').strip()
            if SHA.fullmatch(resolved):heads.append(resolved)
        except Exception:continue
    return sorted(set(heads))


def _pin_status(run,mirror,commit,reachable):
    try:
        resolved=_git(run,mirror,'rev-parse',f'{commit}^{{commit}}').strip()
        if not SHA.fullmatch(resolved) or resolved.lower()!=commit.lower():return 'unavailable-object'
        for head in reachable:
            result=run(['git','--git-dir',mirror,'merge-base','--is-ancestor',commit,head],check=False,timeout=20)
            if result.returncode==0:return 'verified-reachable'
        return 'object-not-reachable-from-restored-refs'
    except Exception:return 'unavailable-object'


def scan_dependencies(mirror:Path, original_repo:str, refs:dict, run,original_repo_id=None):
    """Read branch-tip trees (including non-default branches), never execute source text."""
    findings=[];gaps=[];seen_blobs=set();scanned=0
    reachable=_reachable_commits(run,mirror,refs)
    prs=set();run_ids=set();wf_ids=set();secret_names=set()
    for ref,sha in sorted(refs.items()):
        if not ref.startswith(('refs/heads/','refs/tags/')):
            continue
        try:tree=_git(run,mirror,'ls-tree','-r','--long','-z',sha,timeout=180)
        except Exception as e:
            gaps.append({'ref':ref,'reason':'cannot-read-tree','error':type(e).__name__});continue
        # NUL-separated output may contain files named with whitespace.
        for item in tree.split('\0'):
            if not item:continue
            left,sep,path=item.partition('\t')
            if not sep:continue
            parts=left.split()
            if len(parts)!=4 or parts[1]!='blob':continue
            mode,_,oid,size=parts
            if oid in seen_blobs:continue
            seen_blobs.add(oid)
            if scanned>=MAX_FILES:
                gaps.append({'ref':ref,'reason':'max-file-count','count':scanned});break
            if size=='-' or int(size)>FILE_LIMIT:
                # Never claim a comprehensive scan when a relevant text file is too large.
                if path.startswith('.github/') or path.endswith(('.sh','.py','.json','.yml','.yaml','.toml','.js','.ts')):
                    gaps.append({'ref':ref,'path':path,'reason':'oversized-or-unreadable'})
                continue
            likely=(path.startswith(('.github/','scripts/','tools/')) or
                    path.endswith(('.sh','.py','.json','.yml','.yaml','.toml','.js','.ts','.md','.properties')))
            if not likely:continue
            try:text=_git(run,mirror,'cat-file','blob',oid,timeout=40)
            except Exception:
                gaps.append({'ref':ref,'path':path,'reason':'cannot-read-blob'});continue
            if '\0' in text:continue
            scanned+=1
            for ln,line in enumerate(text.splitlines(),1):
                matches=[]
                for m in REPO_USE.finditer(line):
                    if m['repo'].lower()==original_repo.lower():
                        status=(_pin_status(run,mirror,m['ref'],reachable) if SHA.fullmatch(m['ref'])
                                else 'same-repository-named-ref')
                        if status=='verified-reachable' and m['workflow']:
                            # Reachable commits can still be missing the named reusable workflow.
                            workflow=m['workflow'].lstrip('/')
                            tree=_git(run,mirror,'ls-tree',m['ref'],'--',workflow)
                            if not tree.strip():status='pinned-workflow-missing'
                        matches.append(('repository-action-ref',m.group(0),status))
                    elif SHA.fullmatch(m['ref']):
                        matches.append(('external-action-pin',m.group(0),'external-unverified'))
                for m in PR_REF.finditer(line):
                    prs.add(int(m['id']));matches.append(('pull-request-git-ref',m.group(0),'will-change'))
                for m in PR_CMD.finditer(line):
                    prs.add(int(m['id']));matches.append(('pull-request-number',m.group(0),'will-change'))
                for m in PR_URL.finditer(line):
                    n=int(m['id'] or m['id2'])
                    prefix=line[:m.start()]
                    owner_match=re.search(
                        r'https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$',
                        prefix,re.I)
                    if owner_match and owner_match.group(1).lower()!=original_repo.lower():
                        status='external-repository-unaffected'
                    else:
                        status='will-change';prs.add(n)
                    matches.append(('pull-request-number',m.group(0),status))
                for m in ISSUE_CMD.finditer(line):
                    matches.append(('issue-number',m.group(0),'will-change'))
                for m in REPO_ID.finditer(line):
                    if original_repo_id is not None and int(m['id'])==int(original_repo_id):
                        matches.append(('repository-id',m.group(0),'will-change'))
                for m in RUN_URL.finditer(line):
                    kind=('release-asset-id' if 'releases/assets/' in m.group(0) else
                          'workflow-id' if 'actions/workflows/' in m.group(0) else 'run-id')
                    explicit_repo=(f'{m["owner"]}/{m["repository"]}' if m['owner'] else None)
                    if explicit_repo and explicit_repo.lower()!=original_repo.lower():
                        # A link to a different GitHub repository does not lose
                        # its ID when ONLY the source repository is recreated.
                        status='external-repository-unaffected'
                    elif path.endswith('.md') and kind=='run-id' and m['url']:
                        # Historical hosted evidence cannot be made live again.
                        # Preserve it as an archive obligation, NOT live CI code.
                        status='historical-run-archived-only'
                    else:
                        status='will-change'
                    if status=='will-change':
                        if kind=='workflow-id':wf_ids.add(int(m['id']))
                        elif kind=='run-id':run_ids.add(int(m['id']))
                    matches.append((kind,m.group(0),status))
                for m in SECRET_EXPR.finditer(line):
                    secret_names.add(m[1])
                    if m[1] not in ('GITHUB_TOKEN','GH_TOKEN') and re.search(
                            r'(FEDERATION|(?:^|_)(?:PAT|TOKEN)(?:_|$))',m[1],re.I):
                        matches.append(('external-credential-secret',m[1],
                                        'credential-access-unverified'))
                if OAUTH.search(line):matches.append(('oidc-or-repo-identity','possible fixed OIDC or repository claim','manual-review'))
                if GITHUB_EXPR.search(line):matches.append(('dynamic-workflow-expression','dynamic GitHub expression in source','runtime-review'))
                for kind,val,status in matches:
                    if len(findings)>=MAX_FINDINGS:
                        gaps.append({'ref':ref,'path':path,'reason':'max-findings'});break
                    findings.append({'ref':ref,'path':path,'line':ln,'kind':kind,'value':val[:240],'status':status})
    return {'schema':1,'repo':original_repo,'scanned_text_blobs':scanned,'findings':findings,
            'scan_gaps':gaps,'referenced_pr_numbers':sorted(prs),
            'referenced_workflow_ids':sorted(wf_ids),'referenced_run_ids':sorted(run_ids),
            'referenced_secret_names':sorted(secret_names),
            'method':'branch-tip textual/static scan; not a complete semantic or cross-repository audit'}


def migration_plan(dependencies,objects,old_id,new_id,old_workflows,new_workflows,
                   release_asset_ids=None,ruleset_ids=None):
    """Map only provenance-bound IDs; suggest, never auto-edit historical files."""
    prs={};issues={}
    for key,value in objects.items():
        kind,_,old=key.partition(':')
        if kind in ('pr','issue'):
            (prs if kind=='pr' else issues)[old]=value.get('number')
    original={w['path']:w['id'] for w in old_workflows if 'id' in w}
    current={w['path']:w['id'] for w in new_workflows if 'id' in w}
    wf={str(oid):current.get(path) for path,oid in original.items()}
    runids={str(i):None for i in dependencies.get('referenced_run_ids',[])}
    review=[];proposals=[]
    for f in dependencies['findings']:
        if f['status'] not in ('will-change','manual-review','runtime-review',
                    'object-not-reachable-from-restored-refs','unavailable-object',
                    'pinned-workflow-missing','same-repository-named-ref',
                    'same-repository-ref','same-repository-ref-verify'):
            continue
        item={**f,'suggestion':'Review the current source and any external consumer; never rewrite historical commits.'}
        kind=f['kind'];match=None;new=None;label=None
        value=f.get('value','')
        if kind.startswith('pull-request'):
            match=PR_REF.search(value) or PR_CMD.search(value) or PR_URL.search(value)
            if match:
                old=str(next(v for v in match.groupdict().values() if v is not None))
                new=prs.get(old);label='pull_request'
        elif kind=='issue-number':
            match=ISSUE_CMD.search(value)
            if match:
                old=match['id'];new=issues.get(old);label='issue'
        elif kind in ('run-id','workflow-id','release-asset-id','repository-id'):
            if kind=='repository-id':
                new=new_id;label='repository'
            elif kind=='run-id':
                new=None;label='historical_run_unrecoverable'
            else:
                found=RUN_URL.search(value)
                old=found['id'] if found else None
                new=(wf.get(old) if kind=='workflow-id' else
                     (release_asset_ids or {}).get(old));label=kind
        if new is not None:
            proposals.append({'path':f['path'],'line':f['line'],
                              'reference':value,'target_id':new,'kind':label,
                              'automatically_applied':False,
                              'requires_scope_and_semantic_review':True})
        review.append(item)
    return {'schema':1,'old_repository_id':old_id,'new_repository_id':new_id,
            'pull_requests':prs,'issues':issues,'workflow_ids':wf,
            'release_asset_ids':release_asset_ids or {},'ruleset_ids':ruleset_ids or {},
            'historical_runs_recreated':False,'run_ids':runids,
            'review_required':review,'reviewed_rewrite_candidates':proposals,
            'not_scanned':dependencies['scan_gaps'],
            'limitations':'No edits to live files, external CI, OIDC cloud trust, or historical GitHub run IDs.'}


def status_counts(dependencies):
    out={}
    for f in dependencies['findings']:out[f['status']]=out.get(f['status'],0)+1
    return out


def assess_named_repo_refs(dependencies, original_repo, saved_refs, upstream_refs=None):
    """Prove named same-repository uses resolve to the intended original object.

    Fork upstream collisions preserve original commits under archive refs, but the
    *original name* is replaced by upstream's commit. A name alone isn't proof.
    """
    upstream_refs=upstream_refs or {}
    results=[]
    for finding in dependencies.get('findings',[]):
        if finding.get('kind')!='repository-action-ref':continue
        match=REPO_USE.search(finding.get('value',''))
        if not match or match['repo'].lower()!=original_repo.lower():continue
        name=match['ref']
        if SHA.fullmatch(name):continue
        if name.startswith('refs/heads/') or name.startswith('refs/tags/'):
            candidates=[name]
        elif name.startswith('refs/'):
            candidates=[]  # pull/notes/other refs are not automatically recreated
        else:
            candidates=['refs/heads/'+name,'refs/tags/'+name]
        found=[ref for ref in candidates if ref in saved_refs]
        if not found:
            status='unavailable-named-ref'
        elif len(found)>1:
            status='ambiguous-branch-or-tag'
        elif found[0] in upstream_refs and upstream_refs[found[0]]!=saved_refs[found[0]]:
            status='fork-name-conflict'
        else:
            status='verified-original-named-ref'
        results.append({'path':finding['path'],'line':finding['line'],
                        'reference':finding['value'],'source_ref':name,
                        'original_ref':found[0] if len(found)==1 else None,
                        'original_sha':saved_refs.get(found[0]) if len(found)==1 else None,
                        'replacement_name_status':status})
    return results
