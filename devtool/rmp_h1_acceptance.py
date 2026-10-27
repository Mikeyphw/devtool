"""Bounded H1 source-bound acceptance: retained N01 map + N02/C04/C05/C06.

This does not close risk findings, launch jobs, or qualify a release.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

LANES = (
    'tests/test_rmp_h1_consolidated_boundaries.py',
    'tests/test_rmp_h1_authority_anchor.py',
    'tests/test_r04_durable_credentials.py',
    'tests/test_r09_environment_identity.py',
    'tests/test_r05_test_shard_completeness.py',
    'tests/test_r06_exo_status_truth.py',
    'tests/test_exo08_terminal_truth_migration.py',
    'tests/test_test_x03_runtime_optimization.py',
    'tests/test_v104_cross_campaign_integration.py',
    'tests/test_env_x02_language_environment_convergence.py',
    'tests/test_pres_x01_presentation_surfaces.py',
    'tests/test_pres_r01_execution_presentation.py',
    'tests/test_test_x04_convergence.py',
    'tests/test_remed_r04a_presentation_evidence_truth.py',
    'tests/test_r07_remediation_authority.py',
)
SOURCES = (
    'devtool/validation_sessions.py',
    'devtool/environment_composition.py',
    'devtool/test_runtime_optimization.py',
    'devtool/presentation_surfaces.py',
)
ANCHOR = 'docs/RMP_H1_308_RETAINED_ANCHOR.csv'
ANCHOR_SHA256 = '21f5071a1dd2410c1a32692b65c078f028b9703d273af0b53771d4f32106d719'
MAPPING_SHA256 = '8857e51e91cc91b31d733c48d081ef661b31360bbc8e590e490c305769b05704'

def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def authority_check(repo: Path) -> dict:
    anchor = (repo / ANCHOR).read_bytes()
    if sha(anchor) != ANCHOR_SHA256:
        raise ValueError('N01 retained authority anchor was modified')
    data = list(csv.DictReader(anchor.decode().splitlines()))
    if len(data) != 308:
        raise ValueError('N01 anchor does not have 308 rows')
    source_rows = sorted((str(x['historical_id']), int(x['overlay_number']), str(x['historical_overlay_title'])) for x in data)
    current = json.loads((repo/'FINAL_REMEDIATION_COVERAGE.json').read_text())
    rows = sorted((str(x['id']),int(x['overlay']),str(x['overlay_title'])) for x in current.get('finding_assignments',()))
    digest = sha(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode())
    if rows != source_rows or digest != MAPPING_SHA256:
        raise ValueError('N01 retained historical roadmap no longer matches live assignments')
    counts = current.get('overlay_counts')
    expected = {str(i):sum(row[1]==i for row in source_rows) for i in range(1,15)}
    if not isinstance(counts,dict) or {str(k):int(v) for k,v in counts.items()} != expected:
        raise ValueError('N01 owner overlay counts differ from retained assignments')
    return {'rows':len(rows), 'overlay_counts':expected, 'anchor_sha256':ANCHOR_SHA256,
            'assignment_sha256':digest,'source':'RMP00 2026-10-17 retained reconstruction (not independently signed)',
            'authoritative_signature_verified':False,'formal_n01_closed':False}

def main(argv=None) -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path.cwd())
    ap.add_argument('--output',type=Path)
    ap.add_argument('--timeout',type=int,default=180)
    a=ap.parse_args(argv)
    repo=a.repo.expanduser().resolve()
    now=datetime.now(timezone.utc).isoformat()
    data = {'schema_version':1,'producer':'devtool-rmp-h1-consolidated-local-acceptance',
            'overall':'BLOCKED_H1_ACCEPTANCE','started_at_utc':now,
            'formal_h1_closed':False,'release_qualified':False,'historical_campaigns_requalified':False,
            'automatic_push':False,'lanes':{},'sources':{}}
    result_code=2
    try:
        if not (repo/'.git').exists():raise ValueError('Git repository required')
        if subprocess.run(['git','-C',str(repo),'status','--porcelain'],capture_output=True,text=True,check=True).stdout.strip():
            raise ValueError('Worktree must be clean')
        data['source_commit']=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        data['source_tree']=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD^{tree}'],text=True).strip()
        data['authority']=authority_check(repo)
        for name in (*SOURCES,*LANES):
            fp=repo/name
            if fp.is_symlink() or not fp.is_file():raise ValueError('Missing or symlinked source: '+name)
            h=sha(fp.read_bytes())
            if name in SOURCES:data['sources'][name]=h
            else:data['lanes'][name]=h
        try:
            proc=subprocess.run([sys.executable,'-m','pytest','-q',*LANES],cwd=repo,capture_output=True,
                                text=True,timeout=max(1,a.timeout))
        except subprocess.TimeoutExpired:
            raise ValueError('Focused pytest timed out')
        data['pytest_returncode']=proc.returncode
        data['pytest_output_sha256']=sha((proc.stdout+'\n'+proc.stderr).encode())
        data['pytest_summary_tail']=(proc.stdout+'\n'+proc.stderr).splitlines()[-12:]
        import re
        m=re.search(r'(\d+) passed',proc.stdout)
        data['pytest_passed']=int(m.group(1)) if m else 0
        if proc.returncode != 0 or data['pytest_passed'] < 150:
            raise ValueError('Focused tests incomplete or failed')
        data['overall']='PASS_H1_CONSOLIDATED_LOCAL_ACCEPTANCE'
        result_code=0
    except (ValueError,OSError,subprocess.CalledProcessError) as e:
        data['reason']=str(e)
    finally:
        data['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        output=a.output or Path.home()/'.local/state/devtool/rmp-h1'/('acceptance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf8') as fd:
            json.dump(data,fd,indent=2,sort_keys=True);fd.write('\n')
        print(json.dumps({'overall':data['overall'],'pytest_passed':data.get('pytest_passed',0),'formal_h1_closed':False,'receipt':str(output),'reason':data.get('reason')},sort_keys=True))
    return result_code

if __name__=='__main__':raise SystemExit(main())
