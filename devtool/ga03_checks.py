"""GA-03 independent source-bound GitHub App Checks, never a CI authority.

Only the existing native session, immutable source, native validation history,
Actions attempt verifier and federation gates supply evidence. The publisher owns
no Actions verdicts and cannot modify branch protection, PRs, refs or workflows.
A write is preceded by a durable intent. Unknown outcomes are reconciled by GET,
never replayed. Credentials and provider prose never enter journals or output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from . import delivery_session as ds
from .delivery_github import _ci_candidate, _remote_head
from .delivery_provider import classify_provider_error
from .github_app import AppInstallationProvider, _NoRedirect, _repository
from .ga01_scoped_auth import issue_for_operation
from .github_relationships import load_registry
from .ga02_live_registry import qualify_operation, _role_env
from .github_actions import GitHubActionsClient

NAMES = {'source': 'Devtool / Source Validation',
         'integration': 'Devtool / Integration Qualification',
         'federation': 'Devtool / Federation Compatibility'}
_MAX_ATTEMPTS = 16
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')


class CheckRefused(ds.DeliverySessionError):
    pass


def _require(condition, reason):
    if not condition:
        raise CheckRefused('GA03_' + reason)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def _positive(value):
    return type(value) is int and value > 0


def _source_guard(record):
    root = Path(record['repo_root'])
    now = ds._identity(root, head=record['head'], base=record['base'])
    _require(now['head_sha'] == record['head_sha'] and now['git_dir'] == record['git_dir']
             and now['repository'] == record['repository'] and not ds._git(root, 'status', '--porcelain'),
             'SOURCE_DRIFT_OR_DIRTY')
    _require(record.get('head_repository',record['repository']).casefold() == record['repository'].casefold(),
             'HEAD_REPOSITORY_MISMATCH')
    _require(record['head'] != record['base'], 'BASE_BRANCH_FORBIDDEN')
    return root


def _native_source(record):
    from .artifacts import load_history
    root = _source_guard(record)
    native = record.get('native_apply') or {}
    _require(native.get('state') == 'COMMITTED' and native.get('commit') == record['head_sha']
             and native.get('result_commit') == record['head_sha']
             and native.get('result_tree') == ds._git(root, 'rev-parse', 'HEAD^{tree}')
             and native.get('validation_status') in ('ok','passed','success','succeeded'), 'NATIVE_SOURCE_UNQUALIFIED')
    matches = [h for h in load_history(root) if h.get('history_id') == native.get('history_id')]
    _require(len(matches) == 1, 'NATIVE_HISTORY_UNAVAILABLE')
    h = matches[0]
    manifest = h.get('manifest') or {}
    _require(h.get('commit_hash') == record['head_sha'] and h.get('sha256') == native.get('artifact_sha256')
             and h.get('operation_id') == native.get('operation_id')
             and h.get('status') == 'ok' and (h.get('transaction') or {}).get('id') == native.get('transaction_id')
             and (h.get('transaction') or {}).get('status') == 'completed'
             and manifest.get('schema_version') == 3 and (manifest.get('validation') or {}).get('required') is True,
             'NATIVE_HISTORY_MISMATCH')
    steps = (h.get('validation_plan') or {}).get('validation_step_outcomes')
    if native.get('validation_evidence_source')=='original-static-child-outcomes':
        steps=ds._legacy_static_validation_outcomes(h,manifest)
    if not steps:
        steps = ds._legacy_required_artifact_test_outcomes(h, manifest)
    _require(isinstance(steps,list) and 1 <= len(steps) <= 64 and all(isinstance(x,dict)
             and x.get('outcome') == 'passed' and x.get('skipped') is False
             and type(x.get('returncode')) is int and x['returncode'] == 0 for x in steps),
             'NATIVE_VALIDATION_EVIDENCE_UNAVAILABLE')
    expected = [{'name':str(x.get('name') or ''), 'outcome':'passed','returncode':0,'skipped':False} for x in steps]
    _require(expected == native.get('validation_steps'), 'NATIVE_VALIDATION_EVIDENCE_MISMATCH')
    # No path or command/log text is published.
    return {'verdict':'success','scope':'native-committed-required-validation',
            'tree':native['result_tree'],'artifact_sha256':native['artifact_sha256'],
            'history_id':native['history_id'],'step_count':len(steps),'steps_sha256':_hash(expected)}


def _integration(record, client):
    from .delivery_qualification import _live_pr, _required_ci
    from .delivery_ci_policy import load_policy, load_upstream_policy, assert_observation
    item = record
    if not item.get('pr') or not item.get('ci'):
        return {'verdict':'neutral','scope':'independent-actions','state':'NOT_OBSERVED'}
    pr, merge = _live_pr(client, item)
    proof = _required_ci(client, item, pr, merge)
    if proof.get('passed') is True:
        final_pr, final_merge = _live_pr(client,item)
        _require(final_merge==merge and final_pr.get('number')==pr.get('number') and final_pr.get('head_sha')==pr.get('head_sha'),'PR_IDENTITY_DRIFT')
        attempts = [{k:x[k] for k in ('run_id','attempt','workflow_id','source_sha','job_count')}
                    for x in proof['exact_attempts']]
        _require(bool(attempts) and len(attempts) <= 64, 'ACTIONS_ATTEMPTS_MISSING')
        return {'verdict':'success','scope':'independent-required-actions-attempts',
                'policy_sha256':proof['policy_sha256'], 'pr_number':pr['number'],
                'merge_sha':merge, 'attempts':attempts}
    ci = item['ci']
    # Re-observe actual failed run and failed job on the exact current attempt.
    # Never map an outage, missing run, or stale receipt to a claimed failure.
    if ci.get('state') == 'CHECKS_FAILED' and ci.get('source_sha') == item['head_sha']:
        base = item.get('base_repository') or item['repository']
        if base.casefold() == item['repository'].casefold():
            policy = load_policy(Path(item['repo_root']),expected_repository=base,expected_base=item['base'])
        else:
            policy, _ = load_upstream_policy(client, expected_repository=base,
                             expected_base=item['base'],expected_base_sha=item.get('base_sha'))
        assert_observation(ci, policy)
        for row in ci.get('runs', []):
            if policy.workflow_ids.get(row.get('name')) != row.get('workflow_id') or not _positive(row.get('id')):
                continue
            run = _ci_candidate(client.get_run(base, row['id']), {**item,'repository':base}, pr, merge)
            if not run or any(run.get(k) != row.get(k) for k in ('id','attempt','name','workflow_id','head_sha')):
                continue
            if run['status'] != 'completed' or run['conclusion'] not in ('failure','cancelled','timed_out','action_required'):
                continue
            jobs = client.run_attempt_jobs(base, run['id'], run['attempt'])
            if not isinstance(jobs,list) or not 1 <= len(jobs) < 200:
                continue
            failing = [j['id'] for j in jobs if isinstance(j,dict) and _positive(j.get('id'))
                       and j.get('run_id',run['id']) == run['id'] and j.get('run_attempt',run['attempt']) == run['attempt']
                       and j.get('status') == 'completed' and j.get('conclusion') in ('failure','cancelled','timed_out','action_required')]
            if failing:
                final = _ci_candidate(client.get_run(base,run['id']), {**item,'repository':base}, pr, merge)
                _require(final == run and _live_pr(client,item) == (pr,merge), 'ACTIONS_ATTEMPT_DRIFT')
                return {'verdict':'failure','scope':'independent-required-actions-attempts',
                        'policy_sha256':policy.sha256,'pr_number':pr['number'],'merge_sha':merge,
                        'attempts':[{'run_id':run['id'],'attempt':run['attempt'],'workflow_id':run['workflow_id'],
                                     'source_sha':run['head_sha'],'failed_job_ids':sorted(failing)}]}
    return {'verdict':'neutral','scope':'independent-actions','state':'NOT_QUALIFIED'}


def _federation(record, registry):
    from .config import load_project_config
    from .federation_gate2 import build_federation_gate2_report
    from .github_installations import observe_installed, observe_public_upstream
    from .github_relationships import assert_observation, assert_relation
    root = _source_guard(record)
    config_path = root / '.devtool.toml'
    if not config_path.is_file():
        return {'verdict':'neutral','scope':'federation-gate2','state':'NOT_CONFIGURED'}
    _require(not config_path.is_symlink() and ds._git(root,'cat-file','-t','HEAD:.devtool.toml',optional=True)=='blob',
             'FEDERATION_CONFIG_NOT_COMMITTED')
    config = load_project_config(str(root),str(config_path),allow_parent_config=False)
    if not config.federation.repositories or not (config.federation.workflows or any(g.executable for g in config.federation.groups.values())):
        return {'verdict':'neutral','scope':'federation-gate2','state':'NOT_CONFIGURED'}
    own = registry.node(record['repository'])
    owner = observe_installed(own.repository,role=own.role,environ=_role_env(own.role,None))
    assert_observation(registry,owner)
    def dependencies():
        pins=[]
        for name, dependency in sorted(config.federation.repositories.items()):
            repo=Path(dependency.root).resolve()
            _require(dependency.available and not ds._git(repo,'status','--porcelain'), 'FEDERATION_DEPENDENCY_UNAVAILABLE_OR_DIRTY')
            # Native Git identity must match an explicitly enrolled dependency.
            local=ds._identity(repo,head=None,base='main')
            node=registry.node(local['repository'])
            obs=(observe_public_upstream(node.repository) if node.role=='public' else
                 observe_installed(node.repository,role=node.role,environ=_role_env(node.role,None)))
            assert_observation(registry,obs)
            edges=[e for e in registry.edges if e.source.casefold()==own.repository.casefold()
                   and e.target.casefold()==node.repository.casefold() and e.kind in ('submodule','package_dependency')]
            _require(len(edges)==1,'FEDERATION_DEPENDENCY_RELATION_UNVERIFIED')
            assert_relation(registry,edges[0].kind,owner,obs)
            # A locally asserted commit alone is not a source-bound dependency.
            # Public GET or a separately scoped installation must see exact SHA.
            if node.role=='public':
                depclient=GitHubActionsClient()
            else:
                dep=issue_for_operation(node.repository,'federation.source.fetch',expected_repository_id=node.repository_id,
                          provider=AppInstallationProvider.from_environment(_role_env(node.role,None),role=node.role))
                _require(dep.app_id==obs.app_id and dep.installation_id==node.installation_id,'FEDERATION_SCOPE_DRIFT')
                depclient=GitHubActionsClient(dep.token)
            _require(depclient.repository(node.repository).get('id')==node.repository_id and
                     _remote_head(depclient,node.repository,local['head'])==local['head_sha'],'FEDERATION_DEPENDENCY_SOURCE_DRIFT')
            pins.append({'repository_id':node.repository_id,'sha':local['head_sha'],'name_sha256':_hash(name)})
        return pins
    before=dependencies()
    _require(1<=len(before)<=32,'FEDERATION_DEPENDENCY_LIMIT')
    report=build_federation_gate2_report(config)
    _require(dependencies()==before and _source_guard(record)==root,'FEDERATION_SOURCE_DRIFT')
    # Gate 2 only qualifies the configured planning/compatibility contract, not
    # remote consumer CI, release, or future GA-06/M9 delivery.
    return {'verdict':'success' if report.ok else 'failure','scope':'federation-gate2-planning-compatibility',
            'dependencies':before,'checks':len(report.checks),
            'errors':sum(c.status=='error' for c in report.checks),
            'report_sha256':_hash([(c.section,c.name,c.status) for c in report.checks])}


class ChecksTransport:
    """Fixed endpoint, narrowly scoped native provider. No generic write API."""
    def __init__(self, access, *, opener=None):
        self.access=access
        self.slug=_repository(access.repository)
        self.opener=opener or build_opener(_NoRedirect())
        self.client=GitHubActionsClient(access.token,opener=self.opener)

    def _call(self, method, suffix, payload=None):
        _require(self.access.expires_at > int(time.time())+15,'TOKEN_EXPIRES_TOO_SOON')
        _require(method in ('GET','POST','PATCH') and ((method=='GET') == (payload is None)), 'HTTP_METHOD_REFUSED')
        if method != 'GET':
            _require(self.access.capability=='checks_write','WRITE_SCOPE_REQUIRED')
        pattern={'GET':r'(?:commits/[a-f0-9]{40}/check-runs\?filter=all&per_page=100&page=[1-6]|check-runs/[1-9][0-9]*(?:/annotations\?per_page=100&page=1)?)',
                 'POST':r'check-runs','PATCH':r'check-runs/[1-9][0-9]*'}[method]
        _require(re.fullmatch(pattern,suffix),'HTTP_PATH_REFUSED')
        data=None if payload is None else json.dumps(payload,ensure_ascii=True,separators=(',',':')).encode()
        _require(data is None or len(data)<=32768,'HTTP_PAYLOAD_TOO_LARGE')
        req=Request(f'https://api.github.com/repos/{self.slug}/{suffix}',data=data,method=method,
                    headers={'Authorization':'Bearer '+self.access.token,'Accept':'application/vnd.github+json',
                             'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json','User-Agent':'devtool-ga03/1'})
        try:
            with self.opener.open(req,timeout=20) as response:
                _require(int(response.status)==(201 if method=='POST' else 200),'HTTP_STATUS_UNEXPECTED')
                raw=response.read(2*1024*1024+1)
            _require(len(raw)<=2*1024*1024,'HTTP_RESPONSE_OVERSIZE')
            return json.loads(raw.decode())
        except (HTTPError,URLError,TimeoutError,OSError) as exc:
            raise CheckRefused('GA03_HTTP_'+method+'_'+classify_provider_error(exc).upper()) from None
        except (ValueError,UnicodeError):
            raise CheckRefused('GA03_HTTP_RESPONSE_INVALID') from None

    def runs(self, sha):
        _require(_SHA.fullmatch(sha),'SHA_INVALID')
        result=[]; total=None
        for page in range(1,7):
            data=self._call('GET',f'commits/{sha}/check-runs?filter=all&per_page=100&page={page}')
            _require(isinstance(data,dict) and type(data.get('total_count')) is int and 0<=data['total_count']<=500
                     and isinstance(data.get('check_runs'),list) and len(data['check_runs'])<=100,'CHECK_LIST_INCOMPLETE')
            if total is None: total=data['total_count']
            _require(total==data['total_count'],'CHECK_LIST_DRIFT')
            result.extend(data['check_runs'])
            _require(all(isinstance(x,dict) and _positive(x.get('id')) for x in result)
                     and len({x['id'] for x in result})==len(result),'CHECK_LIST_DUPLICATE')
            if len(result)==total: return result
            _require(len(result)<total and len(data['check_runs'])==100,'CHECK_LIST_INCOMPLETE')
        raise CheckRefused('GA03_CHECK_LIST_UNBOUNDED')

    def get(self, identity):
        _require(_positive(identity),'CHECK_ID_INVALID')
        return self._call('GET',f'check-runs/{identity}')

    def annotations(self, identity):
        _require(_positive(identity),'CHECK_ID_INVALID')
        return self._call('GET',f'check-runs/{identity}/annotations?per_page=100&page=1')

    def create(self,payload):
        self._call('POST','check-runs',payload)

    def finish(self,identity,payload):
        _require(_positive(identity),'CHECK_ID_INVALID')
        self._call('PATCH',f'check-runs/{identity}',payload)


def _scope(registry, record, *, write):
    operation='delivery.checks.publish' if write else 'delivery.checks.read'
    slug=record['repository']; node=registry.node(slug)
    proof=qualify_operation(registry,slug,operation,source_sha=record['head_sha'])
    _require(node.role in ('base','head') and proof['repository_id']==node.repository_id,'POLICY_SCOPE_UNAVAILABLE')
    access=issue_for_operation(slug,operation,expected_repository_id=node.repository_id,
        provider=AppInstallationProvider.from_environment(_role_env(node.role,None),role=node.role))
    _require(access.app_id==proof['app_id'] and access.installation_id==proof['installation_id'],'APP_IDENTITY_DRIFT')
    return ChecksTransport(access)


def _attest(transport, record, registry):
    _source_guard(record)
    _require(transport.client.repository(record['repository']).get('id')==registry.node(record['repository']).repository_id,
             'REMOTE_REPOSITORY_ID_DRIFT')
    _require(_remote_head(transport.client,record['repository'],record['head'])==record['head_sha'],'REMOTE_HEAD_DRIFT')


def _evidence(record, registry, kind, transport):
    native=_native_source(record)
    if kind=='source': return native
    if kind=='federation': return _federation(record,registry)
    base=record.get('base_repository') or record['repository']
    node=registry.node(base)
    _require(node.role in ('base','head'),'ACTIONS_READ_SCOPE_UNAVAILABLE')
    # Independent read-only PR/Actions/Checks evidence token. The Check writer
    # itself has neither Actions nor pull-request permission.
    p=qualify_operation(registry,base,'delivery.checks.evidence',source_sha=record['head_sha'])
    access=issue_for_operation(base,'delivery.checks.evidence',expected_repository_id=node.repository_id,
                provider=AppInstallationProvider.from_environment(_role_env(node.role,None),role=node.role))
    _require(access.app_id==p['app_id'] and access.installation_id==p['installation_id'],'ACTIONS_SCOPE_DRIFT')
    client=GitHubActionsClient(access.token)
    return _integration(record,client)


def _annotation(record, kind, evidence):
    if evidence['verdict']!='failure' or kind not in ('integration','federation'): return []
    path='config/delivery-required-ci.json' if kind=='integration' else '.devtool.toml'
    root=Path(record['repo_root'])
    if ds._git(root,'cat-file','-t','HEAD:'+path,optional=True)!='blob' or not ds._git(root,'show','HEAD:'+path,optional=True): return []
    return [{'path':path,'start_line':1,'end_line':1,'annotation_level':'notice',
             'title':'Qualification evidence','message':'Independent qualification did not pass. This identifies the committed policy or configuration, not a failing source line.'}]


def _payload(record, entry, *, complete):
    output={'title':NAMES[entry['kind']], 'summary':('Independent evidence: '+entry['evidence']['scope']+
              '. Evidence SHA-256: '+entry['evidence_sha256']+'. Session: '+record['id']+
              '. Attempt: '+str(entry['attempt'])+'. This check is not GitHub Actions or release authority.')}
    data={'name':NAMES[entry['kind']], 'external_id':entry['external_id'],
          'details_url':f"https://github.com/{record['repository']}/commit/{record['head_sha']}",
          'status':'completed' if complete else 'in_progress','output':output}
    if complete:
        data['conclusion']=entry['evidence']['verdict']
        if entry['annotations']: output['annotations']=entry['annotations']
    else:
        data['head_sha']=record['head_sha']
    return data


def _match(transport, record, entry, rows):
    matches=[r for r in rows if r.get('external_id')==entry['external_id']]
    _require(len(matches)<=1,'DUPLICATE_CHECK')
    if not matches: return None
    actual=transport.get(matches[0]['id'])
    _require(isinstance(actual,dict) and actual.get('id')==matches[0]['id'] and actual.get('head_sha')==record['head_sha']
             and actual.get('external_id')==entry['external_id'] and actual.get('name')==NAMES[entry['kind']]
             and (actual.get('app') or {}).get('id')==entry['app_id'], 'REMOTE_CHECK_IDENTITY_MISMATCH')
    expected=_payload(record,entry,complete=actual.get('status')=='completed')
    _require(actual.get('status') in ('in_progress','completed') and actual.get('details_url')==expected['details_url']
             and all((actual.get('output') or {}).get(k)==expected['output'][k] for k in ('title','summary')),
             'REMOTE_CHECK_PAYLOAD_MISMATCH')
    complete=actual['status']=='completed'
    _require(actual.get('conclusion')==(entry['evidence']['verdict'] if complete else None),'REMOTE_CHECK_CONCLUSION_MISMATCH')
    annotations=entry['annotations'] if complete else []
    _require((actual.get('output') or {}).get('annotations_count')==len(annotations),'REMOTE_ANNOTATION_COUNT_MISMATCH')
    if annotations:
        observed=transport.annotations(actual['id'])
        _require(isinstance(observed,list) and len(observed)==len(annotations) and
                 [{k:a.get(k) for k in b} for a,b in zip(observed,annotations)]==annotations,'REMOTE_ANNOTATION_MISMATCH')
    return actual


def publish(session_id, *, kind, attempt, policy, policy_sha256, yes=False, state_root=None):
    """Preview/reconcile or publish one exact native evidence class.

    One lock serializes local writes for the whole delivery session, including
    API I/O. Each independently observed phase is persisted before proceeding.
    An unresolved mutation allows only read-only reconciliation, even with yes.
    """
    _require(kind in NAMES and type(attempt) is int and 1<=attempt<=_MAX_ATTEMPTS,'ATTEMPT_OR_KIND_INVALID')
    folder=ds._state_root(state_root,identity=session_id)
    with ds._locked(folder):
        path=ds._path(folder,session_id); record=ds._read(path)
        registry=load_registry(policy,expected_sha256=policy_sha256,expected_source_sha=record['head_sha'])
        _require(ds._intents_terminal_and_valid(record['intents']),'OTHER_MUTATION_UNRESOLVED')
        _source_guard(record)
        transport=_scope(registry,record,write=False)
        _attest(transport,record,registry)
        node=registry.node(record['repository'])
        identity={'repository_id':node.repository_id,'head_sha':record['head_sha'],'kind':kind,'session_id':session_id,'attempt':attempt}
        external='ga03:'+_hash(identity)
        entries=record.setdefault('check_publications',[])
        _require(isinstance(entries,list) and len(entries)<=len(NAMES)*_MAX_ATTEMPTS,'JOURNAL_INVALID')
        own=[e for e in entries if e.get('kind')==kind]
        highest=max((e['attempt'] for e in own),default=0)
        _require(attempt>=highest and attempt<=highest+1,'STALE_OR_NONCONTIGUOUS_ATTEMPT')
        matching=[e for e in own if e.get('attempt')==attempt]
        _require(len(matching)<=1,'JOURNAL_DUPLICATE')
        rows=transport.runs(record['head_sha'])
        known={e['external_id'] for e in own}
        _require(not any(r.get('name')==NAMES[kind] and (r.get('app') or {}).get('id')==transport.access.app_id
                         and r.get('external_id') not in known for r in rows),'UNREGISTERED_APP_CHECK')
        def save():
            record['updated_at']=ds._utc(); ds._write(path,record)
        if matching:
            entry=matching[0]
            _require(entry['external_id']==external and entry['repository_id']==node.repository_id and entry['policy_sha256']==registry.sha256
                     and entry['app_id']==transport.access.app_id and entry['installation_id']==transport.access.installation_id
                     and entry['evidence_sha256']==_hash(entry['evidence']),'JOURNAL_IDENTITY_DRIFT')
            actual=_match(transport,record,entry,rows)
            reconciled=False
            if actual:
                _require(entry.get('check_id') in (None,actual['id']),'CHECK_ID_DRIFT')
                entry['check_id']=actual['id']
                if entry['phase']=='create_intent' and actual['status']=='in_progress':
                    entry['phase']='created'; save(); reconciled=True
                elif entry['phase']=='complete_intent' and actual['status']=='completed':
                    entry['phase']='confirmed'; save(); reconciled=True
                elif entry['phase']=='confirmed':
                    _require(actual['status']=='completed','CONFIRMED_CHECK_DRIFT')
                elif entry['phase']=='created':
                    _require(actual['status']=='in_progress','UNJOURNALED_COMPLETION')
            if entry['phase']=='confirmed':
                _require(actual is not None and actual['status']=='completed','CONFIRMED_CHECK_MISSING')
                # Old green checks cannot be presented as current evidence.
                _require(_hash(_evidence(record,registry,kind,transport))==entry['evidence_sha256'],'STALE_EVIDENCE')
                return _receipt(record,entry,False)
            _require(entry['phase']=='created' and actual is not None and actual['status']=='in_progress','UNCERTAIN_WRITE_RECONCILE_ONLY')
            if reconciled or not yes: return _receipt(record,entry,False)
            _require(_hash(_evidence(record,registry,kind,transport))==entry['evidence_sha256'],'STALE_EVIDENCE')
        else:
            _require(not any(e.get('phase')!='confirmed' for e in entries),'OTHER_CHECK_UNRESOLVED')
            evidence=_evidence(record,registry,kind,transport)
            if own:
                _require(_hash(evidence)!=own[-1]['evidence_sha256'],'DUPLICATE_EVIDENCE_ATTEMPT')
            entry={'kind':kind,'attempt':attempt,'external_id':external,'repository_id':node.repository_id,
                   'app_id':transport.access.app_id,'installation_id':transport.access.installation_id,
                   'policy_sha256':registry.sha256,'evidence':evidence,'evidence_sha256':_hash(evidence),
                   'annotations':_annotation(record,kind,evidence),'phase':'preview'}
            if not yes: return _receipt(record,entry,False)
        # Re-issue narrowly scoped write token only after read-only evidence.
        _require(load_registry(policy,expected_sha256=policy_sha256,expected_source_sha=record['head_sha']).sha256==registry.sha256,'POLICY_DRIFT')
        write=_scope(registry,record,write=True)
        _require(write.access.app_id==entry['app_id'] and write.access.installation_id==entry['installation_id'],'WRITE_SCOPE_DRIFT')
        _attest(write,record,registry)
        current_rows=write.runs(record['head_sha'])
        current=_match(write,record,entry,current_rows)
        if entry['phase']=='preview':
            _require(current is None and not any(r.get('name')==NAMES[kind] and (r.get('app') or {}).get('id')==entry['app_id']
                    and r.get('external_id') not in known for r in current_rows),'DUPLICATE_OR_COMPETING_CHECK')
            entry['phase']='create_intent'; entries.append(entry); save()
            write.create(_payload(record,entry,complete=False))
            # Separate read and no blind retry on absent result.
            actual=_match(transport,record,entry,transport.runs(record['head_sha']))
            _require(actual is not None and actual['status']=='in_progress','CREATE_OUTCOME_UNCERTAIN')
            entry['check_id']=actual['id']; entry['phase']='created'; save()
        else:
            _require(current is not None and current['id']==entry['check_id'] and current['status']=='in_progress','CHECK_BEFORE_UPDATE_DRIFT')
        _attest(transport,record,registry)
        _require(_hash(_evidence(record,registry,kind,transport))==entry['evidence_sha256'],'STALE_EVIDENCE_BEFORE_COMPLETION')
        entry['phase']='complete_intent'; save()
        write.finish(entry['check_id'],_payload(record,entry,complete=True))
        actual=_match(transport,record,entry,transport.runs(record['head_sha']))
        _require(actual is not None and actual['id']==entry['check_id'] and actual['status']=='completed','COMPLETE_OUTCOME_UNCERTAIN')
        _attest(transport,record,registry)
        entry['phase']='confirmed'; save()
        _require(_hash(_evidence(record,registry,kind,transport))==entry['evidence_sha256'],'STALE_EVIDENCE_AFTER_COMPLETION')
        return _receipt(record,entry,True)


def _receipt(record,entry,mutated):
    return {'schema':'devtool.ga03.check-receipt.v1','repository':record['repository'],
            'head_sha':record['head_sha'],'session_id':record['id'],'repository_id':entry['repository_id'],
            'kind':entry['kind'],'attempt':entry['attempt'],'external_id':entry['external_id'],
            'check_id':entry.get('check_id'),'phase':entry['phase'],'evidence_sha256':entry['evidence_sha256'],
            'verdict':entry['evidence']['verdict'],'evidence_scope':entry['evidence']['scope'],
            'annotation_count':len(entry['annotations']),'remote_mutation':mutated,
            'independent_actions_authority_unchanged':True,'release_qualified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-id',required=True); parser.add_argument('--kind',choices=tuple(NAMES),required=True)
    parser.add_argument('--attempt',type=int,default=1); parser.add_argument('--policy',required=True)
    parser.add_argument('--policy-sha256',required=True); parser.add_argument('--yes',action='store_true')
    args=parser.parse_args(argv)
    try:
        result=publish(args.session_id,kind=args.kind,attempt=args.attempt,policy=args.policy,
                       policy_sha256=args.policy_sha256,yes=args.yes)
    except CheckRefused as exc:
        print(str(exc),file=sys.stderr); return 2
    except (Exception,SystemExit):
        print('GA03_PREFLIGHT_OR_RECONCILIATION_REFUSED',file=sys.stderr); return 2
    print(json.dumps(result,sort_keys=True)); return 0 if result['phase'] in ('confirmed','preview','created') else 3


if __name__=='__main__':
    raise SystemExit(main())
