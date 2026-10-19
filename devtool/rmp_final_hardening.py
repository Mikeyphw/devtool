"""Final consolidated RMP H1/H2 source-bound acceptance, not a release seal.

Runs only current targeted regression consumers. Preserves signed-authority,
remote host, Android device and separate audit obligations as OPEN.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUBJECT='fix(security): consolidate final H1 H2 consumer hardening and evidence contracts'
PREVIOUS='fix(security): harden H1 dashboard truth and direnv identity propagation'
OBLIGATIONS=('ENV-A06','ANDROID-A16','DIST-A01','DIST-A12','DIST-A13','TEST-A11')
LANES=(
 'tests/test_rmp_final_hardening.py',
 'tests/test_r01_cleanup_repository_boundary.py',
 'tests/test_r07_remediation_authority.py',
 'tests/test_r04_durable_credentials.py',
 'tests/test_rmp_h1_phase4_cross_consumers.py',
 'tests/test_rmp_h1_phase3_session_and_selection.py',
 'tests/test_remed_r04a_presentation_evidence_truth.py',
)
SOURCES=(
 'devtool/environment_composition.py','devtool/presentation_surfaces.py',
 'devtool/rmp_final_hardening.py','devtool/validation_sessions.py',
 'devtool/test_g3_gate.py','docs/RMP_H1_308_RETAINED_ANCHOR.csv',
 'docs/RMP_FINAL_HISTORICAL_PROBES.csv',
)

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def git(root:Path,*args:str)->bytes:
    result=subprocess.run(['git','-c',f'safe.directory={root}','-C',str(root),*args],capture_output=True)
    if result.returncode:raise ValueError('git '+ ' '.join(args) + ': '+result.stderr.decode(errors='replace')[:300])
    return result.stdout

def native_termux()->bool:
    base='/data/data/com.termux/files/usr'
    try: paths=(sys.executable,sys.base_prefix,os.readlink('/proc/self/exe'))
    except OSError:return False
    return sys.platform in ('linux','android') and all(x==base or x.startswith(base+'/') for x in paths)

def historical_probe_accounting(repo:Path)->dict:
    path=repo/'docs/RMP_FINAL_HISTORICAL_PROBES.csv'
    if path.is_symlink() or not path.is_file():raise ValueError('Historical probe provenance missing')
    with path.open(newline='',encoding='utf-8') as f: rows=list(csv.DictReader(f))
    ids=[row.get('id') for row in rows]
    if len(rows)!=20 or len(set(ids))!=20 or any(not i for i in ids):
        raise ValueError('Historical probe inventory not the retained 20-row set')
    mismatch=sum(row.get('classification')=='MISMATCH' for row in rows)
    if mismatch!=6:raise ValueError('Historical negative observation accounting mismatch')
    return {'original_probes':20,'historical_mismatches':mismatch,'historical_probes_requalified':False,'original_results_retained':True,'sha256':sha(path.read_bytes())}

def independent_obligations(evidence:dict|None=None)->dict:
    # No caller-controlled PASS flags can turn old unrelated substrate evidence
    # into a release or formal U01 qualification. Use original qualification
    # workflows for independent closure instead.
    return {'open_obligations':list(OBLIGATIONS),'formal_u01_closed':False,'release_qualified':False,'external_claims_not_accepted':sorted(str(x) for x in (evidence or {}))}

def verify_prior(repo:Path,source_commit:str,path:Path)->dict:
    if path.is_symlink() or not path.is_file():raise ValueError('Phase4 receipt missing or symlinked')
    record=json.loads(path.read_bytes())
    if (record.get('producer')!='devtool-rmp-h1-phase4-native-cross-consumer-acceptance'
        or record.get('overall')!='PASS_H1_PHASE4_CROSS_CONSUMER_ACCEPTANCE'
        or record.get('formal_h1_closed') is not False
        or record.get('release_qualified') is not False
        or type(record.get('pytest_passed')) is not int or record['pytest_passed']<200
        or record.get('source_commit')!=source_commit
        or record.get('source_tree')!=git(repo,'rev-parse',source_commit+'^{tree}').decode().strip()):
        raise ValueError('Phase4 acceptance identity or outcome is invalid')
    for section in ('sources','lanes'):
        entries=record.get(section)
        if not isinstance(entries,dict) or not entries:raise ValueError('Phase4 receipt missing '+section)
        for name,digest in entries.items():
            if not isinstance(name,str) or name.startswith('/') or '..' in Path(name).parts or not re.fullmatch('[0-9a-f]{64}',str(digest)):
                raise ValueError('Phase4 receipt has invalid source name or digest')
            if sha(git(repo,'show',source_commit+':'+name))!=digest:
                raise ValueError('Phase4 receipt has substituted source: '+name)
    return record

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--prior-acceptance',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--timeout',type=int,default=180)
    a=p.parse_args(argv);repo=a.repo.expanduser().resolve()
    record={'schema_version':1,'producer':'devtool-rmp-final-source-hardening',
            'overall':'BLOCKED_RMP_FINAL_SOURCE_HARDENING',
            'formal_h1_closed':False,'formal_h2_closed':False,'release_qualified':False,
            'historical_campaigns_requalified':False,'automatic_push':False,'automatic_release':False,
            'family_disposition':{
              'N01':'OPEN_INDEPENDENT_AUTHORITY', 'N02':'SOURCE_HARDENED_FORMAL_CONSUMERS_OPEN',
              'C04':'SOURCE_HARDENED_TEST_A11_OPEN','C05':'SOURCE_HARDENED_LIVE_HUD_OPEN',
              'C06':'SOURCE_HARDENED_DOWNSTREAM_EVIDENCE_OPEN',
              'Q01':'SOURCE_BOUND_CLEANUP_VALIDATION_PROVISIONAL',
              'Q02':'SELECTED_CONTRACT_TESTS_PASS_HISTORICAL_11_UNRECONCILED',
              'U01':'OPEN_NATIVE_SUBSTRATE_EVIDENCE'},
            'started_at_utc':datetime.now(timezone.utc).isoformat()}
    exit_code=2
    try:
        if not native_termux():raise ValueError('Native Termux Python required; offline cannot issue acceptance')
        if git(repo,'status','--porcelain=v1','--untracked-files=all').strip():raise ValueError('Clean worktree required')
        head=git(repo,'rev-parse','HEAD').decode().strip()
        parent=git(repo,'rev-parse','HEAD^').decode().strip()
        if git(repo,'log','-1','--format=%s').decode().strip()!=SUBJECT:raise ValueError('Final consolidation native commit not HEAD')
        if git(repo,'log','-1','--format=%s',parent).decode().strip()!=PREVIOUS:raise ValueError('Expected Phase4 parent subject')
        if a.prior_acceptance:prior=a.prior_acceptance.expanduser()
        else:
            paths=sorted((Path.home()/'.local/state/devtool/rmp-h1').glob('phase4-*.json'))
            if not paths:raise ValueError('Missing native Phase4 acceptance; run it once before final acceptance')
            prior=paths[-1]
        phase4=verify_prior(repo,parent,prior)
        record.update({'source_commit':head,'source_tree':git(repo,'rev-parse','HEAD^{tree}').decode().strip(),
                       'phase4_source_commit':parent,'phase4_receipt_sha256':sha(prior.read_bytes()),
                       'phase4_accepted_tests':phase4['pytest_passed'],
                       'historical_probes':historical_probe_accounting(repo),
                       'independent_qualifications':independent_obligations()})
        from devtool.rmp_h1_acceptance import authority_check
        authority=authority_check(repo)
        if (authority.get('rows')!=308 or authority.get('formal_n01_closed') is not False
            or authority.get('authoritative_signature_verified') is not False):
            raise ValueError('N01 inventory is not confirmed provisional / 308 rows')
        record['authority']=authority
        for section,names in [('sources',SOURCES),('lanes',LANES)]:
            record[section]={}
            for name in names:
                path=repo/name
                if path.is_symlink() or not path.is_file():raise ValueError('Expected source missing or symlinked: '+name)
                digest=sha(path.read_bytes())
                if sha(git(repo,'show','HEAD:'+name))!=digest:raise ValueError('Tracked source changed: '+name)
                record[section][name]=digest
        test=subprocess.run([sys.executable,'-m','pytest','-q',*LANES],cwd=repo,capture_output=True,text=True,timeout=max(1,a.timeout))
        output=test.stdout+'\n'+test.stderr
        record['pytest_output_sha256']=sha(output.encode())
        record['pytest_returncode']=test.returncode
        record['pytest_summary_tail']=output.splitlines()[-12:]
        match=re.search(r'(\d+) passed',test.stdout)
        record['pytest_passed']=int(match.group(1)) if match else 0
        if test.returncode!=0 or record['pytest_passed']<65:raise ValueError('Final source regression selection failed or incomplete')
        record['overall']='PASS_RMP_FINAL_SOURCE_HARDENING'
        record['ready_for_github_actions_development']=True
        exit_code=0
    except (ValueError,OSError,RuntimeError,KeyError,TypeError,json.JSONDecodeError,subprocess.TimeoutExpired) as err:
        record['reason']=str(err)
    finally:
        record['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        output=a.output or Path.home()/'.local/state/devtool/rmp-final'/('acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as fp:json.dump(record,fp,sort_keys=True,indent=2);fp.write('\n')
        print(json.dumps({'overall':record['overall'],'pytest_passed':record.get('pytest_passed',0),
                          'receipt':str(output),'formal_h1_closed':False,'formal_h2_closed':False,
                          'reason':record.get('reason')},sort_keys=True))
    return exit_code

if __name__=='__main__':raise SystemExit(main())
