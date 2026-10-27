"""NEXT-05 disposable Git safety tests; no remote dispatch or live checkout mutation."""
from __future__ import annotations
import hashlib,json,os,subprocess,zipfile
from pathlib import Path
import pytest
from devtool.adoption_batch import plan,apply,AdoptionBlocked,_read_config


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()


def sha(raw):return hashlib.sha256(raw).hexdigest()


def fixture(tmp,n,*,rename=False,wrapper=False,migration=False):
    root=tmp/(f'renamed_{n}' if rename else n);root.mkdir()
    git(root,'init','-q');git(root,'config','user.name','T');git(root,'config','user.email','t@example.org')
    git(root,'remote','add','origin',f'https://example.org/repo/{n}.git')
    conf='[project]\nrunner="python"\n[workspace]\ndefault_target="app"\n[wrapper]\nschema=2\n[targets.app]\nrunner="python"\nroot="."\n'
    if migration:
        conf=conf.replace('runner="python"','kind="python"',1)
    (root/'.devtool.toml').write_text(conf)
    (root/'readme.txt').write_text('before\n')
    (root/'.gitignore').write_text('.devtool/\n')
    git(root,'add','.');git(root,'commit','-qm','base')
    head=git(root,'rev-parse','HEAD');tree=git(root,'rev-parse','HEAD^{tree}')
    filename='readme.txt';a=b'after\n';b=b'before\n'
    manifest={'schema_version':2,'type':'overlay','name':'adoption-fixture','inventory':{'format':1,'entries':[
        {'path':filename,'action':'modify','sha256':sha(a),'size':len(a),'source_sha256':sha(b)}]},
        'validation':{'required':False},'base_contract_mode':'touched-files','base_commit':head,'base_tree':tree,
        'touched_paths':[filename],'source_sha256':{filename:sha(b)},'expected_post_sha256':{filename:sha(a)},
        'apply':{'requires_clean_worktree':True,'commit':{'enabled':True,'mode':'new','strategy':'single',
                                                         'message':f'devtool: adopt {n}'}}}
    archive=tmp/f'{n}.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(filename,a)
        z.writestr('.devtool-artifact.json',json.dumps(manifest))
    row={'id':n,'path':str(root),'source_head':head,'source_tree':tree,
         'remote':'origin','remote_url':f'https://example.org/repo/{n}.git',
         'branch':git(root,'symbolic-ref','--short','HEAD'),
         'overlay':str(archive),'overlay_sha256':sha(archive.read_bytes()),
         'wrapper':wrapper,'migrate_config':migration}
    return root,row


def manifest(tmp,rows):
    p=tmp/'adoption.json';p.write_text(json.dumps({'schema':'devtool.multi-repo-adoption.v1','repositories':rows}))
    return p


def test_global_preflight_stops_all_if_second_repo_dirty(tmp_path):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta')
    m=manifest(tmp_path,[ra,rb]);st=tmp_path/'state'
    original=git(a,'rev-parse','HEAD')
    (b/'unrelated').write_text('dirty')
    assert plan(m,st)['overall']=='BLOCKED'
    with pytest.raises(AdoptionBlocked,match='global prepare blocked'):apply(m,st,yes=True)
    assert git(a,'rev-parse','HEAD')==original and (a/'readme.txt').read_text()=='before\n'


def test_two_repos_idempotent_commit_and_provenance(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha',rename=True,wrapper=True)
    b,rb=fixture(tmp_path,'beta')
    m=manifest(tmp_path,[ra,rb]);st=tmp_path/'state'
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    initial=plan(m,st);assert initial['safe_to_apply'],initial
    assert initial['read_only'] and not st.exists()
    result=apply(m,st,yes=True)
    assert result['overall']=='COMPLETE'
    assert git(a,'rev-parse','HEAD^')==ra['source_head']
    assert git(b,'rev-parse','HEAD^')==rb['source_head']
    assert not git(a,'status','--porcelain') and not git(b,'status','--porcelain')
    assert (a/'devtoolw').is_file() and (a/'devtoolw.cmd').is_file()
    assert git(a,'ls-files','devtoolw')=='devtoolw'
    assert git(a,'ls-files','.devtool-wrapper.lock')=='.devtool-wrapper.lock'
    again=apply(m,st,yes=True)
    assert again['overall']=='COMPLETE' and result['repositories']==again['repositories']


def test_source_and_remote_drift_block_without_mutation(tmp_path):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    git(a,'remote','set-url','origin','https://example.org/other.git')
    assert plan(m,st)['repositories'][0]['state']=='BLOCKED'
    with pytest.raises(AdoptionBlocked):apply(m,st,yes=True)
    assert git(a,'rev-parse','HEAD')==ra['source_head']


def test_legacy_migration_preview_is_read_only(tmp_path):
    a,ra=fixture(tmp_path,'alpha',migration=True)
    m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    before=(a/'.devtool.toml').read_bytes()
    info=plan(m,st)
    assert info['safe_to_apply'],info
    assert (a/'.devtool.toml').read_bytes()==before
    assert '.devtool.toml' in info['repositories'][0]['paths']


def test_manifest_rejects_renamed_remote_and_symlink(tmp_path):
    a,ra=fixture(tmp_path,'alpha');p=manifest(tmp_path,[ra]);st=tmp_path/'state'
    ra['remote_url']='https://other.invalid/repo'
    p.write_text(json.dumps({'schema':'devtool.multi-repo-adoption.v1','repositories':[ra]}))
    assert plan(p,st)['overall']=='BLOCKED'
    bad=a/'devtoolw';bad.symlink_to('/tmp/not-a-managed-launcher')
    ra['wrapper']=True
    p.write_text(json.dumps({'schema':'devtool.multi-repo-adoption.v1','repositories':[ra]}))
    assert plan(p,st)['overall']=='BLOCKED'


def test_legacy_config_committed_with_overlay(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha',migration=True,wrapper=True)
    m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    out=apply(m,st,yes=True)
    assert out['ok']
    assert 'kind="python"' not in (a/'.devtool.toml').read_text()
    changed=git(a,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
    assert '.devtool.toml' in changed and 'devtoolw' in changed and 'readme.txt' in changed
    assert git(a,'rev-parse','HEAD^')==ra['source_head']


def test_applied_uncommitted_is_scoped_one_commit(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    (a/'readme.txt').write_text('after\n')
    info=plan(m,st)
    assert info['repositories'][0]['state']=='APPLIED_UNCOMMITTED',info
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    assert apply(m,st,yes=True)['overall']=='COMPLETE'
    assert git(a,'rev-parse','HEAD^')==ra['source_head']
    assert git(a,'status','--porcelain')==''


def test_interrupt_after_first_repo_durable_resume(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta')
    m=manifest(tmp_path,[ra,rb]);st=tmp_path/'state'
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    import devtool.adoption_batch as mod
    original=mod._stage_zip
    def broken(repo,row,target):
        if row['id']=='beta': raise OSError('simulated Nth checkout interruption')
        return original(repo,row,target)
    # Global PREPARE catches this without committing alpha.
    monkeypatch.setattr(mod,'_stage_zip',broken)
    with pytest.raises(OSError,match='simulated'):apply(m,st,yes=True)
    assert git(a,'rev-parse','HEAD')==ra['source_head']
    monkeypatch.setattr(mod,'_stage_zip',original)
    out=apply(m,st,yes=True)
    assert out['overall']=='COMPLETE'


def test_partial_failure_after_commit_keeps_predecessor_truth(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta')
    m=manifest(tmp_path,[ra,rb]);st=tmp_path/'state'
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    import devtool.adoption_batch as mod
    original=mod._stage_zip
    count={'beta':0}
    def broke_during_apply(repo,row,target):
        if row['id']=='beta':
            count['beta']+=1
            if count['beta']==2: raise RuntimeError('interrupted at second repository')
        return original(repo,row,target)
    monkeypatch.setattr(mod,'_stage_zip',broke_during_apply)
    with pytest.raises(AdoptionBlocked,match='partial batch'):apply(m,st,yes=True)
    assert git(a,'rev-parse','HEAD')!=ra['source_head']
    assert git(b,'rev-parse','HEAD')==rb['source_head']
    state=json.loads(next(st.glob('*.json')).read_text())
    assert state['overall']=='PARTIAL'
    assert state['repositories']['alpha']['state']=='COMMITTED'
    assert state['repositories']['beta']['state']=='COMMITTING'
    monkeypatch.setattr(mod,'_stage_zip',original)
    assert apply(m,st,yes=True)['overall']=='COMPLETE'


def test_dirty_ignored_generated_paths_not_deleted(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    gen=a/'.devtool/generated.bin';gen.parent.mkdir();gen.write_bytes(b'cache bytes')
    monkeypatch.setenv('PYTHONPATH',str(Path(__file__).parents[1]))
    assert apply(m,st,yes=True)['overall']=='COMPLETE'
    assert gen.read_bytes()==b'cache bytes'


def test_no_yess_and_source_hash_tamper(tmp_path):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);st=tmp_path/'state'
    with pytest.raises(AdoptionBlocked,match='requires --yes'):apply(m,st)
    (tmp_path/'alpha.zip').write_bytes(b'tamper')
    assert plan(m,st)['overall']=='BLOCKED'
    assert git(a,'rev-parse','HEAD')==ra['source_head']
