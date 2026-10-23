"""Real disposable Git repositories: RM17/18 staged adoption, recovery and no-force invariants."""
from __future__ import annotations
import hashlib, json, subprocess, zipfile
from pathlib import Path
import pytest
from devtool import adoption_staged as staged
from devtool.adoption_batch import AdoptionBlocked


def git(repo,*args):
    return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout.strip()


def fixture(tmp,n):
    repo=tmp/n;repo.mkdir();git(repo,'init','-q');git(repo,'config','user.name','Test');git(repo,'config','user.email','test@example.org');git(repo,'remote','add','origin',f'https://example.org/{n}.git')
    (repo/'README').write_text('old\n');(repo/'.devtool.toml').write_text('[project]\nkind="python"\n[workspace]\ndefault_target="app"\n[wrapper]\nschema=1\n[targets.app]\nrunner="python"\nroot="."\n');git(repo,'add','.');git(repo,'commit','-qm','source')
    head=git(repo,'rev-parse','HEAD');tree=git(repo,'rev-parse','HEAD^{tree}');old=b'old\n';new=b'new\n';sha=lambda x:hashlib.sha256(x).hexdigest()
    inv={'schema_version':2,'type':'overlay','name':'fixture','inventory':{'format':1,'entries':[{'path':'README','action':'modify','sha256':sha(new),'source_sha256':sha(old),'size':len(new)}]},'validation':{'required':False},'base_contract_mode':'touched-files','base_commit':head,'base_tree':tree,'touched_paths':['README'],'source_sha256':{'README':sha(old)},'expected_post_sha256':{'README':sha(new)}}
    archive=tmp/f'{n}.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('README',new);z.writestr('.devtool-artifact.json',json.dumps(inv))
    row={'id':n,'path':str(repo),'source_head':head,'source_tree':tree,'remote':'origin','remote_url':f'https://example.org/{n}.git','branch':git(repo,'branch','--show-current'),'overlay':str(archive),'overlay_sha256':sha(archive.read_bytes()),'migrate_config':True,'wrapper':True,'repair_gitignore':True}
    return repo,row


def manifest(tmp,rows):
    p=tmp/'manifest.json';p.write_text(json.dumps({'schema':'devtool.multi-repo-adoption.v1','repositories':rows}));return p


def test_staged_prepare_publish_and_repeat_no_duplicate_commits(tmp_path):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta');m=manifest(tmp_path,[ra,rb]);state=tmp_path/'journal'
    assert staged.review(m,state)['ok'] and not state.exists()
    result=staged.prepare(m,state,yes=True);assert result['overall']=='PREPARED'
    for repo,row in ((a,ra),(b,rb)):
        assert git(repo,'rev-parse','HEAD')==row['source_head']
        assert git(repo,'status','--porcelain')==''
        assert git(repo,'rev-parse',result['repositories'][row['id']]['prepared_ref'])
    committed=staged.publish(m,state,yes=True);assert committed['overall']=='COMPLETE'
    for repo,row in ((a,ra),(b,rb)):
        assert git(repo,'rev-parse','HEAD^')==row['source_head']
        assert git(repo,'status','--porcelain')==''
        assert (repo/'README').read_text()=='new\n'
        assert 'kind="python"' not in (repo/'.devtool.toml').read_text()
        assert 'schema = 2' in (repo/'.devtool.toml').read_text()
        assert (repo/'devtoolw').is_file()
        assert (repo/'.gitignore').exists()
    assert staged.publish(m,state,yes=True)['overall']=='COMPLETE'


def test_global_dirty_refusal_before_first_publication(tmp_path):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta');m=manifest(tmp_path,[ra,rb]);state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    (b/'README').write_text('intruder\n')
    with pytest.raises(AdoptionBlocked,match='dirty'):staged.publish(m,state,yes=True)
    assert git(a,'rev-parse','HEAD')==ra['source_head']
    assert git(b,'rev-parse','HEAD')==rb['source_head']


def test_partial_publish_receipt_then_compensating_rollback(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta');m=manifest(tmp_path,[ra,rb]);state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    original=staged._materialize
    def fail(repo,record,target):
        if repo==b:raise RuntimeError('injected second checkout failure after CAS')
        return original(repo,record,target)
    monkeypatch.setattr(staged,'_materialize',fail)
    with pytest.raises(AdoptionBlocked,match='PARTIAL_COMMIT'):staged.publish(m,state,yes=True)
    journal=json.loads(next(state.glob('staged-*.json')).read_text())
    assert journal['overall']=='PARTIAL_COMMIT'
    assert git(a,'rev-parse','HEAD')!=ra['source_head']
    monkeypatch.setattr(staged,'_materialize',original)
    output=staged.recover(m,state,yes=True)
    assert output['overall']=='ROLLED_BACK'
    for repo,row in ((a,ra),(b,rb)):
        assert git(repo,'rev-parse','HEAD')==row['source_head']
        assert git(repo,'status','--porcelain')==''
        assert (repo/'README').read_text()=='old\n'


def test_advance_after_partial_blocks_compensation_to_protect_newer_work(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta');m=manifest(tmp_path,[ra,rb]);state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    original=staged._materialize
    def broken(repo,record,target):
        if repo==b:raise RuntimeError('injected after CAS')
        return original(repo,record,target)
    monkeypatch.setattr(staged,'_materialize',broken)
    with pytest.raises(AdoptionBlocked):staged.publish(m,state,yes=True)
    monkeypatch.setattr(staged,'_materialize',original)
    # Mimic unrelated new commit on alpha after publication.
    (a/'unrelated').write_text('newer\n');git(a,'add','unrelated');git(a,'commit','-qm','third party')
    before=git(a,'rev-parse','HEAD')
    with pytest.raises(AdoptionBlocked,match='HEAD moved'):staged.recover(m,state,yes=True)
    assert git(a,'rev-parse','HEAD')==before
    assert git(b,'rev-parse','HEAD')!=rb['source_head']


def test_preflight_of_all_repos_before_prepared_refs(tmp_path):
    a,ra=fixture(tmp_path,'alpha');b,rb=fixture(tmp_path,'beta');m=manifest(tmp_path,[ra,rb]);state=tmp_path/'journal'
    (b/'evil').symlink_to('/tmp/unowned')
    with pytest.raises(AdoptionBlocked):staged.prepare(m,state,yes=True)
    assert not state.exists() or not list(state.glob('staged-*'))
    assert not git(a,'for-each-ref','--format=%(refname)','refs/devtool/adoption-prepared')


def test_explicit_consent_and_identity_preflight(tmp_path):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);state=tmp_path/'journal'
    with pytest.raises(AdoptionBlocked,match='--yes'):staged.prepare(m,state)
    git(a,'remote','set-url','origin','https://wrong.invalid/override')
    assert not staged.review(m,state)['ok']
    with pytest.raises(AdoptionBlocked,match='remote identity'):staged.prepare(m,state,yes=True)


def test_upgrade_proposes_non_overwriting_three_way_when_source_drifted(tmp_path):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);state=tmp_path/'journal'
    (a/'README').write_text('old\nother addition\n');git(a,'add','.');git(a,'commit','-qm','unrelated newer source')
    head=git(a,'rev-parse','HEAD')
    result=staged.upgrade_review(m,state)
    assert result['ok'] and result['review_required']
    assert result['repositories'][0]['state']=='REBASE_REVIEW_REQUIRED'
    assert result['repositories'][0]['paths'][0]['state'] in ('CONFLICT','CLEAN_MERGE_CANDIDATE')
    assert git(a,'rev-parse','HEAD')==head and git(a,'status','--porcelain')==''
    assert not list(state.glob('staged-*.json'))
    with pytest.raises(AdoptionBlocked):staged.prepare(m,state,yes=True)


def test_unsupported_hook_refuses_without_effects(tmp_path):
    a,ra=fixture(tmp_path,'alpha');ra['offline_hooks']=['curl https://example.org'];m=manifest(tmp_path,[ra]);state=tmp_path/'journal'
    with pytest.raises(AdoptionBlocked,match='offline_hooks'):staged.prepare(m,state,yes=True)
    assert git(a,'rev-parse','HEAD')==ra['source_head']
    assert not list(state.glob('staged-*.json'))


def test_config_extension_survives_and_unknown_legacy_section_refused(tmp_path):
    a,ra=fixture(tmp_path,'alpha');config=a/'.devtool.toml'
    config.write_text(config.read_text()+'\n[x-vendor]\ncustom="keep bytes"\n[extensions.example]\nsecret="stable"\n')
    git(a,'add','.');git(a,'commit','-qm','vendor extension');ra['source_head']=git(a,'rev-parse','HEAD');ra['source_tree']=git(a,'rev-parse','HEAD^{tree}')
    m=manifest(tmp_path,[ra]);state=tmp_path/'state';staged.publish(m,state,yes=True)
    assert 'custom="keep bytes"' in config.read_text()
    assert 'secret="stable"' in config.read_text()
    b,rb=fixture(tmp_path,'beta');other=b/'.devtool.toml';other.write_text(other.read_text()+'\n[unsupported-secrets]\nx=1\n')
    git(b,'add','.');git(b,'commit','-qm','unrecognized');rb['source_head']=git(b,'rev-parse','HEAD');rb['source_tree']=git(b,'rev-parse','HEAD^{tree}')
    mb=manifest(tmp_path,[rb]);st=tmp_path/'s2'
    with pytest.raises(AdoptionBlocked,match='unknown legacy config'):staged.prepare(mb,st,yes=True)
    assert git(b,'rev-parse','HEAD')==rb['source_head']


def test_recovery_plan_after_partial_matches_native_and_staged_state(tmp_path,monkeypatch):
    a,ra=fixture(tmp_path,'alpha');m=manifest(tmp_path,[ra]);state=tmp_path/'state';staged.prepare(m,state,yes=True)
    plan=staged.recovery_plan(m,state)
    assert plan['read_only'] and plan['ok']
    assert plan['repositories'][0]['safe_next_action']=='adopt-batch resume-staged --yes'
