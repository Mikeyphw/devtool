#!/usr/bin/env python3
"""G5 workflow executor: run one real fixture and emit a receipt only on success.

All arguments come from an allowlisted source-controlled workflow, not from a
remote API response. The resulting receipt remains untrusted until Devtool
verifies the archive digest and the exact workflow run/attempt/source tuple.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

LANGUAGES = ('python','go','rust','java')
SYSTEMS = ('ubuntu-latest','windows-latest')

def members(text: str, allowed: tuple[str,...]) -> list[str]:
    try: entries=json.loads(text)
    except (ValueError,TypeError): raise ValueError('Invalid JSON matrix dimension') from None
    if (not isinstance(entries,list) or not entries or len(entries)>len(allowed) or
        any(type(x) is not str or x not in allowed for x in entries) or len(set(entries))!=len(entries)):
        raise ValueError('Unsupported or duplicate matrix dimension')
    return [x for x in allowed if x in entries]

def fixture_paths(root: Path, language: str) -> list[Path]:
    names={'python':['test_smoke.py'], 'go':['go.mod','smoke_test.go'],
           'rust':['Cargo.toml','Cargo.lock','src/lib.rs'], 'java':['G5Smoke.java']}
    base=root/'examples'/'actions-g5'/language
    return [base/n for n in names[language]]

def run_job(root: Path, env: dict[str,str], *, output: Path) -> dict:
    language=env.get('DEVTOOL_G5_LANGUAGE','')
    platform=env.get('DEVTOOL_G5_OS','')
    languages=members(env.get('DEVTOOL_G5_LANGUAGES',''), LANGUAGES)
    systems=members(env.get('DEVTOOL_G5_SYSTEMS',''), SYSTEMS)
    if language not in languages or platform not in systems:
        raise ValueError('G5 job is not in the permitted workflow matrix')
    if not ((platform=='windows-latest') == (os.name=='nt')):
        raise ValueError('G5 matrix runner OS does not match runtime')
    sha=env.get('GITHUB_SHA','')
    import re
    if not re.fullmatch('[0-9a-fA-F]{40}',sha):
        raise ValueError('G5 requires immutable GitHub source SHA')
    repository=env.get('GITHUB_REPOSITORY','')
    if not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        raise ValueError('G5 requires GitHub repository identity')
    run_id=int(env.get('GITHUB_RUN_ID','0'))
    attempt=int(env.get('GITHUB_RUN_ATTEMPT','0'))
    if run_id<=0 or attempt<=0:raise ValueError('G5 requires actual workflow run identity')
    src=fixture_paths(root,language)
    if any(not p.is_file() or p.is_symlink() for p in src):
        raise ValueError('G5 fixture sources absent or symlinked')
    h=hashlib.sha256()
    for p in src:
        h.update(p.relative_to(root).as_posix().encode()+b'\0');h.update(p.read_bytes());h.update(b'\0')
    folder=root/'examples'/'actions-g5'/language
    if language=='python':
        cmd=[sys.executable,'-m','unittest','discover','-s',str(folder),'-p','test_*.py']
    elif language=='go':cmd=['go','test','-count=1','./...']
    elif language=='rust':cmd=['cargo','test','--locked']
    else:
        target=folder/'build'
        target.mkdir(exist_ok=True)
        # javac is a separate stage; both javac and java must succeed.
        subprocess.run(['javac','-d',str(target),str(src[0])],cwd=root,check=True,timeout=120)
        cmd=['java','-cp',str(target),'G5Smoke']
    subprocess.run(cmd,cwd=folder if language in ('go','rust') else root,check=True,timeout=180)
    receipt={'schema_version':1, 'kind':'devtool.actions.g5.cell',
             'repository':repository,'head_sha':sha.lower(), 'run_id':run_id,
             'run_attempt':attempt,'language':language,'operating_system':platform,
             'languages':languages,'operating_systems':systems,'fixture_sha256':h.hexdigest(),
             'tests_passed':1,'tests_failed':0,'status':'passed',
             'qualified_release':False,'source_attested':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return receipt

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    try:run_job(Path(__file__).resolve().parents[1],dict(os.environ),output=Path(args.out))
    except (ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError) as exc:
        print('G5 matrix job failed: '+type(exc).__name__,file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
