"""RMP-H1 phase 2: native source-bound consumer regression evidence.

This is not a formal closure certificate, and does not requalify old campaigns.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

LANES = (
    'tests/test_rmp_h1_phase2_resume_and_membership.py',
    'tests/test_rmp_h1_consolidated_boundaries.py',
    'tests/test_rmp_h1_authority_anchor.py',
    'tests/test_r04_durable_credentials.py',
    'tests/test_r05_test_shard_completeness.py',
    'tests/test_test_x03_runtime_optimization.py',
    'tests/test_r09_environment_identity.py',
    'tests/test_exo08_terminal_truth_migration.py',
    'tests/test_r06_exo_status_truth.py',
    'tests/test_pres_x01_presentation_surfaces.py',
)
SOURCE_FILES = (
    'devtool/validation_sessions.py',
    'devtool/test_runtime_optimization.py',
    'devtool/environment_composition.py',
    'devtool/presentation_surfaces.py',
    'devtool/rmp_h1_phase2_acceptance.py',
    'tests/test_rmp_h1_phase2_resume_and_membership.py',
)
EXPECTED_COMMIT_MESSAGE = 'fix(security): bind H1 saved-plan reads and selected shard cases'


def _hash(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(['git','-c',f'safe.directory={repo}','-C',str(repo),*args],capture_output=True)
    if proc.returncode:
        raise ValueError('git '+ ' '.join(args)+': '+proc.stderr.decode(errors='replace')[:250])
    return proc.stdout


def _native_termux() -> bool:
    # Do not reject Android Python 3.14, and do not trust PREFIX alone.
    prefix='/data/data/com.termux/files/usr'
    paths=(str(sys.executable),str(sys.base_prefix))
    try:
        paths+=(os.readlink('/proc/self/exe'),)
    except OSError:
        return False
    return sys.platform in {'android','linux'} and all(
        path == prefix or path.startswith(prefix+'/') for path in paths
    )


def _read_prior_receipt(path: Path, repo: Path, parent: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('H1 acceptance receipt missing or symlinked')
    row=json.loads(path.read_text(encoding='utf-8'))
    if (
        row.get('schema_version')!=1
        or row.get('producer')!='devtool-rmp-h1-consolidated-local-acceptance'
        or row.get('overall')!='PASS_H1_CONSOLIDATED_LOCAL_ACCEPTANCE'
        or type(row.get('pytest_passed')) is not int or row['pytest_passed']<212
        or row.get('formal_h1_closed') is not False
        or row.get('historical_campaigns_requalified') is not False
        or row.get('release_qualified') is not False
        or row.get('source_commit')!=parent
        or row.get('source_tree')!=_git(repo,'rev-parse',parent+'^{tree}').decode().strip()
    ):
        raise ValueError('H1 acceptance status, source, tree or guardrails mismatch')
    for collection in ('lanes','sources'):
        entries=row.get(collection)
        if not isinstance(entries,dict) or not entries:
            raise ValueError('H1 acceptance missing '+collection)
        for name,expected in entries.items():
            if not re.fullmatch('[0-9a-f]{64}',str(expected)) or name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('H1 acceptance path or hash malformed')
            if _hash(_git(repo,'show',parent+':'+name))!=expected:
                raise ValueError('H1 acceptance mismatches prior commit: '+name)
    authority=row.get('authority')
    if not isinstance(authority,dict) or authority.get('rows')!=308 or authority.get('formal_n01_closed') is not False or authority.get('authoritative_signature_verified') is not False:
        raise ValueError('Historical inventory authority status mismatch')
    return row


def main(argv: list[str] | None = None) -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--prior-acceptance',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--timeout',type=int,default=180)
    a=ap.parse_args(argv)
    root=a.repo.expanduser().resolve()
    data={
        'schema_version':1,'producer':'devtool-rmp-h1-phase2-native-consumer-acceptance',
        'overall':'BLOCKED_H1_PHASE2_ACCEPTANCE','formal_h1_closed':False,
        'family_closure':{family:False for family in ('N01','N02','C04','C05','C06')},
        'release_qualified':False,'automatic_push':False,'automatic_release':False,
        'historical_campaigns_requalified':False,'started_at_utc':datetime.now(timezone.utc).isoformat(),
    }
    code=2
    try:
        if not _native_termux(): raise ValueError('Native Termux interpreter required')
        if _git(root,'status','--porcelain=v1','--untracked-files=all').strip():
            raise ValueError('Git worktree must be clean')
        commit=_git(root,'rev-parse','HEAD').decode().strip()
        parent=_git(root,'rev-parse','HEAD^').decode().strip()
        subject=_git(root,'log','-1','--format=%s').decode().strip()
        if subject!=EXPECTED_COMMIT_MESSAGE:
            raise ValueError('Expected H1 phase 2 native auto-commit is not HEAD')
        data['source_commit']=commit
        data['source_tree']=_git(root,'rev-parse','HEAD^{tree}').decode().strip()
        data['prior_h1_source_commit']=parent
        if a.prior_acceptance:
            prior=a.prior_acceptance.expanduser()
        else:
            paths=sorted((Path.home()/'.local/state/devtool/rmp-h1').glob('acceptance-*.json'))
            if not paths: raise ValueError('No native H1 phase1 acceptance receipt found')
            prior=paths[-1]
        old=_read_prior_receipt(prior,root,parent)
        data['prior_receipt_sha256']=_hash(prior.read_bytes())
        data['prior_receipt_path']=str(prior)
        data['prior_h1_test_count']=old['pytest_passed']
        data['sources']={name:_hash((root/name).read_bytes()) for name in SOURCE_FILES}
        data['lanes']={name:_hash((root/name).read_bytes()) for name in LANES}
        from devtool.rmp_h1_acceptance import authority_check
        data['authority']=authority_check(root)
        if data['authority']['formal_n01_closed'] is not False:
            raise ValueError('N01 retained inventory must remain provisional')
        proc=subprocess.run([sys.executable,'-m','pytest','-q',*LANES],cwd=root,
                            capture_output=True,text=True,timeout=max(1,a.timeout))
        data['pytest_output_sha256']=_hash((proc.stdout+'\n'+proc.stderr).encode())
        data['pytest_returncode']=proc.returncode
        data['pytest_summary_tail']=(proc.stdout+'\n'+proc.stderr).splitlines()[-12:]
        match=re.search(r'(\d+) passed',proc.stdout)
        data['pytest_passed']=int(match.group(1)) if match else 0
        if proc.returncode!=0 or data['pytest_passed']<100:
            raise ValueError('H1 phase2 focused suite incomplete or failed')
        data['overall']='PASS_H1_PHASE2_CONSUMER_ACCEPTANCE'
        code=0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        data['reason']=str(exc)
    finally:
        data['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        out=a.output or Path.home()/'.local/state/devtool/rmp-h1'/('phase2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf-8') as fh:
            json.dump(data,fh,sort_keys=True,indent=2)
            fh.write('\n')
        print(json.dumps({'overall':data['overall'],'pytest_passed':data.get('pytest_passed',0),'formal_h1_closed':False,'receipt':str(out),'reason':data.get('reason')},sort_keys=True))
    return code


if __name__=='__main__':
    raise SystemExit(main())
