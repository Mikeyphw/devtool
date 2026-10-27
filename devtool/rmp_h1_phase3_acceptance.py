"""Native RMP-H1 Phase 3 source-bound consumer acceptance.

Reuses the accepted Phase 2 receipt; does not certify historical authority,
remote runners, physical Android HUD, release qualification, or H1 closure.
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
    'tests/test_rmp_h1_phase3_session_and_selection.py',
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
SOURCES = (
    'devtool/validation_sessions.py',
    'devtool/test_runtime_optimization.py',
    'devtool/test_g3_gate.py',
    'devtool/rmp_h1_phase3_acceptance.py',
)
EXPECTED_SUBJECT = 'fix(security): harden H1 session loading and bind selected test cases'
PREVIOUS_SUBJECT = 'fix(security): bind H1 saved-plan reads and selected shard cases'
HEX = re.compile(r'[0-9a-f]{64}\Z')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(['git','-c',f'safe.directory={root}','-C',str(root),*args], capture_output=True)
    if proc.returncode:
        raise ValueError('git '+ ' '.join(args)+': '+proc.stderr.decode(errors='replace')[:300])
    return proc.stdout


def native_termux() -> bool:
    prefix = '/data/data/com.termux/files/usr'
    try:
        paths = (sys.executable,sys.base_prefix,os.readlink('/proc/self/exe'))
    except OSError:
        return False
    return sys.platform in ('android','linux') and all(p==prefix or p.startswith(prefix+'/') for p in paths)


def inspect_phase2(path: Path, repo: Path, parent: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Phase 2 acceptance receipt missing or symlinked')
    payload=json.loads(path.read_text(encoding='utf-8'))
    if (
        payload.get('schema_version')!=1
        or payload.get('producer')!='devtool-rmp-h1-phase2-native-consumer-acceptance'
        or payload.get('overall')!='PASS_H1_PHASE2_CONSUMER_ACCEPTANCE'
        or type(payload.get('pytest_passed')) is not int or payload['pytest_passed']<119
        or payload.get('pytest_returncode')!=0
        or payload.get('formal_h1_closed') is not False
        or payload.get('historical_campaigns_requalified') is not False
        or payload.get('release_qualified') is not False
        or payload.get('source_commit')!=parent
        or payload.get('source_tree')!=git(repo,'rev-parse',parent+'^{tree}').decode().strip()
        or not isinstance(payload.get('authority'),dict)
        or payload['authority'].get('rows')!=308
        or payload['authority'].get('authoritative_signature_verified') is not False
        or payload['authority'].get('formal_n01_closed') is not False
    ):
        raise ValueError('Phase 2 receipt status, source identity or guardrails mismatch')
    for section in ('sources','lanes'):
        entries=payload.get(section)
        if not isinstance(entries,dict) or not entries:
            raise ValueError('Phase 2 receipt missing '+section)
        for name,expected in entries.items():
            if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts or not isinstance(expected,str) or not HEX.fullmatch(expected):
                raise ValueError('Invalid Phase 2 source/lane identity')
            if sha(git(repo,'show',parent+':'+name))!=expected:
                raise ValueError('Phase 2 receipt source mismatch: '+name)
    return payload


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--prior-acceptance',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--timeout',type=int,default=180)
    a=p.parse_args(argv)
    repo=a.repo.expanduser().resolve()
    result={
       'schema_version':1,'producer':'devtool-rmp-h1-phase3-native-consumer-acceptance',
       'overall':'BLOCKED_H1_PHASE3_ACCEPTANCE','formal_h1_closed':False,
       'family_closure':{k:False for k in ('N01','N02','C04','C05','C06')},
       'historical_campaigns_requalified':False,'release_qualified':False,
       'automatic_push':False,'automatic_release':False,
       'started_at_utc':datetime.now(timezone.utc).isoformat(),
    }
    code=2
    try:
        if not native_termux():raise ValueError('Native Termux required for acceptance')
        if git(repo,'status','--porcelain=v1','--untracked-files=all').strip():raise ValueError('Clean worktree required')
        head=git(repo,'rev-parse','HEAD').decode().strip()
        parent=git(repo,'rev-parse','HEAD^').decode().strip()
        if git(repo,'log','-1','--format=%s').decode().strip()!=EXPECTED_SUBJECT:
            raise ValueError('Expected Phase 3 native commit is not HEAD')
        if git(repo,'log','-1','--format=%s',parent).decode().strip()!=PREVIOUS_SUBJECT:
            raise ValueError('Phase 2 native commit is not direct parent')
        if a.prior_acceptance:
            prior=a.prior_acceptance.expanduser()
        else:
            candidates=sorted((Path.home()/'.local/state/devtool/rmp-h1').glob('phase2-*.json'))
            if not candidates:raise ValueError('No Phase 2 acceptance receipts found')
            prior=candidates[-1]
        old=inspect_phase2(prior,repo,parent)
        result.update({
           'source_commit':head,
           'source_tree':git(repo,'rev-parse','HEAD^{tree}').decode().strip(),
           'phase2_source_commit':parent,
           'phase2_receipt_sha256':sha(prior.read_bytes()),
           'phase2_receipt_path':str(prior),
           'phase2_test_count':old['pytest_passed'],
        })
        from devtool.rmp_h1_acceptance import authority_check
        result['authority']=authority_check(repo)
        if result['authority'].get('formal_n01_closed') is not False or result['authority'].get('authoritative_signature_verified') is not False:
            raise ValueError('N01 retained authority must remain provisional')
        for section,names in (('sources',SOURCES),('lanes',LANES)):
            result[section]={}
            for name in names:
                f=repo/name
                if f.is_symlink() or not f.is_file() or sha(git(repo,'show','HEAD:'+name))!=sha(f.read_bytes()):
                    raise ValueError('Source not clean/present: '+name)
                result[section][name]=sha(f.read_bytes())
        proc=subprocess.run([sys.executable,'-m','pytest','-q',*LANES],cwd=repo,
            capture_output=True,text=True,timeout=max(1,a.timeout))
        transcript=proc.stdout+'\n'+proc.stderr
        result['pytest_output_sha256']=sha(transcript.encode())
        result['pytest_returncode']=proc.returncode
        result['pytest_summary_tail']=transcript.splitlines()[-12:]
        m=re.search(r'(\d+) passed',proc.stdout)
        result['pytest_passed']=int(m.group(1)) if m else 0
        if proc.returncode!=0 or result['pytest_passed']<130:
            raise ValueError('Phase 3 consumer regression incomplete/failed')
        result['overall']='PASS_H1_PHASE3_CONSUMER_ACCEPTANCE'
        code=0
    except (ValueError,OSError,RuntimeError,KeyError,TypeError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        result['reason']=str(exc)
    finally:
        result['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        out=a.output or Path.home()/'.local/state/devtool/rmp-h1'/('phase3-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf-8') as f:
            json.dump(result,f,sort_keys=True,indent=2);f.write('\n')
        print(json.dumps({'overall':result['overall'],'pytest_passed':result.get('pytest_passed',0),
                         'formal_h1_closed':False,'receipt':str(out),'reason':result.get('reason')},sort_keys=True))
    return code

if __name__=='__main__':raise SystemExit(main())
