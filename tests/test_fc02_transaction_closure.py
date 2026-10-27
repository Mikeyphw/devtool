"""FC-02 adversarial Git publication/recovery contract; real disposable repositories."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import pytest
from devtool import adoption_staged as staged
from devtool import onboarding as on
from devtool.adoption_batch import AdoptionBlocked
from test_rm17_rm18_staged import fixture, manifest, git
from test_rm20_onboarding import repo as onboarding_repo


def test_late_worktree_edit_preserved_without_complete(tmp_path,monkeypatch):
    root,row = fixture(tmp_path,'sample')
    m=manifest(tmp_path,[row]); state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    original=staged.gg.reconcile
    def edited(repo,source,target,hashes,preimages,escrow,path_guard):
        (repo/'README').write_text('foreign last-minute edit\n')
        return original(repo,source,target,hashes,preimages,escrow,path_guard)
    monkeypatch.setattr(staged.gg,'reconcile', edited)
    with pytest.raises(AdoptionBlocked,match='PARTIAL_COMMIT'):
        staged.publish(m,state,yes=True)
    assert (root/'README').read_text()=='foreign last-minute edit\n'
    assert json.loads(next(state.glob('staged-*.json')).read_text())['overall']=='PARTIAL_COMMIT'


@pytest.mark.parametrize("hardlink_mode", ["missing", "denied"])
def test_late_edit_after_check_moved_to_escrow_not_destroyed(tmp_path,monkeypatch, hardlink_mode):
    root,row=fixture(tmp_path,'sample')
    m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    # An adversarial mutation between _safe_checkout and the rename.
    original=staged.gg.os.rename
    # Android Python 3.14 may not expose hardlinks. The safety path must
    # preserve a late edit without ever calling the hardlink syscall.
    if hardlink_mode == "missing":
        # Termux Python 3.14 can omit os.link entirely.  Never assume it exists.
        monkeypatch.delattr(staged.gg.os, "link", raising=False)
    else:
        def hardlink_forbidden(*args, **kwargs):
            raise OSError("hardlink unavailable on this Termux filesystem")
        # Other POSIX builds expose os.link but deny the syscall.
        monkeypatch.setattr(staged.gg.os, "link", hardlink_forbidden, raising=False)
    fired=[False]
    def swapped(src,dst,*args,**kwargs):
        if not fired[0] and str(src)=='README':
            fired[0]=True
            (root/'README').write_text('foreign late bytes\n')
        return original(src,dst,*args,**kwargs)
    monkeypatch.setattr(staged.gg.os,'rename',swapped)
    with pytest.raises(AdoptionBlocked,match='PARTIAL_COMMIT'):
        staged.publish(m,state,yes=True)
    copies=list((state/'fc02-escrow').rglob('*.original'))
    assert fired[0]
    assert any(p.read_bytes()==b'foreign late bytes\n' for p in copies)
    assert (root/'README').read_bytes()==b'foreign late bytes\n'
    # Retaining the full original in escrow is mandatory even after restoring
    # the visible worktree path; recovery never consumes its only copy.
    assert (root/'README').stat().st_nlink == 1



def test_late_edit_competing_recreation_keeps_both_bytes(tmp_path, monkeypatch):
    root,row=fixture(tmp_path,'sample')
    m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    orig_rename=staged.gg.os.rename
    fired=[False]
    def racing_rename(src,dst,*args,**kwargs):
        if not fired[0] and str(src)=='README':
            fired[0]=True
            (root/'README').write_bytes(b'late source bytes\n')
            out=orig_rename(src,dst,*args,**kwargs)
            (root/'README').write_bytes(b'other new writer\n')
            return out
        return orig_rename(src,dst,*args,**kwargs)
    monkeypatch.setattr(staged.gg.os,'rename',racing_rename)
    with pytest.raises(AdoptionBlocked,match='PARTIAL_COMMIT'):
        staged.publish(m,state,yes=True)
    assert fired[0]
    assert (root/'README').read_bytes()==b'other new writer\n'
    assert any(p.read_bytes()==b'late source bytes\n'
               for p in (state/'fc02-escrow').rglob('*.original'))

def test_prep_journal_and_ref_substitution_cannot_publish(tmp_path):
    root,row=fixture(tmp_path,'sample');m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    result=staged.prepare(m,state,yes=True)
    rec=result['repositories'][row['id']]
    original=rec['prepared_commit']
    # Produce a different commit with identical approved path names, unapproved bytes.
    candidate, tree=staged.gg.pinned_commit(root,row['source_head'], {'README':b'intruder\n',
        '.devtool.toml': (root/'.devtool.toml').read_bytes()},b'attacker\n')
    # Force a modified journal too. Both local writable artifacts are attacker-controlled.
    rec['hashes']['README']=staged.gg.digest(b'intruder\n')
    rec['prepared_commit']=candidate;rec['prepared_tree']=tree
    staged.ab._save(next(state.glob('staged-*.json')),result)
    git(root,'update-ref',rec['prepared_ref'],candidate,original)
    with pytest.raises(AdoptionBlocked,match='independently reviewed overlay'):
        staged.publish(m,state,yes=True)
    assert git(root,'rev-parse','HEAD')==row['source_head']


def test_replay_preparing_after_interrupted_prepare(tmp_path):
    root,row=fixture(tmp_path,'sample');m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    prepared=staged.prepare(m,state,yes=True)
    prepared['overall']='PREPARING'  # durable crash snapshot after ref write
    staged.ab._save(next(state.glob('staged-*.json')),prepared)
    again=staged.prepare(m,state,yes=True)
    assert again['overall']=='PREPARED'
    assert staged.publish(m,state,yes=True)['overall']=='COMPLETE'
    assert git(root,'status','--porcelain')==''


def test_preparing_abort_does_not_move_branch(tmp_path):
    root,row=fixture(tmp_path,'sample');m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    prepared=staged.prepare(m,state,yes=True); prepared['overall']='PREPARING'
    staged.ab._save(next(state.glob('staged-*.json')),prepared)
    recovery=staged.recover(m,state,yes=True)
    assert recovery['overall']=='ROLLED_BACK'
    assert git(root,'rev-parse','HEAD')==row['source_head']


def test_onboarding_concurrent_config_created_after_scan(tmp_path,monkeypatch):
    root=onboarding_repo(tmp_path)
    from tests.test_rm20_onboarding import reviewed
    spec=reviewed(root);plan=on.inventory(root,review_spec=spec)
    real_open=on.os.open
    def intrude(path,flags,*args,**kwargs):
        if Path(path)==root/'.devtool.toml' and (flags & on.os.O_EXCL):
            (root/'.devtool.toml').write_text('foreign configuration\n')
        return real_open(path,flags,*args,**kwargs)
    monkeypatch.setattr(on.os,'open',intrude)
    with pytest.raises(on.OnboardingBlocked,match='concurrent config'):
        on.apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert (root/'.devtool.toml').read_text()=='foreign configuration\n'
    assert git(root,'rev-parse','HEAD')==plan['source']['head']


def test_onboarding_unreviewed_head_update_cas_refuses(tmp_path,monkeypatch):
    root=onboarding_repo(tmp_path)
    from tests.test_rm20_onboarding import reviewed
    spec=reviewed(root);plan=on.inventory(root,review_spec=spec)
    original=on.gg.cas_branch
    newer=[]
    def concurrent(repo,branch,new,old):
        (repo/'LICENSE').write_text('outside commit\n')
        git(repo,'add','LICENSE');git(repo,'commit','-qm','unreviewed')
        newer.append(git(repo,'rev-parse','HEAD'))
        return original(repo,branch,new,old)
    monkeypatch.setattr(on.gg,'cas_branch',concurrent)
    with pytest.raises(on.OnboardingBlocked,match='lease rejected'):
        on.apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert git(root,'rev-parse','HEAD')==newer[0]
    assert (root/'LICENSE').read_text()=='outside commit\n'
    # The owned candidate file is preserved for manual review, not accidentally
    # unlinked after a second process has been allowed to edit it.
    assert (root/'.devtool.toml').exists()


def test_resume_after_kill_between_escrow_and_exclusive_file_creation(tmp_path,monkeypatch):
    root,row=fixture(tmp_path,'sample'); m=manifest(tmp_path,[row]); state=tmp_path/'journal'
    staged.prepare(m,state,yes=True)
    original=staged.gg.os.open
    once=[False]
    def killed(path,flags,*args,**kwargs):
        if path=='README' and flags & staged.gg.os.O_EXCL and not once[0]:
            once[0]=True
            raise RuntimeError('simulated SIGKILL immediately before new file creation')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(staged.gg.os,'open',killed)
    with pytest.raises(AdoptionBlocked,match='PARTIAL_COMMIT'):
        staged.publish(m,state,yes=True)
    assert once[0] and not (root/'README').exists()
    assert any(x.read_bytes()==b'old\n' for x in (state/'fc02-escrow').rglob('*.original'))
    monkeypatch.setattr(staged.gg.os,'open',original)
    receipt=staged.publish(m,state,yes=True)
    assert receipt['overall']=='COMPLETE'
    assert (root/'README').read_bytes()==b'new\n' and git(root,'status','--porcelain')==''


def test_recovered_prep_ref_tamper_does_not_pass_replay(tmp_path):
    root,row=fixture(tmp_path,'sample');m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    prepared=staged.prepare(m,state,yes=True)
    rec=prepared['repositories'][row['id']]
    prepared['overall']='PREPARING'
    staged.ab._save(next(state.glob('staged-*.json')),prepared)
    candidate,_=staged.gg.pinned_commit(root,row['source_head'], {'README':b'foreign bytes\n'},b'fake\n')
    git(root,'update-ref',rec['prepared_ref'],candidate,rec['prepared_commit'])
    with pytest.raises(AdoptionBlocked,match='prepared-ref collision'):
        staged.prepare(m,state,yes=True)
    assert git(root,'rev-parse','HEAD')==row['source_head']


def test_reconcile_refuses_parent_symlink_without_foreign_write(tmp_path):
    from devtool import guarded_git as gg
    owner=tmp_path/'owner'; owner.mkdir()
    foreign=tmp_path/'foreign'; foreign.mkdir()
    (foreign/'victim').write_bytes(b'foreign protected\n')
    (owner/'sub').symlink_to(foreign, target_is_directory=True)
    with pytest.raises((gg.GuardedGitError, AdoptionBlocked, OSError)):
        gg.reconcile(owner,'a'*40,'b'*40,{'sub/victim':gg.digest(b'update')},
                     {'sub/victim':gg.digest(b'foreign protected\n')},tmp_path/'escrow',
                     staged.ab._path)
    assert (foreign/'victim').read_bytes()==b'foreign protected\n'


def test_prepared_extra_unapproved_git_path_rejected(tmp_path):
    root,row=fixture(tmp_path,'sample');m=manifest(tmp_path,[row]);state=tmp_path/'journal'
    prepared=staged.prepare(m,state,yes=True)
    rec=prepared['repositories'][row['id']]
    # Same approved README bytes, with an unreviewed added file.
    changed={}
    for rel,digest in rec['hashes'].items():
        if digest is not None:
            blob=subprocess.run(['git','-C',str(root),'show',rec['prepared_commit']+':'+rel],
                                check=True,capture_output=True).stdout
            changed[rel]=blob
        else:
            changed[rel]=None
    changed['secret.txt']=b'unreviewed\n'
    commit,tree=staged.gg.pinned_commit(root,row['source_head'],changed,b'unapproved extra path\n')
    rec['prepared_commit']=commit;rec['prepared_tree']=tree
    # The independent validator rejects the extra Git tree path, irrespective
    # of the journal's claimed SHA list.
    with pytest.raises((AdoptionBlocked, staged.gg.GuardedGitError)):
        staged._verify_record(root,row,rec)


def test_locked_git_index_blocks_reconciliation_without_overwrite(tmp_path):
    from devtool import guarded_git as gg
    root,onrow=fixture(tmp_path,'sample')
    index=Path(git(root,'rev-parse','--git-path','index'))
    if not index.is_absolute(): index=root/index
    lock=Path(str(index)+'.lock');lock.write_bytes(b'foreign Git process lease\n')
    original=index.read_bytes()
    with pytest.raises(gg.GuardedGitError,match='index locked'):
        gg.reconcile_index_locked(root,onrow['source_head'])
    assert index.read_bytes()==original
    assert lock.read_bytes()==b'foreign Git process lease\n'
