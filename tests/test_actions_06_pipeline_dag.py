"""ACTIONS-06 frozen source, delegated workflows, journal and remote truth."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from devtool.actions_pipeline import (_manifest, pipeline_plan, pipeline_run,
                                      pipeline_status, pipeline_resume, _journal_path)
from devtool.github_actions import GitHubActionsError
from devtool.cli import build_parser

SHA = 'a' * 40
SLUG = 'Owner/repo'


def init_repo(tmp_path):
    tmp_path.mkdir(parents=True,exist_ok=True)
    subprocess.run(['git','init','-q',str(tmp_path)], check=True)
    for x in (['config','user.name','Tester'],['config','user.email','test@example.invalid']):
        subprocess.run(['git','-C',str(tmp_path),*x], check=True)
    return tmp_path


def commit(repo):
    subprocess.run(['git','-C',str(repo),'add','-A'], check=True)
    subprocess.run(['git','-C',str(repo),'commit','-qm','test'], check=True)
    return subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()


def graph(with_archive=False):
    nodes = [
        {'id':'source','kind':'local-git'},
        {'id':'build','kind':'github-dispatch','needs':['source'],
         'workflow':'devtool-android-build.yml','ref':'main',
         'inputs':{'module':'app','variant':'debug','format':'apk','project_dir':'android/devtool-live-hud'}},
        {'id':'observe','kind':'github-result','from':'build','needs':['build']},
    ]
    if with_archive:
        nodes.append({'id':'archive','kind':'github-archive','from':'build',
                      'artifact_name':'devtool-android-debug-apk',
                      'needs':['build','observe']})
    return {'schema':1,'name':'live-hud','nodes':nodes}


def make(repo, body=None):
    path=repo/'pipeline.json'
    path.write_text(json.dumps(body if body is not None else graph()))
    return commit(repo)


def remote_preflight(client, slug, workflow, ref, inputs, *, execute, expected_source_sha=None):
    if execute:
        assert expected_source_sha == client.head
    assert slug == SLUG
    assert workflow == 'devtool-android-build.yml'
    return {'pre_dispatch_sha':client.head,'workflow_id':99,
            'workflow_path':'.github/workflows/devtool-android-build.yml',
            'state':'accepted-correlated' if execute else 'validated-plan',
            'request_id':'e'*32 if execute else None,'run_id':777 if execute else None}


class API:
    def __init__(self, head):self.head=head;self.status='queued';self.conclusion=None;self.calls=0
    def get_run(self, slug, identity):
        self.calls+=1
        assert identity==777
        return {'id':777,'repository':{'full_name':SLUG},'status':self.status,
                'conclusion':self.conclusion,'head_sha':self.head,'run_attempt':1,
                'event':'workflow_dispatch','workflow_id':99}


def test_parser_and_baseline_surface():
    parser=build_parser()
    for name in ('plan','run','status','resume'):
        tail=['--file','pipeline.json'] if name in ('plan','run') else ['--pipeline-id','e'*32]
        result=parser.parse_args(['actions','pipeline',name]+tail)
        assert (result.actions_command,result.pipeline_command)==('pipeline',name)
    from devtool.github_actions import actions_baseline
    assert 'pipeline resume' in actions_baseline()['operations']


def test_plan_validates_remote_without_dispatch_and_freezes_commit(tmp_path, monkeypatch):
    repo=init_repo(tmp_path);head=make(repo);api=API(head)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',remote_preflight)
    result=pipeline_plan(api,SLUG,repo,'pipeline.json')
    assert result['validated'] and not result['dispatched']
    assert result['source_sha']==head and len(result['graph'])==3
    api.head='b'*40
    with pytest.raises(GitHubActionsError,match='differ'):
        pipeline_plan(api,SLUG,repo,'pipeline.json')


def test_durable_dispatch_wait_and_resume_success(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');repo.mkdir(exist_ok=True) if not repo.exists() else None
    head=make(repo);api=API(head)
    calls=[]
    def dispatch(*args,**kwargs):
        calls.append(kwargs['execute']);return remote_preflight(*args,**kwargs)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch)
    root=tmp_path/'state'
    out=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    assert out['state']=='incomplete'
    assert [x['state'] for x in out['nodes']]==['success','success','waiting']
    assert calls==[False,True]
    assert (root/(out['pipeline_id']+'.json')).is_file()
    assert pipeline_status(out['pipeline_id'],state_root=root)['nodes'][2]['state']=='waiting'
    api.status='completed';api.conclusion='success'
    finished=pipeline_resume(api,SLUG,repo,out['pipeline_id'],state_root=root)
    assert finished['completed_successfully'] and not finished['qualified_release']
    assert calls==[False,True]  # never dispatch twice


def test_remote_failure_is_not_success_and_not_auto_retried(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');head=make(repo);api=API(head)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',remote_preflight)
    root=tmp_path/'state'
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    api.status='completed';api.conclusion='failure'
    failed=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=root)
    assert failed['state']=='blocked' and failed['nodes'][-1]['state']=='failure'
    assert not pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=root)['completed_successfully']


def test_mismatched_remote_sha_event_or_workflow_blocks(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');head=make(repo);api=API(head)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',remote_preflight)
    root=tmp_path/'state'
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    api.head='b'*40
    mismatch=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=root)
    assert mismatch['state']=='blocked' and 'mismatch' in mismatch['nodes'][-1]['detail']


def test_uncertain_dispatch_is_not_replayed(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');head=make(repo);api=API(head)
    calls=[]
    def dispatch(*args,**kwargs):
        calls.append(kwargs['execute'])
        if kwargs['execute']:raise GitHubActionsError('Dispatch outcome uncertain')
        return remote_preflight(*args,**kwargs)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',dispatch)
    root=tmp_path/'state'
    result=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    assert result['nodes'][1]['state']=='uncertain'
    pipeline_resume(api,SLUG,repo,result['pipeline_id'],state_root=root)
    assert calls==[False,True]


def test_stale_journal_or_manifest_refuses_resumption(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');head=make(repo);api=API(head)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',remote_preflight)
    root=tmp_path/'state'
    result=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    (repo/'pipeline.json').write_text('changed')
    with pytest.raises(GitHubActionsError):
        pipeline_resume(api,SLUG,repo,result['pipeline_id'],state_root=root)
    with pytest.raises(GitHubActionsError,match='binding'):
        pipeline_resume(api,'Other/repo',repo,result['pipeline_id'],state_root=root)


@pytest.mark.parametrize('bad', [
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'local-git','needs':['b']}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'local-git'},{'id':'a','kind':'local-git'}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'github-dispatch','ref':'main','workflow':'bad','inputs':{}}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'github-dispatch','ref':'main','workflow':'ci.yml','inputs':{'api_token':'x'}}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'local-invoke','action':'../rm'}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'github-result','from':'other','needs':[]}]},
    {'schema':1,'name':'live-hud','nodes':[{'id':'a','kind':'local-git','unknown':'x'}]},
])
def test_unsafe_pipeline_rejected(tmp_path, bad):
    repo=init_repo(tmp_path);make(repo,bad)
    with pytest.raises(GitHubActionsError):_manifest(repo,'pipeline.json')


def test_no_symlink_or_traversal_manifest(tmp_path):
    repo=init_repo(tmp_path/'repo');make(repo)
    with pytest.raises(GitHubActionsError):_manifest(repo,'../pipeline.json')
    (repo/'link.json').symlink_to(repo/'pipeline.json')
    with pytest.raises(GitHubActionsError):_manifest(repo,'link.json')


def test_verified_archive_registered_no_promotion(tmp_path, monkeypatch):
    repo=init_repo(tmp_path/'repo');head=make(repo,graph(True));api=API(head)
    monkeypatch.setattr('devtool.actions_pipeline.dispatch_workflow',remote_preflight)
    downloaded=[]
    monkeypatch.setattr('devtool.actions_pipeline.artifact_list',lambda *args:{'artifacts':[{'id':55,'name':'devtool-android-debug-apk'}]})
    monkeypatch.setattr('devtool.actions_pipeline.download_artifact',lambda *args:downloaded.append(1) or {'download_verified':True})
    def verify(*args):
        if not downloaded:raise GitHubActionsError('No registered download for this GitHub artifact')
        return {'archive_verified':True}
    monkeypatch.setattr('devtool.actions_pipeline.verify_download',verify)
    root=tmp_path/'state'
    first=pipeline_run(api,SLUG,repo,'pipeline.json',state_root=root)
    api.status='completed';api.conclusion='success'
    done=pipeline_resume(api,SLUG,repo,first['pipeline_id'],state_root=root)
    assert done['completed_successfully'] and done['nodes'][-1]['artifact_id']==55 and downloaded==[1]
    assert done['qualified_release'] is False


def test_tool_owned_run_catalog_is_not_mistaken_for_source_mutation(tmp_path):
    from devtool.actions_pipeline import _source
    repo=init_repo(tmp_path/'repo')
    sha=make(repo, {'schema':1,'name':'local','nodes':[{'id':'source','kind':'local-git'}]})
    catalog=repo/'.devtool/private/run-v3/catalog.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{}')
    artifacts=repo/'.devtool/artifacts/github-downloads/download.zip'
    artifacts.parent.mkdir(parents=True)
    artifacts.write_bytes(b'signed archive placeholder')
    assert _source(repo)==sha
    # An unexpected untracked user file still blocks dispatch.
    (repo/'uncommitted.txt').write_text('uncommitted')
    with pytest.raises(GitHubActionsError, match='clean'):_source(repo)


def test_native_workflow_delegation_bounded_and_not_shell(tmp_path, monkeypatch):
    from devtool.actions_pipeline import _local_invoke
    calls=[]
    def run(cmd, **kwargs):
        calls.append((cmd,kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('devtool.actions_pipeline.subprocess.run',run)
    _local_invoke(tmp_path,{'kind':'local-invoke','action':'codegen','target':'devtool'})
    assert calls[0][0][:3] == [__import__('sys').executable,'-m','devtool']
    assert calls[0][0][-4:]==['invoke','codegen','--target','devtool']
    assert calls[0][1]['timeout']==900 and calls[0][1]['cwd']==tmp_path
    assert calls[0][1]['stdout']==subprocess.DEVNULL
