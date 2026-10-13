"""Original ACTIONS-09 G5 provider-native polyglot cross-platform CI fixtures."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
import pytest

from devtool.actions_polyglot import (LANGUAGES,SYSTEMS,WORKFLOW,WORKFLOW_PATH,
    dimensions,ci_dispatch,ci_check,ci_collect,_cell_receipt,artifact_name)
from devtool.github_actions import GitHubActionsError
from devtool.cli import build_parser

SHA='a'*40
RUN={'id':123,'run_attempt':2,'workflow_id':991,'head_sha':SHA,'head_branch':'main',
     'event':'workflow_dispatch','status':'completed','conclusion':'success',
     'repository':{'full_name':'Mikeyphw/devtool'}}
ALL={(a,b) for a in LANGUAGES for b in SYSTEMS}

class Fake:
    def __init__(self, cells=ALL, attempt=2):
        self.cells=set(cells)
        self.attempt=attempt
        self.jobs=[]
        for i,(language,system) in enumerate(sorted(self.cells)):
            self.jobs.append({'id':i+1,'name':f'polyglot ({language}, {system})',
                  'run_attempt':attempt,'status':'completed','conclusion':'success'})
        self.artifacts=[]
        for i,(a,b) in enumerate(sorted(self.cells)):
            self.artifacts.append({'id':i+10,'name':artifact_name(a,b,attempt),
                'digest':'sha256:'+'b'*64,'size_in_bytes':1000,'expired':False,
                'workflow_run':{'id':123,'head_sha':SHA,'run_attempt':attempt}})
    def get_run(self,slug,rid):return dict(RUN)
    def workflow(self,slug,workflow):return {'id':991,'path':WORKFLOW_PATH}
    def run_attempt_jobs(self,slug,rid,attempt):return self.jobs
    def run_artifacts(self,slug,rid):return self.artifacts
    def get_artifact(self,slug,aid):return next(x for x in self.artifacts if x['id']==aid)


def receipt(language,system,*,run_id=123,attempt=2,sha=SHA,failed=False):
    return {'schema_version':1,'kind':'devtool.actions.g5.cell',
        'repository':'Mikeyphw/devtool','head_sha':sha,'run_id':run_id,
        'run_attempt':attempt,'language':language,'operating_system':system,
        'languages':list(LANGUAGES),'operating_systems':list(SYSTEMS),
        'fixture_sha256':'c'*64,'tests_passed':1,'tests_failed':1 if failed else 0,
        'status':'passed','qualified_release':False,'source_attested':False}

def zip_receipt(p,obj):
    with zipfile.ZipFile(p,'w') as z:z.writestr('receipt.json',json.dumps(obj))
    return p


def test_cli_registry_authority():
    parser=build_parser()
    for name in ('plan','run','check','collect'):
        cmd=['actions','ci',name,'--json']
        if name in ('run','plan'):cmd+=['--ref','main']
        else:cmd+=['--run-id','123','--expected-sha',SHA]
        x=parser.parse_args(cmd)
        assert x.actions_command=='ci' and x.ci_command==name and x.json
        if name=='run':assert not x.yes
        if name=='collect':assert not x.download


def test_dimensions_allowlist_and_bounded_matrix():
    assert dimensions('go,python',LANGUAGES)==['python','go']
    for bad in ('','python,python','python,evil','python;rm -rf /','python,,go'):
        with pytest.raises(GitHubActionsError):dimensions(bad,LANGUAGES)


def test_plan_and_dispatch_reuse_existing_provider_and_confirm(monkeypatch,tmp_path):
    from devtool import actions_polyglot as m
    monkeypatch.setattr('devtool.actions_android._git',lambda *a:SHA if a[1]=='rev-parse' else '')
    executed=[]
    def dispatch(client,slug,workflow,ref,inputs,execute):
        executed.append((workflow,ref,inputs,execute))
        return {'workflow_path':WORKFLOW_PATH,'pre_dispatch_sha':SHA,
                'run_id':123 if execute else None}
    monkeypatch.setattr(m,'dispatch_workflow',dispatch)
    result=ci_dispatch(object(),'Mikeyphw/devtool',tmp_path,ref='main',
         languages='rust,python',systems='windows-latest',execute=True)
    assert result['matrix_cells']==2 and result['dispatched']
    assert [r[-1] for r in executed]==[False,True]
    assert json.loads(executed[0][2]['languages'])==['python','rust']
    assert executed[0][0]==WORKFLOW
    monkeypatch.setattr('devtool.actions_android._git',lambda *a:' M test' if a[1]=='status' else SHA)
    with pytest.raises(GitHubActionsError,match='clean committed'):
        ci_dispatch(object(),'Mikeyphw/devtool',tmp_path,ref='main',languages='python',systems='ubuntu-latest')


def test_read_only_check_fail_closed_for_missing_failure_wrong_attempt():
    good=Fake()
    ok=ci_check(good,'Mikeyphw/devtool',run_id=123,expected_sha=SHA,
                languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))
    assert ok['matrix_complete'] and len(ok['cells'])==8
    one=Fake(ALL-{('rust','windows-latest')})
    assert not ci_check(one,'Mikeyphw/devtool',run_id=123,expected_sha=SHA,
                languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))['matrix_complete']
    bad=Fake()
    bad.jobs[0]['conclusion']='failure'
    assert not ci_check(bad,'Mikeyphw/devtool',run_id=123,expected_sha=SHA,
                languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))['matrix_complete']
    wrong=Fake(attempt=1)
    with pytest.raises(GitHubActionsError,match='attempt'):
        ci_check(wrong,'Mikeyphw/devtool',run_id=123,expected_sha=SHA,
                languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))
    with pytest.raises(GitHubActionsError,match='source'):
        ci_check(good,'Mikeyphw/devtool',run_id=123,expected_sha='b'*40,
                 languages='python',systems='ubuntu-latest')


def test_untrusted_cell_receipt_cross_attempt_and_tamper(tmp_path):
    z=zip_receipt(tmp_path/'a.zip',receipt('python','ubuntu-latest'))
    kw=dict(slug='Mikeyphw/devtool',run=RUN,language='python',system='ubuntu-latest',
            languages=list(LANGUAGES),systems=list(SYSTEMS))
    assert _cell_receipt(z,**kw)['tests_passed']==1
    for i,raw in enumerate((receipt('python','ubuntu-latest',attempt=1),
             receipt('python','ubuntu-latest',run_id=13),receipt('python','ubuntu-latest',sha='b'*40),
             receipt('python','ubuntu-latest',failed=True),receipt('go','ubuntu-latest'))):
        with pytest.raises(GitHubActionsError):
            _cell_receipt(zip_receipt(tmp_path/f'bad{i}.zip',raw),**kw)
    twice=tmp_path/'twice.zip'
    with zipfile.ZipFile(twice,'w') as z:
        z.writestr('receipt.json',json.dumps(receipt('python','ubuntu-latest')))
        z.writestr('other',b'payload')
    with pytest.raises(GitHubActionsError):_cell_receipt(twice,**kw)
    traversal=tmp_path/'traversal.zip'
    with zipfile.ZipFile(traversal,'w') as z:z.writestr('../receipt.json','bad')
    with pytest.raises(GitHubActionsError):_cell_receipt(traversal,**kw)


def test_collection_never_accepts_partial_attempt_or_extra_artifact(tmp_path):
    f=Fake(ALL-{('python','windows-latest')})
    with pytest.raises(GitHubActionsError,match='incomplete'):
        ci_collect(f,'Mikeyphw/devtool',tmp_path,run_id=123,expected_sha=SHA,
                   languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))
    good=Fake()
    good.artifacts.append(dict(good.artifacts[0]))
    with pytest.raises(GitHubActionsError,match='Duplicate'):
        ci_collect(good,'Mikeyphw/devtool',tmp_path,run_id=123,expected_sha=SHA,
                   languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))
    good=Fake()
    good.artifacts[0]['name']=good.artifacts[0]['name'].replace('attempt2','attempt1')
    with pytest.raises(GitHubActionsError,match='foreign or stale'):
        ci_collect(good,'Mikeyphw/devtool',tmp_path,run_id=123,expected_sha=SHA,
                   languages=','.join(LANGUAGES),systems=','.join(SYSTEMS))


def test_collector_requires_verified_archive_and_registry(monkeypatch,tmp_path):
    f=Fake(cells={('python','ubuntu-latest')})
    z=zip_receipt(tmp_path/'verified.zip',{'schema_version':1,'kind':'devtool.actions.g5.cell',
      'repository':'Mikeyphw/devtool','head_sha':SHA,'run_id':123,'run_attempt':2,
      'language':'python','operating_system':'ubuntu-latest','languages':['python'],
      'operating_systems':['ubuntu-latest'],'fixture_sha256':'c'*64,
      'tests_passed':1,'tests_failed':0,'status':'passed','qualified_release':False,'source_attested':False})
    from devtool import actions_polyglot as mod
    monkeypatch.setattr(mod,'verify_download',lambda *a:{'file':str(z),'sha256':'b'*64})
    called=[]
    monkeypatch.setattr(mod,'register_paths',lambda root,paths,metadata:[called.append(metadata) or {'id':'ababa1234567'}])
    r=ci_collect(f,'Mikeyphw/devtool',tmp_path,run_id=123,expected_sha=SHA,
                languages='python',systems='ubuntu-latest',materialize=True)
    assert r['registered'] and r['verified_artifacts']==1
    assert called[0]['qualified_release'] is False
    assert called[0]['state']=='polyglot-ci-verified-unqualified'


def test_workflow_source_is_reusable_unqualified_and_excludes_signer():
    p=Path(__file__).resolve().parents[1]/'.github/workflows/devtool-polyglot-ci.yml'
    t=p.read_text()
    assert 'workflow_call:' in t and 'workflow_dispatch:' in t
    assert 'windows-latest' in t and 'ubuntu-latest' in t
    assert 'actions/upload-artifact@v5' in t
    assert 'DEVTOOL_RELEASE_KEYSTORE_B64' not in t
    assert 'secrets.' not in t
    assert 'github.run_attempt' in t and 'github.sha' not in t # Runner gets GitHub SHA via environment
