"""RM16 batch behavioral gates: one producer, independent artifacts, no sign shortcuts."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import pytest

from devtool.github_actions import GitHubActionsError
from devtool.actions_android_release import release_batch_build, release_batch_import, release_batch_sign
from devtool.cli import build_parser


def fixtures(tmp_path):
    repository=tmp_path/'devtool';repository.mkdir()
    (repository/'config').mkdir()
    sample={
       'repository':'Mikeyphw/bkx','workflow':'bkx-unsigned.yml',
       'application_id':'com.example.bkx','target':'bkx',
       'project_dir':'.','module':'app','build_type':'release',
       'signing_identity_id':'bkx-life','signer_sha256':'a'*64,
    }
    entries=[]
    for suffix,variant,required,forbidden in [('embedded','embeddedRelease',['lib/arm64-v8a/rclone'],[]),('lite','liteRelease',[],['lib/arm64-v8a/rclone'])]:
        entries.append(dict(sample,id='bkx-'+suffix,artifact='bkx-'+suffix+'-unsigned',
              apk='bkx-'+suffix+'.apk',variant=variant,required_files=required,forbidden_files=forbidden))
    (repository/'config/android-consumers.json').write_text(json.dumps({'schema_version':1,'consumers':entries}))
    producer=tmp_path/'bkx';producer.mkdir()
    subprocess.run(['git','init','-q',str(producer)],check=True)
    subprocess.run(['git','-C',str(producer),'config','user.email','test@example.com'],check=True)
    subprocess.run(['git','-C',str(producer),'config','user.name','Test'],check=True)
    (producer/'README.md').write_text('source')
    subprocess.run(['git','-C',str(producer),'add','-A'],check=True)
    subprocess.run(['git','-C',str(producer),'commit','-qm','source'],check=True)
    sha=subprocess.check_output(['git','-C',str(producer),'rev-parse','HEAD'],text=True).strip()
    return repository,producer,sha

IDS=['bkx-embedded','bkx-lite']


def test_batch_one_dispatch_two_distinct_artifacts(tmp_path,monkeypatch):
    root,producer,sha=fixtures(tmp_path)
    calls=[]
    def dispatch(client,repo,local,**kw):
        calls.append(kw)
        return {'source_sha':sha,'dispatched':kw['execute'],
                'request_id':'request-1','run_id':51,'state':'accepted-correlated'}
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_dispatch',dispatch)
    result=release_batch_build(object(),'Mikeyphw/bkx',root,consumer_ids=IDS,ref='main',execute=True,source_root=producer)
    assert len(calls)==1 and calls[0]['consumer_id']==IDS[0]
    assert calls[0]['resume_if_existing'] is True and calls[0]['source_root']==producer
    assert result['dispatch_count']==1 and not result['qualified_release']
    assert [x['artifact'] for x in result['consumers']]==['bkx-embedded-unsigned','bkx-lite-unsigned']
    assert len({x['contract_sha256'] for x in result['consumers']})==2


def test_batch_rejects_mismatched_workflow_or_signer_before_dispatch(tmp_path,monkeypatch):
    root,_,_=fixtures(tmp_path)
    p=root/'config/android-consumers.json';info=json.loads(p.read_text())
    for key,value in [('workflow','another.yml'),('signer_sha256','b'*64),('artifact','bkx-embedded-unsigned'),('variant','embeddedRelease')]:
        patched=json.loads(json.dumps(info));patched['consumers'][1][key]=value
        p.write_text(json.dumps(patched))
        with pytest.raises(GitHubActionsError):
            release_batch_build(object(),'Mikeyphw/bkx',root,consumer_ids=IDS,ref='main')
        p.write_text(json.dumps(info))
    with pytest.raises(GitHubActionsError,match='duplicate'):
        release_batch_build(object(),'Mikeyphw/bkx',root,consumer_ids=[IDS[0],IDS[0]],ref='main')


def test_batch_import_preflights_every_artifact_before_materializing(tmp_path,monkeypatch):
    root,_,sha=fixtures(tmp_path)
    calls=[]
    def importing(client,repo,local,**kw):
        calls.append(kw)
        if kw['artifact_id']==9 and kw['materialize'] is False:
            raise GitHubActionsError('Wrong artifact for Lite')
        return {'consumer_id':kw['consumer_id'],'source_sha':sha,'run_id':kw['run_id'],
                'run_attempt':3, 'registry_id':'registry-'+kw['consumer_id']}
    monkeypatch.setattr('devtool.actions_unsigned.unsigned_import',importing)
    with pytest.raises(GitHubActionsError,match='Wrong artifact'):
        release_batch_import(object(),'Mikeyphw/bkx',root,consumer_ids=IDS,run_id=50,
                             artifact_ids={IDS[0]:8,IDS[1]:9},expected_sha=sha,materialize=True)
    assert len(calls)==2 and all(not x['materialize'] for x in calls)
    calls.clear()
    done=release_batch_import(object(),'Mikeyphw/bkx',root,consumer_ids=IDS,run_id=50,
        artifact_ids={IDS[0]:8,IDS[1]:10},expected_sha=sha,materialize=True)
    assert done['complete'] and len(done['imports'])==2
    assert [x['materialize'] for x in calls]==[False,False,True,True]
    assert not done['qualified_release']
    with pytest.raises(GitHubActionsError,match='distinct'):
        release_batch_import(object(),'Mikeyphw/bkx',root,consumer_ids=IDS,run_id=50,
            artifact_ids={IDS[0]:8,IDS[1]:8},expected_sha=sha,materialize=False)


def test_batch_sign_all_preflight_and_same_run(tmp_path,monkeypatch):
    root,_,sha=fixtures(tmp_path)
    calls=[];attempts={IDS[0]:1,IDS[1]:1}
    def imported(local,id):
        name=id.removeprefix('reg-')
        return Path('/candidate.apk'),{'consumer_id':name,'remote_repository':'Mikeyphw/bkx',
             'remote_workflow_path':'.github/workflows/bkx-unsigned.yml','remote_run_id':11,
             'remote_run_attempt':attempts[name],'remote_head_sha':sha}
    def signer(local,**kw):
        calls.append(kw)
        return {'signing':{'expected_signer_sha256':'a'*64},'qualified_release':False}
    monkeypatch.setattr('devtool.actions_unsigned._imported',imported)
    monkeypatch.setattr('devtool.actions_android_release.release_sign',signer)
    regs={id:'reg-'+id for id in IDS}
    plan=release_batch_sign(root,consumer_ids=IDS,registry_ids=regs,execute=False)
    assert not plan['complete'] and len(calls)==2 and not any(x['execute'] for x in calls)
    calls.clear()
    done=release_batch_sign(root,consumer_ids=IDS,registry_ids=regs,execute=True)
    assert done['complete'] and len(calls)==4 and [x['execute'] for x in calls]==[False,False,True,True]
    calls.clear();attempts[IDS[1]]=2
    with pytest.raises(GitHubActionsError,match='single identical'):
        release_batch_sign(root,consumer_ids=IDS,registry_ids=regs,execute=True)
    assert calls==[]


def test_parser_and_explicit_consent(tmp_path):
    p=build_parser()
    p.parse_args(['actions','android','release-batch-plan','--github-repo','Mikeyphw/bkx',
                  '--consumers',','.join(IDS),'--source-root',str(tmp_path)])
    a=p.parse_args(['actions','android','release-batch-import','--github-repo','Mikeyphw/bkx',
        '--consumers',','.join(IDS),'--artifact','bkx-embedded=1','--artifact','bkx-lite=2','--yes'])
    assert a.artifact==['bkx-embedded=1','bkx-lite=2'] and a.yes


def test_external_bkx_checkout_is_producer_not_devtool_root(tmp_path,monkeypatch):
    root,producer,sha=fixtures(tmp_path)
    from devtool.actions_unsigned import unsigned_dispatch
    calls=[]
    def fake_dispatch(client,repo,workflow,ref,inputs,**opts):
        calls.append(opts)
        return {'workflow_path':'.github/workflows/bkx-unsigned.yml',
                'pre_dispatch_sha':sha,'state':'validated-plan','dispatched':False}
    monkeypatch.setattr('devtool.actions_unsigned.dispatch_workflow',fake_dispatch)
    result=unsigned_dispatch(object(),'Mikeyphw/bkx',root,consumer_id=IDS[0],
                             source_root=producer,ref='main',resume_if_existing=True)
    assert result['source_sha']==sha
    assert calls[0]['expected_source_sha']==sha and calls[0]['resume_if_existing'] is True
    with pytest.raises(GitHubActionsError,match='clean committed'):
        (producer/'untracked').write_text('dirty')
        unsigned_dispatch(object(),'Mikeyphw/bkx',root,consumer_id=IDS[0],
                          source_root=producer,ref='main',resume_if_existing=True)


def test_batch_local_sign_never_needs_github_login(tmp_path,monkeypatch):
    from devtool.github_actions import actions_cli
    from types import SimpleNamespace
    observed=[]
    def signer(root,**kw):
        observed.append(kw)
        return {'phase':'ACTIONS-ANDROID-BATCH','complete':True,'qualified_release':False}
    monkeypatch.setattr('devtool.actions_android_release.release_batch_sign',signer)
    args=SimpleNamespace(actions_command='android',android_command='release-batch-sign',
       consumers=','.join(IDS),registry=['bkx-embedded=e1','bkx-lite=l1'],yes=True,
       github_repo='Mikeyphw/bkx',auth='none')
    result=actions_cli(args,tmp_path)
    assert result['complete'] and observed[0]['execute']
    assert observed[0]['registry_ids']=={'bkx-embedded':'e1','bkx-lite':'l1'}
