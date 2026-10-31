#!/usr/bin/env python3
"""R30: opt-in, synthetic cleanup authority replay on real shared storage.

This is NOT a real artifact apply, cleanup authorization for user files, or
release qualification. Only a brand-new O_EXCL owned fixture can be removed.
The worker gets normal durable RUN-V3 fixture authority in a private git repo.
On refusal, preserve the fixture for investigation rather than force unlink.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

SCHEMA = 'devtool.core-r30.synthetic-fresh-worker-replay.v1'
REASON = 'after successful apply'
FIXTURE_PREFIX = '.devtool-core-r30-owned-'


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True, timeout=20).strip()


def _new_owned_file(directory: Path, *, payload: bytes) -> Path:
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('SCRATCH_DIRECTORY_REQUIRED')
    name = FIXTURE_PREFIX + uuid.uuid4().hex + '.zip'
    path = directory / name
    dfd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=dfd)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    return path


def _authority(private_repo: Path, path: Path) -> bytes:
    from devtool.artifact_lifecycle import ArtifactExecutionRecord
    from devtool.artifact_cleanup_worker import cleanup_source_snapshot
    from devtool.core_runtime import private_run_root
    from devtool.run_store import AttachmentDescriptor, RunCatalog

    run_id = uuid.uuid4().hex
    record = ArtifactExecutionRecord.create(
        artifact_id='artifact:synthetic-r30', plan_id='plan:synthetic-r30',
        run_id=run_id, qualification_receipt_id='qualification:synthetic-r30',
        execution_status='succeeded')
    store = private_run_root(private_repo)
    record_path = store / run_id / 'artifacts' / (record.record_id.replace(':', '-') + '.json')
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record.as_dict(), sort_keys=True))
    catalog = RunCatalog.open(store)
    catalog.add_run({'run_id':run_id, 'artifact_id':record.artifact_id,
                     'artifact_plan_id':record.plan_id,
                     'artifact_qualification_receipt_id':record.qualification_receipt_id,
                     'status':'succeeded'})
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id,path=record_path,kind='artifact-execution-record',media_type='application/json'))
    snapshot = cleanup_source_snapshot(path)
    evidence_path = record_path.with_name(record_path.stem + '-sources.json')
    evidence_path.write_text(json.dumps({
        'schema_version':1,'repository':str(private_repo.resolve()),'run_id':run_id,
        'artifact_execution_record_id':record.record_id,'sources':[snapshot],
    }, sort_keys=True))
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id,path=evidence_path,kind='artifact-apply-source-provenance',media_type='application/json'))
    catalog.save()
    return json.dumps({
        'schema_version':1,'repository':str(private_repo.resolve()),'reason':REASON,
        'run_id':'','apply_completed':True,'sources':[snapshot],
        'execution_records':[{'run_id':run_id,'record_id':record.record_id,
                              'sources':[str(path)]}],
    },sort_keys=True).encode()


def _run_worker(source_repo: Path, private_repo: Path, target: Path, authority: bytes | None):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(source_repo) + os.pathsep + env.get('PYTHONPATH','')
    argv = [sys.executable, '-m','devtool.artifact_cleanup_worker',
            '--repo-root',str(private_repo),'--reason',REASON]
    with tempfile.TemporaryFile('w+b') as fp:
        if authority is not None:
            fp.write(authority);fp.seek(0)
            argv += ['--authority-fd',str(fp.fileno())]
        argv += ['--',str(target)]
        cp = subprocess.run(argv,env=env,cwd=source_repo,capture_output=True,
                            timeout=60,pass_fds=(fp.fileno(),) if authority is not None else ())
    if len(cp.stdout) > 65536 or len(cp.stderr) > 65536:
        raise ValueError('UNBOUNDED_WORKER_OUTPUT')
    try:
        doc=json.loads(cp.stdout)
    except (ValueError,UnicodeError):
        doc={}
    if not isinstance(doc,dict):doc={}
    metadata=doc.get('metadata') if isinstance(doc.get('metadata'),dict) else {}
    accepted_categories={'APPLY_AUTHORITY_REFUSED','FILESYSTEM_ERROR','UNEXPECTED_WORKER_FAILURE',
                         'SOURCE_AUTHORITY_REFUSED'}
    return {'exit_code':cp.returncode,'worker_returncode':doc.get('returncode'),
            'failure_kind':doc.get('failure_kind') if doc.get('failure_kind') in
                 {'artifact_cleanup_failure','artifact_cleanup_authority_missing',''} else 'UNVERIFIED',
            'error_category':metadata.get('error_category') if metadata.get('error_category') in
                 accepted_categories else None,
            'cleanup_status':metadata.get('cleanup_status') if metadata.get('cleanup_status') in
                 {'protected','applied','no-op','failed'} else None,
            'source_failure_categories':[item.get('category') for item in metadata.get('source_failure_diagnostics',[])
                       if isinstance(item,dict) and item.get('category') in
                       {'identity-drift','authority-refusal','filesystem-error'}][:5],
            'stdout_sha256':hashlib.sha256(cp.stdout).hexdigest(),
            'stderr_sha256':hashlib.sha256(cp.stderr).hexdigest()}


def replay(source_repo:Path, scratch_dir:Path, *, authorized:bool=False):
    if not authorized:raise ValueError('EXPLICIT_SYNTHETIC_REPLAY_APPROVAL_REQUIRED')
    source_repo=source_repo.resolve(strict=True)
    sha=_git('rev-parse','HEAD',cwd=source_repo)
    tree=_git('rev-parse','HEAD^{tree}',cwd=source_repo)
    if not re.fullmatch('[0-9a-f]{40}',sha) or not re.fullmatch('[0-9a-f]{40}',tree):
        raise ValueError('SOURCE_IDENTITY_INVALID')
    if _git('status','--porcelain','--untracked-files=all',cwd=source_repo):
        raise ValueError('SOURCE_WORKTREE_DIRTY')
    outcomes={}
    residues=[]
    with tempfile.TemporaryDirectory(prefix='core-r30-private-repo-') as td:
        private_repo=Path(td)/'repo'
        private_repo.mkdir()
        subprocess.run(['git','init','-q',str(private_repo)],check=True,capture_output=True,timeout=20)
        for scenario in ('positive','drift','no-authority'):
            target=_new_owned_file(scratch_dir,payload=b'R30 SYNTHETIC ZIP SOURCE - NOT USER ARTIFACT\n')
            authorized_bytes=_authority(private_repo,target) if scenario!='no-authority' else None
            if scenario=='drift':
                # This intentionally invalidates authority without substitution
                # of an existing file and must force the worker to refuse unlink.
                fd=os.open(target,os.O_WRONLY | getattr(os,'O_NOFOLLOW',0))
                try:
                    st=os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                        raise ValueError('OWNED_FIXTURE_REPLACED')
                    os.ftruncate(fd,0)
                    os.write(fd,b'R30 REPLACEMENT - MUST SURVIVE\n')
                    os.fsync(fd)
                finally:os.close(fd)
            result=_run_worker(source_repo,private_repo,target,authorized_bytes)
            remains=target.exists() or target.is_symlink()
            if scenario=='positive':
                success=result['exit_code']==0 and result['worker_returncode']==0 and not remains
            else:
                success=result['exit_code']!=0 and remains
            # Only on this caller-approved synthetic fixture, issue a *new*
            # source-bound durable authority for the current bytes. This is a
            # separate cleanup, never an automatic retry of stale authority.
            recovered=False
            if remains and success and scenario!='positive':
                recovery=_run_worker(source_repo,private_repo,target,
                                     _authority(private_repo,target))
                recovered=recovery['exit_code']==0 and not target.exists()
                success=success and recovered
            if target.exists() or target.is_symlink():
                # An inconclusive identity or authority result NEVER becomes
                # an uncontrolled os.unlink; leave it for manual review.
                residues.append(target.name)
            outcomes[scenario]={'passed':success,'source_preserved_on_refusal':remains,
                                'fixture_removed_after_new_authority':recovered,
                                'fixture_sha256':hashlib.sha256(target.name.encode()).hexdigest(),
                                'worker':result}
    return {'schema':SCHEMA,'source':{'sha':sha,'tree':tree},
            'host':'synthetic-worker-replay','scenarios':outcomes,
            'status':'PASS' if all(x['passed'] for x in outcomes.values()) else 'BLOCKED',
            'residue_names':residues,'actual_user_artifact_touched':False,
            'real_apply_lifecycle_qualified':False,'original_cleanup_root_cause_qualified':False,
            'native_build_qualified':False,'fc08_overall_qualified':False,'release_qualified':False}


def _atomic_json(out:Path,proof:dict):
    out=out.absolute()
    if out.is_symlink() or (out.exists() and not out.is_file()) or out.parent.is_symlink():
        raise ValueError('UNSAFE_OUTPUT')
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.core-r30-receipt-',dir=out.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(proof,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,out)
    finally:
        if os.path.lexists(name):os.unlink(name)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--scratch-dir',type=Path,required=True)
    p.add_argument('--yes-create-owned-fixtures',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        proof=replay(a.repo,a.scratch_dir,authorized=a.yes_create_owned_fixtures)
        _atomic_json(a.out,proof)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        print('BLOCKED_CORE_R30_SYNTHETIC_REPLAY_UNQUALIFIED',file=sys.stderr)
        return 2
    print('PASS_CORE_R30_SYNTHETIC_FRESH_WORKER_NON_RELEASE' if proof['status']=='PASS'
          else 'BLOCKED_CORE_R30_SYNTHETIC_WORKER_UNQUALIFIED')
    return 0 if proof['status']=='PASS' else 2

if __name__=='__main__':raise SystemExit(main())
