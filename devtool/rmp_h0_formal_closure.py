#!/usr/bin/env python3
"""Native source-bound RMP-B01/RMP-C01 bounded consumer-security closure.

Reuses the 47-test native acceptance receipt without rerunning it. Independently
exercises disposable forged G3R lane receipts and Android planned-result streams.
Never claims real Android execution, historical G3R requalification or release seal.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest import mock
import zipfile

BRANCH='feature/rmp01-security-remediation'
C02='b448f3fca5fdf87072b3c9fb162296b65229c53e'
C03='e4ffa2b8e4788c5ee92bd1737a82f0d8cfa10849'
EXPECTED_SOURCE='3f987899c44809433dcfbb01e450a2b40761a2ab'
EXPECTED_TREE='fc435546994b05b77b4558a0583dc016527c13eb'
EXPECTED_SOURCE_SHA={
 'devtool/android/release.py':'9fea58bcfd2a8db74b70c962e50e8e9ea73994e70b083d49fd827e8d586b8e94',
 'devtool/g3r_independent_verify.py':'71bca7664c02408ba3af3e3232b3303477f220b82ca4ac0f26442157d3a1d627',
}
MESSAGE='audit(security): formally close RMP-B01 and RMP-C01 consumer integrity'
CLOSE_DOC='docs/RMP_H0_B01_C01_FORMAL_CLOSURE.md'
CLOSE_MODULE='devtool/rmp_h0_formal_closure.py'
LEDGER='docs/DEVTOOL_IMPROVEMENTS.md'
TERMUX_ROOTS=('/data/data/com.termux/files/usr','/data/user/0/com.termux/files/usr')
SOURCE=('devtool/android/release.py','devtool/g3r_independent_verify.py')
EXPECTED_LANES=(
 'tests/test_r02_android_result_completeness.py',
 'tests/test_aops04_android_release_evidence_final_seal.py',
 'tests/test_rmp_b01_g3r_lane_provenance.py',
 'tests/test_g3r_native_termux_detection.py',
)


def sha(blob:bytes)->str:return sha256(blob).hexdigest()
def inside(path:str,bin_only:bool=False)->bool:
    try:
        resolved=Path(path).resolve(strict=False)
        return any(resolved.is_relative_to((Path(root)/('bin' if bin_only else '')).resolve(strict=False)) for root in TERMUX_ROOTS)
    except (ValueError,TypeError,OSError):return False

def native_probe(*,platform=None,executable=None,base_prefix=None,proc_executable=None)->dict:
    platform=sys.platform if platform is None else platform
    executable=sys.executable if executable is None else executable
    base_prefix=sys.base_prefix if base_prefix is None else base_prefix
    if proc_executable is None:
        try: proc_executable=os.readlink('/proc/self/exe')
        except OSError:proc_executable=''
    accepted=(platform.startswith(('android','linux')) and inside(base_prefix)
              and (inside(executable,bin_only=True) or inside(proc_executable,bin_only=True)))
    return {'native_termux':accepted,'platform':platform,'python_executable':executable,
            'python_base_prefix':base_prefix,'proc_self_exe':proc_executable,
            'prefix_environment':os.environ.get('PREFIX','')}

def git(repo:Path,*args:str)->str:
    r=subprocess.run(['git','-c',f'safe.directory={repo}','-C',str(repo),*args],capture_output=True,timeout=45)
    if r.returncode:raise RuntimeError('Git authority failed: '+' '.join(args)+': '+r.stderr.decode(errors='replace')[-240:])
    return r.stdout.decode(errors='replace').strip()

def committed_bytes(repo:Path,name:str)->bytes:
    r=subprocess.run(['git','-c',f'safe.directory={repo}','-C',str(repo),'show','HEAD:'+name],capture_output=True,timeout=30)
    if r.returncode:raise RuntimeError('Uncommitted or missing source: '+name)
    return r.stdout

def committed_sha(repo:Path,name:str)->str:
    path=repo/name
    if path.is_symlink() or not path.is_file():raise RuntimeError('Source missing or symlinked: '+name)
    data=path.read_bytes()
    if committed_bytes(repo,name)!=data:raise RuntimeError('Source differs from HEAD: '+name)
    return sha(data)

def pin(repo:Path, *, native:bool)->tuple[str,str]:
    if git(repo,'branch','--show-current')!=BRANCH:raise RuntimeError('Expected security branch '+BRANCH)
    if git(repo,'status','--porcelain=v1','--untracked-files=no'):
        raise RuntimeError('Tracked worktree must be clean; commit/preserve changes first')
    for name in (CLOSE_DOC,CLOSE_MODULE):
        if (repo/name).exists() or (repo/name).is_symlink():raise RuntimeError('Closure artifact already exists: '+name)
    for name,marker in (
       ('docs/RMP_C02_FORMAL_CLOSURE.md',b'PASS_C02_FORMALLY_CLOSED'),
       ('docs/RMP_C03_FORMAL_CLOSURE.md',b'PASS_C03_FORMALLY_CLOSED'),
       ('docs/RMP_H0_B01_C01_CONSUMER_BINDING.md',b'RMP H0')):
        if marker not in (repo/name).read_bytes():raise RuntimeError('Missing prerequisite record: '+name)
        committed_sha(repo,name)
    if native:
        if (git(repo,'rev-parse','HEAD'),git(repo,'rev-parse','HEAD^{tree}')) != (EXPECTED_SOURCE,EXPECTED_TREE):
            raise RuntimeError('Source commit/tree differs from accepted B01/C01 native receipt; do not override the pin')
        for path,want in EXPECTED_SOURCE_SHA.items():
            if committed_sha(repo,path)!=want:raise RuntimeError('B01/C01 qualified source digest changed: '+path)
        for ancestor in (C02,C03):
            test=subprocess.run(['git','-c',f'safe.directory={repo}','-C',str(repo),'merge-base','--is-ancestor',ancestor,'HEAD'],capture_output=True)
            if test.returncode:raise RuntimeError('Missing formal closure ancestor: '+ancestor)
    return git(repo,'rev-parse','HEAD'),git(repo,'rev-parse','HEAD^{tree}')

def validate_acceptance(repo:Path, r:dict, head:str,tree:str)->dict:
    from devtool.rmp_h0_acceptance import LANES
    if not isinstance(r,dict) or r.get('schema_version')!=1 or r.get('producer')!='devtool-rmp-b01-c01-local-consumer-acceptance':
        raise RuntimeError('Not a B01/C01 acceptance receipt')
    for key,val in {'overall':'PASS_H0_B01_C01_LOCAL_ACCEPTANCE','formal_b01_closed':False,
                    'formal_c01_closed':False,'historical_campaigns_requalified':False,
                    'release_qualified':False,'automatic_push':False,'pytest_returncode':0}.items():
        if type(r.get(key)) is not type(val) or r[key]!=val:raise RuntimeError('Acceptance field mismatch: '+key)
    if r.get('source_commit')!=head or r.get('source_tree')!=tree:
        raise RuntimeError('Acceptance source commit/tree does not match current HEAD')
    if tuple(LANES)!=EXPECTED_LANES:raise RuntimeError('B01/C01 acceptance inventory changed')
    if set(r.get('lanes',{}))!=set(EXPECTED_LANES):raise RuntimeError('Acceptance lane inventory mismatch')
    source={name:committed_sha(repo,name) for name in SOURCE}
    lanes={name:committed_sha(repo,name) for name in EXPECTED_LANES}
    if r.get('source_sha256')!=source or r.get('lanes')!=lanes:
        raise RuntimeError('Acceptance source or lane SHA-256 mismatch')
    if r.get('runtime',{}).get('platform') not in ('android','linux') or not inside(r.get('runtime',{}).get('python',''),bin_only=True):
        raise RuntimeError('Acceptance was not recorded by native Termux Python')
    if not re.fullmatch('[0-9a-f]{64}',str(r.get('pytest_output_sha256',''))):
        raise RuntimeError('Malformed pytest output digest')
    summary='\n'.join(r.get('pytest_summary_tail',[]))
    hit=re.search(r'\b(\d+) passed in [0-9.]+s\b',summary)
    if not hit or int(hit.group(1))!=47:raise RuntimeError('Expected 47/47 passing acceptance cases')
    return {'pytest_passed':47,'source_sha256':source,'lane_sha256':lanes,
            'pytest_output_sha256':r['pytest_output_sha256'],'timestamp_utc':r.get('timestamp_utc')}

def find_acceptance(repo:Path,provided:Path|None,head:str,tree:str):
    if provided:
        choices=[provided.expanduser()]
    else:
        base=Path.home()/'.local/state/devtool/rmp-h0'
        choices=([base/'acceptance.json'] if (base/'acceptance.json').is_file() else [])
        if base.is_dir() and not base.is_symlink():
            choices+=sorted(base.glob('acceptance-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]
    for path in choices:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size>128000:continue
            data=json.loads(path.read_bytes())
            checked=validate_acceptance(repo,data,head,tree)
            return path,data,checked
        except (OSError,ValueError,RuntimeError,TypeError,KeyError):
            if provided:raise RuntimeError('Explicit acceptance invalid or source-mismatched: '+str(path))
    raise RuntimeError('No current-source 47-test acceptance receipt in ~/.local/state/devtool/rmp-h0')

def expect_denial(label:str, action, match:str)->dict:
    try:action()
    except (ValueError,RuntimeError) as exc:
        if match not in str(exc):raise RuntimeError('Wrong rejection reason for '+label+': '+str(exc))
        return {'case':label,'outcome':'PASS_REJECTED','reason':match}
    raise RuntimeError('Forged evidence accepted: '+label)

def b01_disposable()->list[dict]:
    from devtool.g3r_independent_verify import expected_lane_log_name, validate_lane_log
    from devtool.g3r_evidence_gate import REQUIRED_LANES
    if len(REQUIRED_LANES)!=13:raise RuntimeError('G3R required lane count changed')
    names=[expected_lane_log_name(k) for k in REQUIRED_LANES]
    if len(set(names))!=13:raise RuntimeError('G3R lane log names duplicate')
    cases=[]
    with tempfile.TemporaryDirectory(prefix='rmp-b01-probe-') as folder:
        root=Path(folder);runs=root/'runs';runs.mkdir()
        key='tests/test_android_instrumentation.py'; name=expected_lane_log_name(key)
        correct=b'synthetic evidence only: 1 passed\n'
        (runs/name).write_bytes(correct)
        receipt={'status':'PASS','exit_code':0,'output_file':name,'output_sha256':sha(correct)}
        seen=set();validate_lane_log(root,key,receipt,seen)
        if seen!={name}:raise RuntimeError('Valid lane did not establish uniqueness')
        cases.append({'case':'positive-bound-lane','outcome':'PASS_BOUND','log_sha256':sha(correct)})
        cases.append(expect_denial('duplicate-reference',lambda:validate_lane_log(root,key,receipt,seen),'duplicate lane log'))
        cases.append(expect_denial('borrowed-from-another-lane',lambda:validate_lane_log(root,'tests/test_dist_x04_operational_adoption.py',receipt,set()),'lane log identity mismatch'))
        cases.append(expect_denial('path-substitution',lambda:validate_lane_log(root,key,{**receipt,'output_file':'../wrong.log'},set()),'lane log identity mismatch'))
        cases.append(expect_denial('digest-substitution',lambda:validate_lane_log(root,key,{**receipt,'output_sha256':'0'*64},set()),'SHA mismatch'))
        cases.append(expect_denial('bool-exit',lambda:validate_lane_log(root,key,{**receipt,'exit_code':True},set()),'invalid exit code'))
        (runs/name).write_bytes(b'')
        cases.append(expect_denial('empty-success',lambda:validate_lane_log(root,key,{**receipt,'output_sha256':sha(b'')},set()),'empty execution log'))
        cases.append(expect_denial('missing-success-log',lambda:validate_lane_log(root,key,{'status':'PASS','exit_code':0},set()),'passed lane missing log'))
    return cases

def c01_disposable()->list[dict]:
    import hashlib
    from devtool.android.release import AndroidReleaseOrchestrator
    from devtool.models import ProjectConfig,CommandSpec,OperationResult
    from devtool.project_runners import AndroidGradleRunner
    cases=[]
    with tempfile.TemporaryDirectory(prefix='rmp-c01-probe-') as folder:
        root=Path(folder);apk=root/'disposable-release.apk';apk.write_bytes(b'nondeployable-test-fixture')
        digest=hashlib.sha256(apk.read_bytes()).hexdigest()
        config=ProjectConfig(repo_root=root, project_root=str(root),kind='android',runner='android',
            selected_target='app',variant='release',build_target=':app',runner_options={'android':{
              'release':{'variant':'release','device_tests':'required','install_smoke':False,
                         'upgrade_smoke':False,'cold_start_smoke':False,'restart_smoke':False,
                         'verify_signature':False,'verify_certificate_identity':False},
              'signing':{'release':{'mode':'project','policy':'optional'}}}})
        def specs(self,project,action):
            return ([CommandSpec('test-a',['fixture','a']),CommandSpec('test-b',['fixture','b'])]
                    if action=='test' else [CommandSpec(action,['fixture',action])])
        context=mock.Mock();context.as_dict.return_value={'tools':{}}
        scenarios=(('valid-ordered',('test-a','test-b'),False,'passed'),
                   ('duplicate-command',('test-a','test-a'),False,'failed'),
                   ('reordered-command',('test-b','test-a'),False,'failed'),
                   ('substituted-command',('test-a','foreign'),False,'failed'),
                   ('skipped-required',('test-a','test-b'),True,'failed'),
                   ('missing-result',('test-a',),False,'failed'))
        with mock.patch.object(AndroidGradleRunner,'build_specs',specs), mock.patch(
            'devtool.android.release.resolve_android_execution_context',return_value=context):
            for name,result_names,skipped,expected in scenarios:
                def execute(_config,planned):
                    if planned[0].name=='test-a':
                        return [OperationResult(n,['fixture'],0,0.01,'synthetic test',[],skipped=skipped)
                                for n in result_names],''
                    result=OperationResult(planned[0].name,planned[0].argv,0,0.01,'synthetic test',[],metadata={})
                    if planned[0].name=='package':
                        result.metadata['artifacts']={'items':[{'valid':True,'type':'apk',
                            'variant':'release','path':str(apk),'sha256':digest}]}
                    return [result],''
                recorded=AndroidReleaseOrchestrator(config,executor=execute).run(continue_on_error=True)
                gate=next((g for g in recorded.evidence.get('gates',[]) if g.get('name')=='unit-tests'),None)
                if not gate or gate.get('status')!=expected:
                    raise RuntimeError('Android planned-result consumer verdict wrong for '+name+': '+str(gate))
                if expected=='failed' and recorded.evidence.get('verdict')=='passed':
                    raise RuntimeError('Android forged command created a false-green release: '+name)
                cases.append({'case':name,'outcome':'PASS_EXPECTED_'+expected.upper(),
                              'unit_test_status':gate['status'],'release_verdict':recorded.evidence.get('verdict')})
    return cases

def write_new(path:Path,data:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())

def create_overlay(repo:Path,head:str,tree:str,out:Path,add:dict[str,bytes],modify:dict[str,bytes])->dict:
    all_files={**add,**modify};rows=[];base={};post={}
    for path,blob in sorted(all_files.items()):
        row={'path':path,'action':'modify' if path in modify else 'add','sha256':sha(blob),'size':len(blob)}
        if path in modify:
            old=(repo/path).read_bytes();row['source_sha256']=sha(old);base[path]=sha(old)
        else:
            if (repo/path).exists() or (repo/path).is_symlink():raise RuntimeError('Duplicate closure file '+path)
            base[path]=None
        rows.append(row);post[path]=sha(blob)
    validation=b'''#!/bin/sh\nset -eu\npython3 -c 'from pathlib import Path; s=Path("docs/RMP_H0_B01_C01_FORMAL_CLOSURE.md").read_text(); assert "PASS_H0_B01_C01_FORMALLY_CLOSED" in s'\n'''
    manifest={'schema_version':2,'type':'overlay','name':'RMP-H0 B01/C01 bounded formal consumer-security closure',
         'base_contract_mode':'touched-files','base_commit':head,'base_tree':tree,
         'touched_paths':sorted(post),'source_sha256':base,'expected_post_sha256':post,
         'inventory':{'format':1,'entries':rows},
         'apply':{'atomic':True,'conflict_policy':'fail','path_policy':'repository-relative',
                  'requires_clean_worktree':False,'commit':{'enabled':True,'strategy':'single','message':MESSAGE}},
         'validation':{'required':True,'rollback_on_failure':True,'restore':False,'build':False,'test':False,
                       'script':{'path':'.devtool-artifact/validate.sh','runner':'sh','timeout_seconds':90,'sha256':sha(validation)}}}
    from devtool.artifacts import artifact_commit_policy
    if artifact_commit_policy(manifest)!=(True,MESSAGE,'single'):raise RuntimeError('Native apply.commit schema rejected')
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for path,blob in sorted(all_files.items()):z.writestr(path,blob)
        z.writestr('.devtool-artifact.json',json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        z.writestr('.devtool-artifact/validate.sh',validation)
    return manifest

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--acceptance',type=Path)
    p.add_argument('--evidence-dir',type=Path);p.add_argument('--apply',action='store_true')
    p.add_argument('--allow-non-termux-test',action='store_true',help='offline fixture-only: never authorizes closure')
    args=p.parse_args(argv)
    diag=native_probe();native=diag['native_termux'] and not args.allow_non_termux_test
    if not native and not args.allow_non_termux_test:
        print(json.dumps({'overall':'BLOCKED_H0_FORMAL_CLOSURE','formal_b01_closed':False,'formal_c01_closed':False,
              'reason':'Native Termux Python required','environment_diagnostic':diag,'release_qualified':False},sort_keys=True))
        return 2
    try:
        repo=args.repo.expanduser().resolve(strict=True)
        head,tree=pin(repo,native=native)
        sys.path.insert(0,str(repo))
        receipt_path,receipt,checked=find_acceptance(repo,args.acceptance,head,tree)
        b01=b01_disposable();c01=c01_disposable()
        if len(b01)!=8 or len(c01)!=6:raise RuntimeError('Closure probe coverage mismatch')
        now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ')
        folder=(args.evidence_dir or Path.home()/'.local/state/devtool/rmp-h0').expanduser().resolve(strict=False)
        acceptance_sha=sha(receipt_path.read_bytes())
        verdict='PASS_H0_B01_C01_FORMAL_GATE' if native else 'PASS_H0_B01_C01_OFFLINE_REHEARSAL_ONLY'
        evidence={'schema_version':1,'producer':'devtool-rmp-h0-b01-c01-formal-consumer-gate',
                  'overall':verdict,'formal_b01_closed':False,'formal_c01_closed':False,'release_qualified':False,
                  'qualified_source_commit':head,'qualified_source_tree':tree,'native_termux':native,
                  'environment':diag,'acceptance':{'receipt':str(receipt_path),'sha256':acceptance_sha,**checked},
                  'b01_disposable_lane_provenance':b01,'c01_disposable_android_result_completeness':c01,
                  'residual_limits':['Consumer source/identity consistency only: no cryptographic G3R worker attestation',
                                     'No real Android device execution or historical G3R regeneration',
                                     'No full release qualification or cross-campaign requalification'],
                  'guardrails':{'automatic_push':False,'automatic_release':False,
                     'automatic_requalification_of_historical_campaigns':False,'other_rmp_findings_closed':False,
                     'disposable_artifacts_only':True},'finished_at_utc':now.isoformat()}
        path=folder/f'rmp-h0-formal-{stamp}-{head[:8]}.json'
        write_new(path,(json.dumps(evidence,sort_keys=True,indent=2)+'\n').encode())
        print('PASS_H0_FORMAL_EVIDENCE:',path,flush=True)
        if not args.apply:
            print(json.dumps({'overall':verdict,'formal_b01_closed':False,'formal_c01_closed':False},sort_keys=True))
            return 0
        if not native:
            print('OFFLINE_REHEARSAL_ONLY: no closure commit outside Termux',flush=True)
            return 0
        if (head,tree)!=pin(repo,native=True):raise RuntimeError('Source changed during formal gate')
        report=f'''# RMP-B01 and RMP-C01 — bounded formal consumer-integrity closure\n\n- **Decision:** `PASS_H0_B01_C01_FORMALLY_CLOSED`\n- **Qualified source commit:** `{head}`\n- **Qualified source tree:** `{tree}`\n- **Native 47-test acceptance receipt SHA-256:** `{acceptance_sha}`\n- **Formal disposable proof SHA-256:** `{sha(path.read_bytes())}`\n- **Formal evidence path:** `{path}`\n- **Independent disposable checks:** 8 G3R lane identity/negative/positive checks, 6 Android ordered-planned-result checks\n- **Source-bound native automatic commit:** required and verified by installer\n- **Release qualification:** false; historical campaigns not requalified; other RMP findings unchanged\n\n**Scope and residual risk:** B01 closes only the G3R per-lane log identity, uniqueness, SHA-256 and non-empty/exit-code consumer obligation; it does not certify cryptographic attestation, genuine remote execution or regeneration of historical G3R evidence. C01 closes only Android release planned-command-to-result ordering, duplicate/substitution/missing/skip rejection in source-bound local consumers; it does not certify actual device execution or Android release readiness. No full release qualification. C02/C03 formal closure stays valid; six independent audit obligations stay open.\n'''.encode()
        old=(repo/LEDGER).read_bytes();committed_sha(repo,LEDGER)
        append=f'''\n## RMP-H0-B01-C01 formal native bounded consumer closure ({now.date().isoformat()})\n\n- **Status:** B01 G3R lane-consumer identity and C01 Android release result-completeness **CLOSED within bounded consumer-security scope** at `{head}`.\n- Native 47/47 acceptance reused without retest; receipt SHA-256 `{acceptance_sha}`.\n- Independent disposable 8-case G3R and 6-case Android negative/positive proof SHA-256 `{sha(path.read_bytes())}`.\n- Original 308 findings otherwise preserved; no historical native G3R, Android, CI24, SSH or RS13 requalification. Six independent audit obligations remain open.\n- This does not establish cryptographic peer attestation, genuine Android execution, or release seal.\n'''.encode()
        additions={CLOSE_DOC:report,CLOSE_MODULE:Path(__file__).read_bytes()}
        modified={LEDGER:old.rstrip(b'\n')+b'\n'+append}
        with tempfile.TemporaryDirectory(prefix='devtool-rmp-h0-closure-') as tmp:
            archive=Path(tmp)/'RMP_H0_CLOSURE.overlay.zip'
            manifest=create_overlay(repo,head,tree,archive,additions,modified)
            env=os.environ.copy()
            for key in ('DEVTOOL_RUN_ID','DEVTOOL_TRANSACTION_REPO_ROOT',
                        'DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT','DEVTOOL_TRANSACTION_ISOLATION',
                        'DEVTOOL_VALIDATION_STATE_REPO_ROOT'):
                env.pop(key,None)
            env['PYTHONPATH']=str(repo)
            child=subprocess.run([sys.executable,'-m','devtool','-r',str(repo),'--yes',
                      'apply-overlay',str(archive),'--keep-artifact'],cwd=repo,env=env,
                      stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
            if child.returncode:raise RuntimeError('Native closure overlay failed: '+child.stdout.decode(errors='replace')[-1200:])
            newhead=git(repo,'rev-parse','HEAD')
            changed=git(repo,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
            if (newhead==head or git(repo,'log','-1','--format=%s')!=MESSAGE
                or sorted(changed)!=sorted(manifest['touched_paths'])):
                raise RuntimeError('Auto-commit missing or wrong changed-file scope')
            for file,blob in {**additions,**modified}.items():
                if (repo/file).read_bytes()!=blob or committed_bytes(repo,file)!=blob:
                    raise RuntimeError('Installed/committed content differs: '+file)
            print('PASS_H0_FORMAL_COMMIT:',newhead,flush=True)
            print('PASS_H0_EXACT_COMMIT_SCOPE:',len(changed),'files',flush=True)
        final={'schema_version':1,'producer':'devtool-rmp-h0-b01-c01-closure-commit',
           'overall':'PASS_H0_B01_C01_FORMALLY_CLOSED','formal_b01_closed':True,'formal_c01_closed':True,
           'release_qualified':False,'closure_commit':newhead,'qualified_source_commit':head,
           'source_evidence_sha256':sha(path.read_bytes()),'evidence_path':str(path),
           'guardrails':evidence['guardrails']}
        final_file=folder/f'rmp-h0-closed-{stamp}-{newhead[:8]}.json'
        write_new(final_file,(json.dumps(final,sort_keys=True,indent=2)+'\n').encode())
        print('PASS_H0_FINAL_RECEIPT:',final_file,flush=True)
        print(json.dumps(final,sort_keys=True))
        return 0
    except (RuntimeError,TypeError,ValueError,OSError,KeyError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        print(json.dumps({'overall':'BLOCKED_H0_FORMAL_CLOSURE','formal_b01_closed':False,
              'formal_c01_closed':False,'reason':str(exc),'release_qualified':False},sort_keys=True))
        return 2

if __name__=='__main__':raise SystemExit(main())
