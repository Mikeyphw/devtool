"""NEXT-00/01 targeted source and disposable Git safety tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devtool.historical_transactions import (
    HistoricalRetirementError, inspect_historical_transaction,
    retire_historical_transaction, unretire_historical_transaction,
)
from devtool.transactions import transaction_root, _hash_path
from devtool.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
TXID = '20260930T220314Z-21c89d352db4'


def git(repo, *args):
    result = subprocess.run(['git', *args],cwd=repo,stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,text=True,check=True)
    return result.stdout.strip()


def fixture(tmp_path, monkeypatch):
    repo = tmp_path / 'project'; repo.mkdir()
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(tmp_path/'state'))
    git(repo,'init','-q');git(repo,'config','user.email','test@example.invalid');git(repo,'config','user.name','Test')
    target=repo/'source.txt';target.write_text('old\n');git(repo,'add','.');git(repo,'commit','-qm','old')
    old=git(repo,'rev-parse','HEAD')
    target.write_text('new\n');git(repo,'add','.');git(repo,'commit','-qm','new')
    new=git(repo,'rev-parse','HEAD')
    root=transaction_root(repo)
    txdir=root/TXID; (txdir/'backup').mkdir(parents=True)
    backup=txdir/'backup/source.txt';backup.write_text('old\n')
    journal=txdir/'transaction.json'
    tx={ 'schema_version':4,'transaction_id':TXID,'path':str(journal),'repo_root':str(repo),
         'backup_dir':str(txdir/'backup'),'status':'rollback-failed','pid':0,'process_start':'',
         'git_state':{'initial_head':old},
         'affected_paths':[{'path':'source.txt','restore_state':'pending','existed':True,
                            'backup_sha256':_hash_path(backup),'verified':False}]}
    journal.write_text(json.dumps(tx))
    markers=[]
    for marker in (root/'active.json',root/'active'/f'{TXID}.json'):
        marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(json.dumps({'path':str(journal),'transaction_id':TXID,'pid':0}))
        markers.append(marker)
    return repo, journal, markers, old, new


def identity(repo):
    index=Path(git(repo,'rev-parse','--git-path','index'))
    if not index.is_absolute():index=repo/index
    return git(repo,'rev-parse','HEAD'),hashlib.sha256(index.read_bytes()).hexdigest(),git(repo,'status','--porcelain=v1','--untracked-files=all'),(repo/'source.txt').read_bytes()


def test_safe_retire_and_reversible_unretire(tmp_path,monkeypatch):
    repo,journal,markers,old,new=fixture(tmp_path,monkeypatch)
    before=identity(repo); jbytes=journal.read_bytes()
    plan=inspect_historical_transaction(repo,TXID)
    assert plan['eligible'] and plan['old_head']==old and plan['current_head']==new
    assert identity(repo)==before and all(m.exists() for m in markers)
    retired=retire_historical_transaction(repo,TXID)
    assert retired['state']=='retired-preserve-current' and not retired['rollback_verified']
    assert identity(repo)==before and journal.read_bytes()==jbytes
    assert not any(m.exists() for m in markers)
    assert all((journal.parent/'historical-retirement'/n).exists() for n in ('receipt.json','legacy.json','transaction-marker.json'))
    un=unretire_historical_transaction(repo,TXID)
    assert un['state']=='unretired' and all(m.exists() for m in markers)
    assert identity(repo)==before and journal.read_bytes()==jbytes


@pytest.mark.parametrize('mutation',[
 'dirty','wrong_history','no_advancement','live','forged_marker','missing_backup','tamper_backup',
 'changed_state','symlink_backup','symlink_marker','bad_original_sha','status_applied','wrong_repo',
])
def test_unsafe_historical_cases_fail_closed(tmp_path,monkeypatch,mutation):
    repo,journal,markers,old,new=fixture(tmp_path,monkeypatch)
    tx=json.loads(journal.read_text())
    if mutation=='dirty':(repo/'source.txt').write_text('dirty')
    if mutation=='wrong_history':tx['git_state']['initial_head']='f'*40
    if mutation=='no_advancement':tx['git_state']['initial_head']=new
    if mutation=='live':tx['pid']=os.getpid()
    if mutation=='forged_marker':markers[0].write_text(json.dumps({'path':'/tmp/forged','transaction_id':TXID}))
    if mutation=='missing_backup':(journal.parent/'backup/source.txt').unlink()
    if mutation=='tamper_backup':(journal.parent/'backup/source.txt').write_text('tampered')
    if mutation=='changed_state':tx['affected_paths'][0]['restore_state']='restored'
    if mutation=='symlink_backup':
        (journal.parent/'backup/source.txt').unlink();(journal.parent/'backup/source.txt').symlink_to(repo/'source.txt')
    if mutation=='symlink_marker':
        markers[0].unlink();markers[0].symlink_to(markers[1])
    if mutation=='bad_original_sha':tx['git_state']['initial_head']='abc'
    if mutation=='status_applied':tx['status']='applied-pending-validation'
    if mutation=='wrong_repo':tx['repo_root']='/tmp/forged'
    journal.write_text(json.dumps(tx))
    before=identity(repo); marker_before=[m.read_bytes() if m.exists() and not m.is_symlink() else None for m in markers]
    result=inspect_historical_transaction(repo,TXID)
    assert not result['eligible'] and result['blockers']
    with pytest.raises(HistoricalRetirementError):retire_historical_transaction(repo,TXID)
    assert identity(repo)==before
    assert [m.read_bytes() if m.exists() and not m.is_symlink() else None for m in markers]==marker_before
    assert not (journal.parent/'historical-retirement').exists()


def test_unretire_rejects_new_head_and_journal_tamper(tmp_path,monkeypatch):
    repo,journal,markers,old,new=fixture(tmp_path,monkeypatch)
    retire_historical_transaction(repo,TXID)
    # Git changes after retirement invalidate readmission.
    (repo/'source.txt').write_text('newer');git(repo,'add','.');git(repo,'commit','-qm','later')
    with pytest.raises(HistoricalRetirementError):unretire_historical_transaction(repo,TXID)
    assert not any(m.exists() for m in markers)


def test_cli_schema_and_explicit_consent(tmp_path,monkeypatch,capsys):
    repo,journal,markers,old,new=fixture(tmp_path,monkeypatch)
    from devtool.cli import main
    assert build_parser().parse_args(['transaction','retire',TXID,'--preserve-current','--dry-run']).transaction_command=='retire'
    assert main(['--no-hud','-r',str(repo),'transaction','retire',TXID,'--preserve-current','--json'])==0
    assert all(m.exists() for m in markers)
    assert main(['--no-hud','-r',str(repo),'transaction','retire',TXID,'--yes','--json'])==2
    assert all(m.exists() for m in markers)
    assert main(['--no-hud','-r',str(repo),'transaction','retire',TXID,'--preserve-current','--yes','--json'])==0
    assert not any(m.exists() for m in markers)


def test_pipeline_example_is_trackable_and_index_continuous(tmp_path):
    doc=(ROOT/'docs/actions-06-pipeline.md').read_text()
    assert 'git add docs/ci/actions-pipeline.json' in doc
    assert '--file docs/ci/actions-pipeline.json' in doc
    repo=tmp_path/'tracked'; repo.mkdir();git(repo,'init','-q');git(repo,'config','user.email','test@example.invalid');git(repo,'config','user.name','Test')
    (repo/'.gitignore').write_text('.devtool/\n')
    dest=repo/'docs/ci/actions-pipeline.json';dest.parent.mkdir(parents=True)
    dest.write_bytes((ROOT/'docs/templates/actions-pipeline-live-hud.json').read_bytes())
    git(repo,'add','docs/ci/actions-pipeline.json');git(repo,'commit','-qm','example')
    assert git(repo,'ls-files')=='docs/ci/actions-pipeline.json'
    index=json.loads((ROOT/'docs/DEVTOOL_IMPROVEMENT_INDEX.json').read_text())
    rows={x['id']:x for x in index['issues'] if x['id'].startswith('DT-')}
    assert set(rows)=={f'DT-{i:03}' for i in range(1,57)}
    assert index['baseline']['sha']=='6f7b1de146838dca2458eaa35d54cc8dd4445f70'
    assert not index['formal_release_qualified']
    assert all(row['current_source_assessment']['source_commit']=='b045f65eb44b9e7b7ca25f55f9a60c7902d60671' for row in rows.values())


def test_keep_artifact_existing_and_bootstrap_diagnosis():
    from devtool.bootstrap_doctor import run
    assert 'keep_artifact' in (ROOT/'devtool/core_cli_schema.json').read_text()
    assert 'not final_args.keep_artifact' in (ROOT/'devtool/cli.py').read_text()
    assert run('devtool.bootstrap_doctor')['ok']
    failed=run('devtool.__next00_missing_dependency_xyz')
    assert failed['classification']=='dependency_missing'
    assert 'ModuleNotFoundError' in failed['traceback']
    assert not failed['overlay_defect_confirmed']


def test_interrupt_partial_unretire_is_idempotently_recoverable(tmp_path,monkeypatch):
    repo,journal,markers,_,_=fixture(tmp_path,monkeypatch)
    retire_historical_transaction(repo,TXID)
    escrow=journal.parent/'historical-retirement'
    (escrow/'legacy.json').replace(markers[0])
    outcome=unretire_historical_transaction(repo,TXID)
    assert outcome['state']=='unretired' and all(m.is_file() for m in markers)


def test_actual_zip_keep_and_default_cleanup_baseline(tmp_path,monkeypatch):
    """Verify the existing KEEP contract; background LIFE requirements are external."""
    import zipfile
    from devtool.cli import main
    monkeypatch.setenv('DEVTOOL_XDG_DIR',str(tmp_path/'life'))
    outcomes=[]
    for keep in (True,False):
        repo=tmp_path/('kept' if keep else 'consumed');repo.mkdir();(repo/'.git').mkdir()
        archive=tmp_path/('kept.zip' if keep else 'consumed.zip')
        with zipfile.ZipFile(archive,'w') as zf:zf.writestr('payload.txt','hello')
        flags=['--keep-artifact'] if keep else []
        code=main(['--no-hud','-r',str(repo),'--yes','apply-overlay',*flags,str(archive)])
        outcomes.append((code,archive.exists(),(repo/'payload.txt').read_text()))
    assert outcomes[0]==(0,True,'hello')
    # Some host runtimes cannot establish an independent LIFE cleanup authority;
    # failure is honest and retains original archive, not silent input deletion.
    assert outcomes[1][0] in {0,1}
    if outcomes[1][0]==0:
        assert not outcomes[1][1]
    else:
        assert outcomes[1][1]


def test_readonly_existing_recovery_plan_includes_safe_historical_hint(tmp_path,monkeypatch):
    from devtool.overlay.recovery import overlay_recovery_plan
    repo,_,_,_,_=fixture(tmp_path,monkeypatch)
    report=overlay_recovery_plan(repo)
    assert report['read_only'] and not report['safe_to_auto_recover']
    assert report['active_transactions'][0]['historical_retirement_review']['eligible']
    assert 'transaction inspect' in report['next_step']
    assert inspect_historical_transaction(repo,TXID)['eligible']
