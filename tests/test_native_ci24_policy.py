"""Explicit finite native validation, without claiming hosted acceptance."""
from pathlib import Path
import pytest
import subprocess
import copy
import hashlib
import json
from zipfile import ZipFile
from devtool import ci_tree_evidence as tree, qualification_workflows as q, postmerge_ci as p
from devtool.github_actions import GitHubActionsError
from tests.test_qualification_workflows import FakeApi, fake_artifacts
from tests import w3_validation_support as support
from tests import w4_native_validate as w4

ROOT = Path(__file__).resolve().parents[1]


def test_w4_uses_bounded_local_inventory_and_preserves_hosted_regressions():
    selected = support.reviewed_native_plan(ROOT, w4.TESTS, w4.REVIEWED_CHANGES,
                                            w4.REVIEWED_CHANGES, w4.NATIVE_MODULES)
    assert len(selected) == len(w4.NATIVE_MODULES) == 14
    assert set(support.MANDATORY) <= {Path(p).stem for p in selected}
    assert 'tests/test_rm08_completion.py' in w4.TESTS
    assert 'tests/test_rm08_completion.py' not in selected
    hosted = (ROOT / '.github/workflows/devtool-full-repository-pytest.yml').read_text()
    assert '--shards 24' in hosted and 'summarize' in hosted


@pytest.mark.parametrize('changes', [set(), {'unreviewed.py'}, {'missing.py'}])
def test_native_plan_refuses_unknown_or_absent_changes(changes):
    with pytest.raises(RuntimeError, match='REVIEWED_CHANGE_SET_MISMATCH'):
        support.reviewed_native_plan(ROOT, w4.TESTS, changes, w4.REVIEWED_CHANGES,
                                     w4.NATIVE_MODULES)


@pytest.mark.parametrize('modules', [(), w4.TESTS, ('tests/test_not_present.py',),
                                     ('tests/test_native_ci24_policy.py',)*2,
                                     ('tests/test_native_ci24_policy.py',)])
def test_native_plan_refuses_empty_exhaustive_or_invalid_module_inventory(modules):
    with pytest.raises(RuntimeError, match='INVENTORY_INVALID'):
        support.reviewed_native_plan(ROOT, w4.TESTS, w4.REVIEWED_CHANGES,
                                     w4.REVIEWED_CHANGES, modules)


def test_reviewed_plan_cannot_be_combined_with_standard_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv('DEVTOOL_XDG_DIR', str(tmp_path/'state'))
    monkeypatch.delenv('DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT', raising=False)
    monkeypatch.setattr(support.SourceIdentity, 'digest', lambda *a, **k: 'source')
    monkeypatch.setattr(support, 'changed_paths', lambda root: set(w4.REVIEWED_CHANGES))
    with pytest.raises(RuntimeError, match='PROFILE_CONFLICT'):
        support.run_local(ROOT, w4.TESTS, standard=True, reviewed_changes=w4.REVIEWED_CHANGES,
                          reviewed_native_modules=w4.NATIVE_MODULES)


# CI24 exact-tree evidence and fresh-run fallback.
SLUG='Mikeyphw/devtool'


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


@pytest.fixture
def source(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    git(root,'init','-b','main');git(root,'config','user.email','test@example.invalid');git(root,'config','user.name','Fixture')
    (root/'file').write_text('candidate\n');git(root,'add','file');git(root,'commit','-m','tested')
    tested=git(root,'rev-parse','HEAD');identity=tree.tree(root,tested)
    git(root,'commit','--allow-empty','-m','equivalent main');head=git(root,'rev-parse','HEAD')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    v={'complete':True,'run_attempt':1,'shards_verified':24,'jobs_verified':25,'artifact_archives_verified':25}
    r={'repository':SLUG,'source_sha':tested,'branch':'feature/source','workflow_id':42,'run_id':1234,'state':'PASS_CI24_VERIFIED','verification':v}
    tree.remember(root,r)
    return root,head,tested,identity,r


def fixture_dispatch(monkeypatch,head):
    calls=[]
    def dispatch(*a,**kw):
        calls.append(kw['execute']);return {'pre_dispatch_sha':head,'workflow_id':42,'workflow_path':'.github/workflows/'+q.WORKFLOW,'run_id':5678,'request_id':'a'*32}
    monkeypatch.setattr(q,'dispatch_workflow',dispatch)
    return calls


def test_exact_tree_reuse_requires_ancestry_and_authentic_artifacts(source,tmp_path,monkeypatch):
    root,head,tested,identity,_=source
    api=FakeApi(tested);fake_artifacts(monkeypatch,tmp_path,tested)
    calls=fixture_dispatch(monkeypatch,head)
    assert tree.equivalent(root,head,tested,identity)
    result=q.ci24_start(root,client=api,slug=SLUG)
    assert calls==[False,False]
    assert result['state']=='PASS_CI24_TREE_REUSED' and result['tree_verified'] and not result['ci_verified']
    assert result['tested_source_sha']==tested and result['source_sha']==head and result['request_id'] is None
    assert not result['run_correlated'] and not result['formal_release_qualified']
    resumed=q.ci24_resume(root,client=api,slug=SLUG)
    assert resumed['state']=='PASS_CI24_TREE_REUSED' and resumed['tree_verified'] and not resumed['ci_verified']
    api.remote_conclusion='failure'
    with pytest.raises(GitHubActionsError):q.ci24_resume(root,client=api,slug=SLUG)
    assert calls==[False,False,False]


@pytest.mark.parametrize('mode',['changed-tree','unrelated-history','unavailable-proof','ambiguous','feature','malformed-locator'])
def test_reuse_does_not_bypass_fresh_ci(source,tmp_path,monkeypatch,mode):
    root,head,tested,identity,r=source
    if mode=='changed-tree':
        (root/'file').write_text('changed');git(root,'commit','-am','new bytes')
    elif mode=='unrelated-history':
        # Same tree on an unrelated root cannot qualify history-dependent CI24.
        new=git(root,'commit-tree',identity,'-m','unrelated root');git(root,'reset','--hard',new)
    elif mode=='feature':git(root,'checkout','-b','another-pr')
    elif mode=='ambiguous':tree.remember(root,{**r,'run_id':5679})
    elif mode=='malformed-locator':
        p=next(tree._folder(SLUG,identity).iterdir());p.write_text('{}')
    else:monkeypatch.setattr(q,'_ci24_evaluate',lambda *a: (_ for _ in ()).throw(GitHubActionsError('unavailable proof')))
    head=git(root,'rev-parse','HEAD');calls=fixture_dispatch(monkeypatch,head)
    result=q.ci24_start(root,client=FakeApi(tested),slug=SLUG)
    assert calls==[False,True] and not result['tree_verified']
    assert result['source_sha']==head and result['run_id']==5678


def test_reuse_requires_complete_record_and_stops_on_source_drift(source,monkeypatch):
    root,head,tested,identity,r=source
    with pytest.raises(GitHubActionsError):tree.remember(root,{**r,'verification':{'complete':True}})
    calls=fixture_dispatch(monkeypatch,head)
    def evaluate(*args):
        git(root,'commit','--allow-empty','-m','drift')
        return {'status':'PASS_CI24_VERIFIED','complete':True}
    monkeypatch.setattr(q,'_ci24_evaluate',evaluate)
    with pytest.raises(GitHubActionsError,match='changed'):q.ci24_start(root,client=FakeApi(tested),slug=SLUG)
    assert calls==[False]


# Source-only workflow merge proof.
WF='devtool-dn03-source-qualification.yml'
SCOPE='ubuntu-24.04'


@pytest.fixture
def data(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();git(root,'init','-b','main');git(root,'config','user.name','Fixture');git(root,'config','user.email','test@example.invalid')
    path=root/'.github/workflows'/WF;path.parent.mkdir(parents=True);path.write_bytes((ROOT/'.github/workflows'/WF).read_bytes());git(root,'add','.');git(root,'commit','-m','base')
    base=git(root,'rev-parse','HEAD');git(root,'checkout','-b','feature');(root/'source').write_text('reviewed\n');git(root,'add','source');git(root,'commit','-m','feature');head=git(root,'rev-parse','HEAD')
    git(root,'checkout','main');git(root,'merge','--no-ff','feature','-m','synthetic PR merge');old=git(root,'rev-parse','HEAD');identity=git(root,'rev-parse','HEAD^{tree}')
    event=tmp_path/'event.json';repo={'full_name':p.REPO};event.write_text(json.dumps({'number':56,'pull_request':{'head':{'sha':head,'repo':repo},'base':{'sha':base,'ref':'main','repo':repo}}}))
    env={'GITHUB_REPOSITORY':p.REPO,'GITHUB_SHA':old,'GITHUB_EVENT_NAME':'pull_request','GITHUB_EVENT_PATH':str(event),'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
    proof_file=tmp_path/'tree-proof.json';proof=p.record(root,WF,SCOPE,env,proof_file)
    git(root,'reset','--hard',base);git(root,'merge','--no-ff','feature','-m','actual merge');current=git(root,'rev-parse','HEAD')
    assert current != old and identity==git(root,'rev-parse','HEAD^{tree}')
    env.update(GITHUB_SHA=current,GITHUB_EVENT_NAME='push',GITHUB_REF='refs/heads/main',GITHUB_RUN_ID='456',GH_TOKEN='fixture-only')
    run={'id':123,'workflow_id':42,'path':'.github/workflows/'+WF,'event':'pull_request','run_attempt':1,'status':'completed','conclusion':'success','head_sha':head,'repository':repo,'head_repository':repo,'pull_requests':[{'number':56,'head':{'sha':head},'base':{'ref':'main'}}]}
    pr={'number':56,'merged':True,'merge_commit_sha':current,'head':{'sha':head,'repo':repo},'base':{'ref':'main','repo':repo}}
    commit={'sha':old,'tree':{'sha':identity},'parents':[{'sha':base},{'sha':head}]}
    jobs=[{'name':p.WORKFLOWS[WF][0],'status':'completed','conclusion':'success','run_attempt':1,'steps':[{'name':p.WORKFLOWS[WF][1],'status':'completed','conclusion':'success'}]}]
    archive=tmp_path/'artifact.zip'
    class API:
        def __init__(self):self.reads=[];self.run=run;self.pr=pr;self.commit=commit;self.jobs=jobs;self.proof=proof;self.bad_digest=False;self.expired=False;self.corrupt=False
        def workflow(self,*args):return {'id':42,'path':run['path']}
        def workflow_runs(self,*args,**kw):return [copy.deepcopy(self.run)]
        def get_run(self,*args):self.reads.append('run');return copy.deepcopy(self.run)
        def run_artifacts(self,*args):return [{'name':p.artifact_name(123,1,SCOPE),'id':99}]
        def get_artifact(self,*args):
            with ZipFile(archive,'w') as z:z.writestr('tree-proof.json',json.dumps(self.proof))
            if self.corrupt:archive.write_bytes(b'bad zip')
            self.bytes=archive.read_bytes();return {'name':p.artifact_name(123,1,SCOPE),'id':99,'workflow_run':{'id':123,'head_sha':head},'expired':self.expired,'size_in_bytes':len(self.bytes),'digest':'sha256:'+('f'*64 if self.bad_digest else hashlib.sha256(self.bytes).hexdigest())}
        def artifact_download_location(self,*args):return 'https://results.actions.githubusercontent.com/fixture'
        def _get(self,path):return copy.deepcopy(self.pr if '/pulls/' in path else self.commit)
        def run_attempt_jobs(self,*args):return copy.deepcopy(self.jobs)
    api=API()
    def download(client,url,output):output.write_bytes(client.bytes);return hashlib.sha256(client.bytes).hexdigest(),len(client.bytes)
    monkeypatch.setattr(p,'_download_blob',download)
    return root,env,api,old,identity


def test_verified_merge_tree_reuses_original_test_job(data):
    root,env,api,old,identity=data
    result=p.decide(root,WF,SCOPE,env,api)
    assert result['tested_source_sha']==old and result['source_tree']==identity and result['run_id']==123
    assert len(api.reads)==2


@pytest.mark.parametrize('mode',['digest','expired','corrupt','run-attempt','wrong-workflow','failed-test','skipped-test','wrong-job','wrong-tree','wrong-parent','wrong-pr','not-merged','reused-proof','wrong-host','foreign-repo','changed-source','no-token','pr-event','manual','other-branch'])
def test_nonqualifying_source_proof_always_runs_fresh(data,mode):
    root,env,api,old,identity=data
    if mode=='digest':api.bad_digest=True
    elif mode=='expired':api.expired=True
    elif mode=='corrupt':api.corrupt=True
    elif mode=='run-attempt':api.run['run_attempt']=2
    elif mode=='wrong-workflow':api.run['workflow_id']=43
    elif mode=='failed-test':api.jobs[0]['steps'][0]['conclusion']='failure'
    elif mode=='skipped-test':api.jobs[0]['steps'][0]['conclusion']='skipped'
    elif mode=='wrong-job':api.jobs[0]['name']='wrong job'
    elif mode=='wrong-tree':api.commit['tree']['sha']='a'*40
    elif mode=='wrong-parent':api.commit['parents'].reverse()
    elif mode=='wrong-pr':api.pr['merge_commit_sha']='f'*40
    elif mode=='not-merged':api.pr['merged']=False
    elif mode=='reused-proof':api.proof['mode']='reused'
    elif mode=='wrong-host':api.proof['scope']='windows-2022'
    elif mode=='foreign-repo':api.run['head_repository']={'full_name':'other/devtool'}
    elif mode=='changed-source':(root/'source').write_text('changed')
    elif mode=='no-token':del env['GH_TOKEN']
    elif mode=='pr-event':env['GITHUB_EVENT_NAME']='pull_request'
    elif mode=='manual':env['GITHUB_EVENT_NAME']='workflow_dispatch'
    elif mode=='other-branch':env['GITHUB_REF']='refs/heads/feature'
    assert p.decide(root,WF,SCOPE,env,api) is None


def test_only_source_workflows_can_skip_and_require_read_only_proof():
    import yaml
    for name,(_,test_name) in p.WORKFLOWS.items():
        doc=yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
        assert doc['permissions']=={'contents':'read','actions':'read','pull-requests':'read'}
        steps=next(iter(doc['jobs'].values()))['steps']
        assert next(s for s in steps if s['name']==test_name)['if']=="steps.tree_reuse.outputs.reuse != 'true'"
        assert next(s for s in steps if s.get('id')=='tree_reuse')['env']['GH_TOKEN']=='${{ github.token }}'
        assert next(s for s in steps if s['name']=='Record executed source tree proof')['if'].startswith("success() && steps.tree_reuse.outputs.reuse != 'true'")
        assert 'tree-proof.json' in steps[-1]['with']['path']
    assert not any('tree_reuse' in f.read_text() for f in (ROOT/'.github/workflows').glob('*.yml') if f.name not in p.WORKFLOWS)
