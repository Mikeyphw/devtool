"""NEXT-06 disposable git fixtures: no network, no real repository mutation."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from devtool.repository_extraction import plan,run,ExtractionBlocked,_state_paths,_read_manifest


def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True)
    return p.stdout.strip()


def fixtures(tmp,*,extra=False,unknown=False,symlink=False,dirty=False,history=False,cleanup=False):
    roots=[]
    for name in ('mono','standalone'):
        p=tmp/name;p.mkdir()
        git(p,'init','-q');git(p,'config','user.name','Test');git(p,'config','user.email','t@example.org')
        git(p,'remote','add','origin','https://example.org/'+name+'.git')
        (p/'.gitignore').write_text('*.tmp\nlocal.properties\n')
        (p/'README.md').write_text('Shared project\n')
        if name=='mono':
            (p/'apps/xbs').mkdir(parents=True)
            (p/'apps/xbs/go.mod').write_text('module example.org/xbs\n\ngo 1.21\n')
            (p/'apps/xbs/main.go').write_text('package main\nfunc main(){}\n')
            (p/'targets.txt').write_text('target=xbs\nrunner="command"\n')
        git(p,'add','.');git(p,'commit','-qm','base')
        roots.append(p)
    src,dst=roots
    (src/'apps/xbs/.tmp').mkdir()
    (src/'apps/xbs/.tmp/cache.tmp').write_bytes(b'GEN_STAGE_1')
    (src/'apps/xbs/local.properties').write_bytes(b'sdk.dir=/machine\n')
    if extra:(src/'apps/xbs/.tmp/other.tmp').write_bytes(b'EXTRA')
    if unknown:(src/'apps/xbs/new.go').write_text('unknown')
    if symlink:(src/'apps/xbs/bad.tmp').symlink_to('/tmp/elsewhere')
    if dirty:(dst/'README.md').write_text('dirty')
    rows=[]
    for p in roots:
        rows.append({'path':str(p),'head':git(p,'rev-parse','HEAD'),'tree':git(p,'rev-parse','HEAD^{tree}'),
                     'branch':git(p,'symbolic-ref','--short','HEAD'),'remote':'origin',
                     'remote_url':'https://example.org/'+p.name+'.git'})
    config={'schema':'devtool.repo-extraction.v1','source':rows[0],'destination':rows[1],
           'moves':[{'from':'apps/xbs','to':'.'}],
           'source_rewrites':[{'path':'targets.txt','old':'target=xbs\n','new':'target=extracted\n'}],
           'forbid_source_references':['target=xbs','apps/xbs'],
           'preserve_history':history,'clean_ignored':cleanup}
    f=tmp/'extract.json';f.write_text(json.dumps(config))
    return src,dst,f,tmp/'escrow'


def test_plan_readonly_and_prepare_escrow(tmp_path):
    src,dst,f,state=fixtures(tmp_path,history=True)
    p=plan(f,state);assert p['ok'],p
    assert p['tracked_moves']==2 and p['generated_files']==2
    assert not state.exists()
    a=run('prepare',f,state,yes=True)
    assert a['state']=='PREPARED' and a['backup_files']==2
    assert git(src,'rev-parse','HEAD')==_read_manifest(f)['source']['head']
    assert git(dst,'rev-parse','HEAD')==_read_manifest(f)['destination']['head']
    base,_,_= _state_paths(f,state,_read_manifest(f))
    assert (base/'source-history.bundle').is_file()
    assert (base/'generated/apps/xbs/local.properties').read_bytes()==b'sdk.dir=/machine\n'


def test_split_commits_history_and_idempotence(tmp_path):
    src,dst,f,state=fixtures(tmp_path,history=True)
    old_src=git(src,'rev-parse','HEAD');old_dst=git(dst,'rev-parse','HEAD')
    run('prepare',f,state,yes=True)
    res=run('execute',f,state,yes=True)
    assert res['state']=='COMPLETE'
    assert git(dst,'rev-parse','HEAD^')==old_dst and git(src,'rev-parse','HEAD^')==old_src
    assert (dst/'go.mod').is_file() and (dst/'main.go').is_file()
    assert not (src/'apps/xbs/main.go').exists()
    assert (src/'targets.txt').read_text().startswith('target=extracted')
    assert (src/'apps/xbs/.tmp/cache.tmp').is_file()  # ignored bytes retained by default
    assert git(src,'status','--porcelain')=='' and git(dst,'status','--porcelain')==''
    assert run('resume',f,state,yes=True)['state']=='COMPLETE'
    assert git(src,'rev-parse','HEAD')==res['source_commit']


def test_rejected_unknown_or_dirty_do_not_change_repositories(tmp_path):
    src,dst,f,state=fixtures(tmp_path,unknown=True)
    assert not plan(f,state)['ok']
    with pytest.raises(ExtractionBlocked):run('prepare',f,state,yes=True)
    assert not (dst/'go.mod').exists()


def test_backup_tamper_blocks_before_first_commit(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    run('prepare',f,state,yes=True)
    base,_,_= _state_paths(f,state,_read_manifest(f))
    (base/'generated/apps/xbs/.tmp/cache.tmp').write_bytes(b'BAD')
    with pytest.raises(ExtractionBlocked,match='tampered'):run('execute',f,state,yes=True)
    assert git(src,'rev-parse','HEAD')==_read_manifest(f)['source']['head']
    assert git(dst,'rev-parse','HEAD')==_read_manifest(f)['destination']['head']


def test_resume_after_destination_committed_before_source(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    run('prepare',f,state,yes=True)
    base,_,_= _state_paths(f,state,_read_manifest(f))
    # inject safe pre-source failure only AFTER destination becomes committed
    original=src/'apps/xbs/.tmp/cache.tmp'
    # A simulated failure occurs after destination commit by monkeypatching _commit_scoped.
    import devtool.repository_extraction as mod
    fn=mod._commit_scoped
    def interrupt(repo,paths,subject):
        if repo == src: raise ExtractionBlocked('synthetic interrupted source stage')
        return fn(repo,paths,subject)
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as m:
        m.setattr(mod,'_commit_scoped',interrupt)
        with pytest.raises(ExtractionBlocked,match='synthetic'):run('execute',f,state,yes=True)
    journal=json.loads((base/'journal.json').read_text())
    assert journal['destination_commit'] and journal['source_commit'] is None
    assert git(src,'rev-parse','HEAD')==_read_manifest(f)['source']['head']
    assert run('resume',f,state,yes=True)['state']=='COMPLETE'


def test_source_references_block_if_not_pruned(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    (src/'remainder.txt').write_text('apps/xbs')
    git(src,'add','.');git(src,'commit','-qm','remainder')
    c=json.loads(f.read_text());c['source']['head']=git(src,'rev-parse','HEAD');c['source']['tree']=git(src,'rev-parse','HEAD^{tree}')
    f.write_text(json.dumps(c))
    assert 'dangling' in plan(f,state)['reason']


def test_ignored_cleanup_is_opt_in_and_backup_persists(tmp_path):
    src,dst,f,state=fixtures(tmp_path,cleanup=True)
    run('prepare',f,state,yes=True)
    assert run('execute',f,state,yes=True)['state']=='COMPLETE'
    assert not (src/'apps/xbs/.tmp/cache.tmp').exists()
    base,_,_=_state_paths(f,state,_read_manifest(f))
    assert (base/'generated/apps/xbs/.tmp/cache.tmp').is_file()


def test_missing_approval_and_symlink_rejection(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    with pytest.raises(ExtractionBlocked,match='--yes'):run('prepare',f,state)
    (src/'apps/xbs/.tmp/suspicious.tmp').symlink_to('/etc/passwd')
    assert plan(f,state)['ok'] is False


def test_interrupted_destination_file_staging_is_resumable(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    run('prepare',f,state,yes=True)
    (dst/'go.mod').write_bytes((src/'apps/xbs/go.mod').read_bytes())
    info=plan(f,state)
    assert info['ok'] and info['repositories']['destination']=='PARTIAL_OWNED',info
    assert run('resume',f,state,yes=True)['state']=='COMPLETE'


def test_bad_partial_destination_content_rejected_before_commit(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    run('prepare',f,state,yes=True)
    (dst/'go.mod').write_bytes(b'evil')
    assert not plan(f,state)['ok']
    with pytest.raises(ExtractionBlocked):run('resume',f,state,yes=True)
    assert git(src,'rev-parse','HEAD')==_read_manifest(f)['source']['head']


def test_executable_mode_preserved(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    (src/'apps/xbs/main.go').chmod(0o755)
    git(src,'add','.');git(src,'commit','-qm','executable')
    c=json.loads(f.read_text());c['source']['head']=git(src,'rev-parse','HEAD');c['source']['tree']=git(src,'rev-parse','HEAD^{tree}')
    f.write_text(json.dumps(c))
    run('prepare',f,state,yes=True)
    assert run('execute',f,state,yes=True)['state']=='COMPLETE'
    assert git(dst,'ls-files','--stage','main.go').startswith('100755 ')


def test_explicit_go_relative_parent_repaired_or_blocked(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    (src/'apps/xbs/go.mod').write_text('module example.org/xbs\nreplace example.org/core => ../../core\n')
    git(src,'add','.');git(src,'commit','-qm','legacy')
    c=json.loads(f.read_text());c['source']['head']=git(src,'rev-parse','HEAD');c['source']['tree']=git(src,'rev-parse','HEAD^{tree}')
    f.write_text(json.dumps(c))
    assert plan(f,state)['ok'] is False
    c['destination_rewrites']=[{'path':'go.mod','old':'replace example.org/core => ../../core','new':'replace example.org/core => ./core'}]
    f.write_text(json.dumps(c))
    assert plan(f,state)['ok'] is True


def test_dirty_source_after_prepare_blocks_before_destination_commit(tmp_path):
    src,dst,f,state=fixtures(tmp_path)
    run('prepare',f,state,yes=True)
    (src/'targets.txt').write_text('user edit')
    with pytest.raises(ExtractionBlocked):run('execute',f,state,yes=True)
    assert not (dst/'go.mod').exists()
