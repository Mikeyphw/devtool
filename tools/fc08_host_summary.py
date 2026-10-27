#!/usr/bin/env python3
"""Fail-closed hosted FC08 evidence bundle verifier (NOT a release seal)."""
from __future__ import annotations
import argparse,json,os,pathlib,sys
try:
    from tools.fc08_host_workloads import LANES, validate_receipt, identity, sha_file
except ModuleNotFoundError:
    from fc08_host_workloads import LANES, validate_receipt, identity, sha_file

def summarize(source:pathlib.Path,env:dict[str,str])->dict:
    ident=identity(env,allow_evidence_dirs=True)
    needs=json.loads(env.get('FC08_NEEDS_JSON','{}'))
    if set(needs)!={'dag','linux','windows','macos'} or any(x.get('result')!='success' for x in needs.values()):
        raise ValueError('FAILED_OR_MISSING_GITHUB_DAG_JOB')
    entries=[p for p in source.glob('*/') if p.is_dir()]
    if len(entries)!=4 or len(set(p.name for p in entries))!=4:raise ValueError('MISSING_OR_DUPLICATED_ARTIFACT')
    verified={}
    for lane in LANES:
        label=f'fc08-real-{lane}-attempt{ident["attempt"]}'
        folder=source/label
        contents=[p for p in folder.rglob('*') if p.is_file()]
        if len(contents)!=1 or contents[0].name!=lane+'.json' or contents[0].is_symlink():
            raise ValueError('FOREIGN_OR_MISSING_LANE_CONTENT:'+lane)
        raw=contents[0]
        data=json.loads(raw.read_text())
        validate_receipt(data,lane,ident)
        verified[lane]={'receipt_sha256':sha_file(raw),'checks':list(data['checks'])}
    return {'schema':'devtool.fc08.host-real-summary.v1','identity':ident,'lanes':verified,
            'status':'HOSTED_REAL_WORKLOAD_EVIDENCE_CAPTURED_PENDING_INDEPENDENT_REVIEW',
            'fc08_overall_qualified':False,'release_qualified':False,
            'open':['github-dag repeat/failed dispatch and permission denial','linux lock/ABI negative cases',
                    'windows exact signed authority','native Termux ARM64','Android signing/installed identity',
                    'real consumer adoption and rollback','FC09 final seal']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args()
    d=summarize(pathlib.Path(a.input),dict(os.environ))
    dest=pathlib.Path(a.out);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(d,sort_keys=True,indent=2)+'\n')
    print('PASS_FC08_FOUR_HOST_WORKLOAD_EVIDENCE_COLLECTED_QUALIFICATION_OPEN')
if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,KeyError,FileNotFoundError) as exc:
        print('BLOCKED_FC08_HOST_SUMMARY:'+str(exc),file=sys.stderr);sys.exit(2)
