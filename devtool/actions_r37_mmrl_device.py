"""R37: authenticated OfficialDebug APK pair -> canonical AOPS03 device runner.

A small reviewed consumer adapter. GitHub I/O, archive authentication, APK
inspection, registry, ADB/device selection and instrumentation remain Devtool-owned.
Never invokes Gradle locally. Release qualification is outside this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

from .actions_r35_verified_import import _deny, _json_member, _requires, _verified_or_download, HEX40, HEX64
from .actions_artifacts import artifact_list, check_zip
from .actions_android import android_collect
from .artifact_registry import register_paths, verify_record, registry_path
from .github_actions import GitHubActionsClient, GitHubActionsError, resolve_token, _slug

REPO='Mikeyphw/MMRL'
WORKFLOW='mmrl-r37-paired-device-apks.yml'
DEVTOOL_SHA='246f168ebdef6d9b25c194268c810c877d099fa3'
APP_PACKAGE='com.mikeyphw.mmrl.debug'
TEST_PACKAGE=APP_PACKAGE+'.test'
RUNNER='androidx.test.runner.AndroidJUnitRunner'
APP_MEMBER='payload/app/build/outputs/apk/official/debug/MMRL-R36-OfficialDebug.apk'
TEST_MEMBER='payload/MMRL-R37-OfficialDebug-androidTest.apk'
ROLES=('app','test','evidence')
ROLE_NAME={r:f'mmrl-r37-paired-{r}' for r in ROLES}
MAX_APK=160*1024*1024
MAX_RECEIPT=64*1024


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def _check_keys(plan: dict) -> None:
    if (plan.get('repository')!=REPO or plan.get('workflow')!=WORKFLOW
        or not HEX40.fullmatch(str(plan.get('source_sha','')))
        or type(plan.get('run_id')) is not int or plan['run_id']<1
        or type(plan.get('attempt')) is not int or plan['attempt']<1
        or not HEX64.fullmatch(str(plan.get('app_sha256','')))
        or not HEX64.fullmatch(str(plan.get('test_sha256','')))
        or not HEX64.fullmatch(str(plan.get('signer_sha256','')))
        or plan.get('app_package')!=APP_PACKAGE or plan.get('test_package')!=TEST_PACKAGE):
        _deny('R37 requires explicit exact source/run/attempt/APK/signer pins')


def select(client:GitHubActionsClient, repository:str, run_id:int, attempt:int, source_sha:str,
           app_sha256:str, test_sha256:str, signer_sha256:str) -> dict:
    plan={'schema':'devtool.r37.pair-import-plan.v1', 'repository':_slug(repository),
          'workflow':WORKFLOW,'run_id':run_id,'attempt':attempt,'source_sha':source_sha,
          'app_sha256':app_sha256,'test_sha256':test_sha256,'signer_sha256':signer_sha256,
          'app_package':APP_PACKAGE,'test_package':TEST_PACKAGE}
    _check_keys(plan)
    inventory=artifact_list(client,REPO,run_id)
    remote=inventory['remote_run']
    workflow=client.workflow(REPO,WORKFLOW)
    if (remote.get('completed') is not True or remote.get('conclusion')!='success'
        or remote.get('event')!='workflow_dispatch' or remote.get('workflow_id')!=workflow.get('id')
        or remote.get('run_attempt')!=attempt or remote.get('head_sha')!=source_sha):
        _deny('R37 remote workflow/run/attempt/source identity mismatch')
    selected={}
    for role in ROLES:
        name=f'{ROLE_NAME[role]}-attempt{attempt}'
        rows=[x for x in inventory['artifacts'] if x.get('name')==name]
        if len(rows)!=1:
            _deny('Missing/ambiguous R37 artifact role: '+role)
        x=rows[0]
        digest=x.get('digest','')
        if (x.get('run_id')!=run_id or x.get('run_attempt')!=attempt
            or x.get('head_sha')!=source_sha or x.get('expired') is not False
            or not isinstance(digest,str) or not digest.startswith('sha256:')
            or not HEX64.fullmatch(digest[7:])):
            _deny('R37 artifact role has untrusted inventory metadata: '+role)
        selected[role]=x
    if len({x['id'] for x in selected.values()})!=3:
        _deny('R37 artifact IDs are not distinct')
    plan.update({'workflow_id':workflow['id'],'artifacts':selected,
                 'remote_actions_artifact_authenticated':False,
                 'device_qualified':False,'release_qualified':False})
    return plan


def _members(archive:zipfile.ZipFile, expected:set[str], label:str) -> None:
    if set(archive.namelist())!=expected or len(archive.infolist())!=len(expected):
        _deny('R37 unexpected/duplicate archive entry: '+label)
    for item in archive.infolist():
        if item.is_dir() or item.flag_bits & 1 or item.file_size<1:
            _deny('R37 unsafe ZIP entry in '+label)
        if item.file_size>(MAX_APK if item.filename.endswith('.apk') else MAX_RECEIPT):
            _deny('R37 oversized ZIP entry in '+label)


def _member_hash(archive:zipfile.ZipFile, member:str) -> tuple[str,int]:
    h=hashlib.sha256();size=0
    with archive.open(member) as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            size+=len(block)
            if size>MAX_APK:_deny('R37 APK member exceeds limit')
            h.update(block)
    return h.hexdigest(),size


def validate_chain(plan:dict, archives:dict[str,Path]) -> dict:
    _check_keys(plan)
    if set(archives)!=set(ROLES):_deny('R37 requires exactly three source-bound archives')
    for path in archives.values():check_zip(path)
    with (zipfile.ZipFile(archives['app']) as app,
          zipfile.ZipFile(archives['test']) as test,
          zipfile.ZipFile(archives['evidence']) as evidence):
        _members(app,{'receipt.json',APP_MEMBER},'app')
        _members(test,{TEST_MEMBER},'test')
        _members(evidence,{'pair.json','r36-plan.json','r36-verification.json','r36-native-receipt.json'},'evidence')
        pair=_json_member(evidence,'pair.json')
        r36plan=_json_member(evidence,'r36-plan.json')
        r36check=_json_member(evidence,'r36-verification.json')
        native=_json_member(evidence,'r36-native-receipt.json')
        native_other=_json_member(app,'receipt.json')
        if native!=native_other:_deny('R37 native DAG receipts diverge between archives')
        app_hash,app_size=_member_hash(app,APP_MEMBER)
        test_hash,test_size=_member_hash(test,TEST_MEMBER)
        if app_hash!=plan['app_sha256'] or test_hash!=plan['test_sha256']:
            _deny('R37 APK bytes do not match pinned SHA-256 values')
        for obj,label in ((pair,'pair'),(r36plan,'R36 plan'),(r36check,'R36 verification')):
            if obj.get('source_sha')!=plan['source_sha']:
                _deny('R37 '+label+' source SHA differs from authenticated GitHub run')
        _requires(pair,{'schema':'mmrl.r37.hosted-pair.v1',
            'status':'HOSTED_PAIR_VERIFIED_NON_DEVICE','repository':REPO,
            'workflow':WORKFLOW,'source_sha':plan['source_sha'],
            'run_id':plan['run_id'],'attempt':plan['attempt'],
            'devtool_sha':DEVTOOL_SHA,'app_test_compatible':True,
            'instrumentation_runner':TEST_PACKAGE+'/'+RUNNER,
            'target_package':APP_PACKAGE,'test_execution':'NOT_RUN',
            'remote_actions_artifact_authenticated':False,
            'device_qualified':False,'consumer_qualified':False,
            'release_qualified':False}, 'R37 pair receipt')
        _requires(pair.get('app',{}),{'package':APP_PACKAGE,'sha256':app_hash,
            'size':app_size,'signer_sha256':plan['signer_sha256'],
            'signature_verified':True},'R37 application receipt')
        _requires(pair.get('test',{}),{'package':TEST_PACKAGE,'sha256':test_hash,
            'size':test_size,'signer_sha256':plan['signer_sha256'],
            'signature_verified':True},'R37 instrumentation receipt')
        _requires(r36plan,{'schema':'mmrl.r36.hosted-plan.v1',
            'devtool_sha':DEVTOOL_SHA,'source_sha':plan['source_sha'],
            'device_qualified':False,'release_qualified':False},'R36 plan')
        _requires(r36check,{'schema':'mmrl.r36.hosted-verification.v1',
            'status':'VERIFIED_DEBUG_NON_RELEASE','source_sha':plan['source_sha'],
            'run_id':plan['run_id'],'attempt':plan['attempt'],
            'apk_sha256':app_hash,'signer_sha256':plan['signer_sha256'],
            'signature_independently_verified':True,'release_qualified':False,
            'device_qualified':False},'R36 verification')
        _requires(native,{'kind':'devtool.actions.node-handoff',
            'schema_version':2,'source_commit':plan['source_sha'],
            'run_id':str(plan['run_id']),'attempt':plan['attempt'],
            'authenticated':False,'release_qualified':False},'R36 native handoff')
        files=native.get('files')
        if (not isinstance(files,list) or len(files)!=1 or not isinstance(files[0],dict)
            or any(files[0].get(k)!=v for k,v in {'path':APP_MEMBER.removeprefix('payload/'),
                'sha256':app_hash,'size':app_size}.items())):
            _deny('R37 native R36 file identity differs from APK')
        graph=native.get('graph_sha256')
        if (not HEX64.fullmatch(str(graph or '')) or
            any(x.get('graph_sha256')!=graph for x in (pair,r36plan,r36check)) or
            pair.get('r36_native_workflow_sha256')!=native.get('workflow_sha256')):
            _deny('R37 graph and workflow receipts disagree')
    return {'app_sha256':app_hash,'app_size':app_size,'test_sha256':test_hash,
            'test_size':test_size,'app_package':APP_PACKAGE,'test_package':TEST_PACKAGE,
            'signer_sha256':plan['signer_sha256'],'graph_sha256':graph,
            'runner':TEST_PACKAGE+'/'+RUNNER,
            'app_version_code':pair['app'].get('version_code'),
            'test_version_code':pair['test'].get('version_code'),
            'release_qualified':False,'device_qualified':False}


def _local_path(root:Path,plan:dict)->Path:
    _check_keys(plan)
    root=root.resolve()
    base=root/'.devtool'/'artifacts'/'r37-imports'
    for path in (root/'.devtool',root/'.devtool'/'artifacts',base):
        if path.is_symlink():_deny('Unsafe symlinked Devtool artifact boundary')
    return base/f"mmrl-run{plan['run_id']}-attempt{plan['attempt']}-{plan['source_sha'][:12]}.json"


def _apk_targets(root:Path,plan:dict) -> dict[str,Path]:
    dest=_local_path(root,plan)
    return {'app':dest.with_name(dest.stem+'-app.apk'),
            'test':dest.with_name(dest.stem+'-test.apk')}


def inspect_local(root:Path,plan:dict) -> dict:
    path=_local_path(root,plan)
    if not path.exists():
        if path.is_symlink():_deny('Symlinked R37 local receipt')
        return {'status':'NOT_IMPORTED','remote_actions_artifact_authenticated':False,
                'device_qualified':False,'release_qualified':False}
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_RECEIPT:
        _deny('Unsafe R37 local receipt')
    try:receipt=json.loads(path.read_text())
    except (OSError,ValueError,UnicodeError):_deny('R37 receipt malformed')
    _requires(receipt,{'schema':'devtool.r37.pair-import.v1',
        'status':'IMPORTED_VERIFIED_DEBUG_PAIR','repository':REPO,
        'workflow':WORKFLOW,'source_sha':plan['source_sha'],
        'run_id':plan['run_id'],'attempt':plan['attempt'],
        'app_sha256':plan['app_sha256'],'test_sha256':plan['test_sha256'],
        'signer_sha256':plan['signer_sha256'],
        'remote_actions_artifact_authenticated':True,
        'apk_signatures_independently_verified':True,
        'device_qualified':False,'release_qualified':False},'R37 stored receipt')
    if 'artifacts' in plan and receipt.get('artifacts')!={role:{
        'id':plan['artifacts'][role]['id'],'digest':plan['artifacts'][role]['digest']}
        for role in ROLES}:
        _deny('R37 local artifact IDs/archive digests differ from GitHub')
    reg=registry_path(root)
    if not reg.is_file() or reg.is_symlink():_deny('R37 canonical registry missing')
    try:db=json.loads(reg.read_text())
    except (OSError,ValueError,UnicodeError):_deny('R37 canonical registry is malformed')
    items=db.get('items') if isinstance(db,dict) else None
    if not isinstance(items,list):_deny('R37 canonical registry entries unavailable')
    targets=_apk_targets(root,plan)
    for role in ('app','test'):
        digest=plan[role+'_sha256']
        record=next((x for x in items if isinstance(x,dict) and x.get('id')==receipt['registry_ids'][role]),None)
        if not record or record.get('sha256')!=digest:_deny('R37 '+role+' registry identity mismatch')
        metadata=record.get('metadata')
        if (not isinstance(metadata,dict) or any(metadata.get(k)!=v for k,v in {
            'remote_repository':REPO,'remote_run_id':plan['run_id'],
            'remote_run_attempt':plan['attempt'],'remote_head_sha':plan['source_sha'],
            'remote_actions_artifact_authenticated':True,'pair_role':role,
            'signer_sha256':plan['signer_sha256']}.items())):
            _deny('R37 registry provenance mismatch for '+role)
        raw=root.resolve()/str(record.get('path',''))
        if (raw.is_symlink() or raw.resolve()!=targets[role].resolve()
            or not targets[role].is_file() or targets[role].is_symlink()
            or _sha(targets[role])!=digest):
            _deny('R37 imported bytes have disappeared or changed: '+role)
    return {**receipt,'local_bytes_reverified':True}


def _extract_member(archive:Path,member:str,target:Path,expected_sha:str) -> None:
    h=hashlib.sha256();size=0
    with zipfile.ZipFile(archive) as z,z.open(member) as inp,target.open('xb') as out:
        os.fchmod(out.fileno(),0o600)
        for block in iter(lambda:inp.read(1024*1024),b''):
            size+=len(block)
            if size>MAX_APK:_deny('R37 extracted APK is oversized')
            h.update(block);out.write(block)
        out.flush();os.fsync(out.fileno())
    if h.hexdigest()!=expected_sha:_deny('R37 extracted bytes differ from verified archive')


def import_pair(client:GitHubActionsClient,root:Path,plan:dict)->dict:
    old=inspect_local(root,plan)
    if old['status']!='NOT_IMPORTED':return old
    verified={role:_verified_or_download(client,plan,role,root) for role in ROLES}
    archives={role:Path(row['file']) for role,row in verified.items()}
    chain=validate_chain(plan,archives)
    for role,member,package in (('app',APP_MEMBER,APP_PACKAGE),('test',TEST_MEMBER,TEST_PACKAGE)):
        inspected=android_collect(client,REPO,root,run_id=plan['run_id'],
            artifact_id=plan['artifacts'][role]['id'],fmt='apk',variant='debug',
            expected_package=package,expected_cert=plan['signer_sha256'],
            member=member,materialize=False,download=False,
            workflow=WORKFLOW,expected_source_sha=plan['source_sha'],
            expected_run_attempt=plan['attempt'])['android']
        if (inspected.get('sha256')!=chain[role+'_sha256']
            or inspected.get('size')!=chain[role+'_size']
            or inspected.get('package')!=package
            or inspected.get('signer_sha256')!=plan['signer_sha256']
            or inspected.get('signature_verified') is not True
            or inspected.get('manifest_verified') is not True):
            _deny('R37 independent ACTIONS-05 APK inspection mismatch: '+role)
    # Independently check instrumentation declaration, not merely its package ID.
    from .actions_android import _tool, _run
    with zipfile.ZipFile(archives['test']) as z,tempfile.TemporaryDirectory(prefix='r37-manifest-') as t:
        apk=Path(t)/'instrumentation.apk'
        with z.open(TEST_MEMBER) as inp,apk.open('xb') as out:shutil.copyfileobj(inp,out)
        manifest=_run([_tool('aapt'),'dump','xmltree',str(apk),'AndroidManifest.xml'],purpose='R37 instrumentation target verification')
        if (not re.search(r'E:\s+instrumentation\b',manifest)
            or not re.search(r'android:targetPackage[^\n]*="'+re.escape(APP_PACKAGE)+'"',manifest)
            or not re.search(r'android:name[^\n]*="'+re.escape(RUNNER)+'"',manifest)):
            _deny('R37 test APK does not declare the reviewed runner/target')
    dest=_local_path(root,plan); targets=_apk_targets(root,plan)
    dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any(p.exists() or p.is_symlink() for p in (dest,*targets.values())):
        _deny('R37 interrupted/occupied import requires explicit reconciliation')
    for role,member in (('app',APP_MEMBER),('test',TEST_MEMBER)):
        _extract_member(archives[role],member,targets[role],plan[role+'_sha256'])
    registry_ids={}
    for role in ('app','test'):
        metadata={'producer':'github-actions-r37-r35-verified-import',
            'state':'android-debug-instrumentation-pair-unqualified',
            'remote_repository':REPO,'remote_run_id':plan['run_id'],
            'remote_run_attempt':plan['attempt'],'remote_head_sha':plan['source_sha'],
            'remote_workflow_id':plan['workflow_id'],'workflow':WORKFLOW,
            'remote_artifact_ids':{r:plan['artifacts'][r]['id'] for r in ROLES},
            'remote_archive_digests':{r:plan['artifacts'][r]['digest'] for r in ROLES},
            'pair_role':role,'pair_hashes':{r:plan[r+'_sha256'] for r in ('app','test')},
            'package':APP_PACKAGE if role=='app' else TEST_PACKAGE,
            'variant':'officialDebug','signer_sha256':plan['signer_sha256'],
            'remote_actions_artifact_authenticated':True,
            'signature_verified':True,'manifest_verified':True,
            'device_qualified':False,'release_qualified':False}
        created=register_paths(root,[targets[role]],metadata=metadata)
        if len(created)!=1 or created[0]['sha256']!=plan[role+'_sha256']:
            _deny('R37 canonical registry rejected '+role)
        chk=verify_record(root,created[0]['id'])
        if chk.get('valid') is not True or chk.get('current_sha256')!=plan[role+'_sha256']:
            _deny('R37 canonical registry failed verification for '+role)
        registry_ids[role]=created[0]['id']
    report={**chain,'schema':'devtool.r37.pair-import.v1',
        'status':'IMPORTED_VERIFIED_DEBUG_PAIR','repository':REPO,
        'workflow':WORKFLOW,'workflow_id':plan['workflow_id'],
        'source_sha':plan['source_sha'],'run_id':plan['run_id'],
        'attempt':plan['attempt'],'registry_ids':registry_ids,
        'artifacts':{r:{'id':plan['artifacts'][r]['id'],
                        'digest':plan['artifacts'][r]['digest']} for r in ROLES},
        'remote_actions_artifact_authenticated':True,
        'apk_signatures_independently_verified':True,
        'test_execution':'NOT_RUN','consumer_qualified':False,
        'device_qualified':False,'release_qualified':False}
    with dest.open('x',encoding='utf-8') as out:
        os.fchmod(out.fileno(),0o600)
        json.dump(report,out,sort_keys=True,indent=2)
        out.write('\n');out.flush();os.fsync(out.fileno())
    return report


def _stage_path(root:Path,role:str) -> Path:
    return (root/'app/build/outputs/apk/official/debug/MMRL-R37-Device-OfficialDebug.apk'
            if role=='app' else
            root/'app/build/outputs/apk/androidTest/official/debug/MMRL-R37-Device-OfficialDebug-androidTest.apk')


def _device_acceptance(root:Path,plan:dict,*,allow_replace:bool=False) -> dict:
    """No own ADB runner: invoke the existing AOPS02/AOPS03 authority with --skip-build."""
    # A source-pinned local checkout is required for the project-owned AOPS03
    # configuration/test selection. Downloaded APKs alone are not source identity.
    try:
        head=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,
                            text=True,check=False,timeout=15)
        dirty=subprocess.run(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],
                             capture_output=True,text=True,check=False,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        _deny('R37 cannot authenticate local MMRL source checkout')
    if (head.returncode!=0 or head.stdout.strip()!=plan['source_sha']
        or dirty.returncode!=0 or dirty.stdout.strip()):
        _deny('R37 device execution requires clean source pinned to imported APK SHA')
    receipt=inspect_local(root,plan)
    if receipt['status']!='IMPORTED_VERIFIED_DEBUG_PAIR' or not receipt.get('local_bytes_reverified'):
        _deny('R37 requires a fully reverified imported pair before touching a device')
    for r in ('app','test'):
        if not _apk_targets(root,plan)[r].is_file():_deny('R37 imported pair disappeared')
    from .config import load_project_config
    from .android.device_ops import AndroidDeviceOperations
    cfg=load_project_config(str(root),None,target_name='mmrl_android',
                            variant_name='officialDebug',allow_parent_config=True)
    ops=AndroidDeviceOperations.from_config(cfg)
    device=ops.select_device(policy='exactly-one',enrich=True)
    preexisting={}
    for pkg in (APP_PACKAGE,TEST_PACKAGE):
        result=ops.shell(['pm','path',pkg],serial=device.serial,policy='serial',check=False)
        if result.returncode!=0:
            _deny('R37 could not establish pre-existing package state: '+pkg)
        preexisting[pkg]='package:' in result.stdout
    if any(preexisting.values()) and not allow_replace:
        _deny('R37 existing app/test package requires explicit --allow-replace (no changes made)')
    paths={role:_stage_path(root,role) for role in ('app','test')}
    if any(p.exists() or p.is_symlink() for p in paths.values()):
        _deny('R37 staged APK path occupied; refusing to choose unreviewed build outputs')
    # Existing AOPS03 _find_apks selects by 'officialdebug' in APK path.
    # Refuse all older candidates across modules, not just our staging directory.
    for apk in root.glob('**/build/outputs/apk/**/*.apk'):
        if 'officialdebug' in apk.as_posix().lower():
            _deny('R37 unrelated OfficialDebug APK exists; refuse ambiguous AOPS03 selection')
    session_base=_local_path(root,plan).with_name('device-'+_local_path(root,plan).stem)
    session_base.mkdir(mode=0o700,exist_ok=True,parents=True)
    session=None
    for number in range(1,10000):
        candidate=session_base/f'attempt-{number:04d}'
        try:
            candidate.mkdir(mode=0o700,exist_ok=False)
        except FileExistsError:
            continue
        session=candidate
        break
    if session is None:_deny('R37 immutable device evidence attempt space exhausted')
    evidence_path=session/'instrumentation.json'
    command=[sys.executable,'-m','devtool.android_instrumentation',str(root),
        '--target','mmrl_android','--variant','officialDebug',
        '--skip-build', '--class','com.dergoogler.mmrl.acceptance.OfficialDebugAcceptanceTest',
        '--report',str(evidence_path)]
    result=None
    # Source-bound, non-destructive logcat marker: never clear global device logs.
    boundary='R37_BEGIN_'+secrets.token_hex(16)
    marker=ops.shell(['log','-p','i','-t','MMRL_R37_ACCEPTANCE',boundary],
                     serial=device.serial,policy='serial',check=False)
    before_log=ops.logcat(serial=device.serial,policy='serial',lines=300,clear=False)
    try:
        for role in ('app','test'):
            path=paths[role]
            path.parent.mkdir(parents=True,exist_ok=True)
            with _apk_targets(root,plan)[role].open('rb') as inp,path.open('xb') as out:
                shutil.copyfileobj(inp,out)
            if _sha(path)!=plan[role+'_sha256']:_deny('R37 staged APK corrupted')
        try:
            proc=subprocess.run(command,cwd=root,text=True,capture_output=True,
                                timeout=1950,check=False)
            child_returncode=proc.returncode
            child_stdout,child_stderr=proc.stdout,proc.stderr
        except (OSError,subprocess.TimeoutExpired) as exc:
            child_returncode=124
            child_stdout=''
            child_stderr='AOPS03 invocation unavailable or timed out: '+type(exc).__name__
        if evidence_path.is_file():
            raw=json.loads(evidence_path.read_text())
        else:
            raw={'status':'failed','failure_kind':'missing-instrumentation-receipt',
                 'executed':0,'failed':0,'returncode':child_returncode}
        # The AOPS03 receipt is authoritative; never infer success from process exit alone.
        passed=(child_returncode==0 and raw.get('status')=='passed'
                and type(raw.get('executed')) is int and raw['executed']==3
                and raw.get('passed')==3 and raw.get('skipped')==0
                and raw.get('failed')==0 and raw.get('returncode')==0
                and raw.get('device_serial')==device.serial)
        did_execute=type(raw.get('executed')) is int and raw['executed']>0
        after_log=ops.logcat(serial=device.serial,policy='serial',lines=4000,clear=False)
        before_text=before_log.stdout if before_log.ok else ''
        after_text=after_log.stdout if after_log.ok else ''
        # Scan only log lines after this run's own marker. A missing marker
        # (e.g. log rotation or unavailable logcat) remains NOT_RUN, not PASS.
        marker_pos=after_text.find(boundary) if marker.ok and after_log.ok else -1
        if marker_pos>=0:
            new_logs=after_text[marker_pos+len(boundary):]
            log_status='PASS'
        else:
            new_logs=''
            log_status='NOT_RUN'
        crash_found=(bool(re.search(r'ANR in\s+'+re.escape(APP_PACKAGE)+r'\b',new_logs))
                     or bool(re.search(r'Process:\s*'+re.escape(APP_PACKAGE)+r'\b',new_logs)
                             and 'FATAL EXCEPTION' in new_logs))
        if crash_found:log_status='FAIL'
        # Confirm the installed target remains the intended debug package.
        android_release=ops.shell(['getprop','ro.build.version.release'],
                                  serial=device.serial,policy='serial',check=False)
        installed=ops.shell(['pm','path',APP_PACKAGE],serial=device.serial,policy='serial',check=False)
        install_status='PASS' if installed.ok and 'package:' in installed.stdout else 'FAIL'
        passed=passed and log_status=='PASS' and install_status=='PASS'
        status='PASS' if passed else ('FAIL' if did_execute else 'NOT_RUN')
        tests={'instrumentation_suite':status,
               'package_identity':install_status, 'crash_anr':log_status,
               'launcher_resolves': 'PASS' if passed else ('FAIL' if did_execute else 'NOT_RUN'),
               'startup_stability':'PASS' if passed else ('FAIL' if did_execute else 'NOT_RUN'),
               'root_integration':'NOT_RUN',
               'lsposed_provider_readonly':'NOT_RUN',
               'module_list':'NOT_RUN','repository_display':'NOT_RUN',
               'navigation':'NOT_RUN','filesystem_readonly':'NOT_RUN'}
        outcome={'schema':'devtool.r37.mmrl-device.v1','status':status,
            'debug_device_smoke_qualified':passed, 'device_qualified':False,
            'debug_device_qualified':False,
            'qualification_verdict': 'PASSED_DEBUG_DEVICE_SMOKE_ONLY' if passed else 'NOT_QUALIFIED',
            'consumer_qualified':False,'release_qualified':False,
            'build_source_sha':plan['source_sha'],'run_id':plan['run_id'],
            'attempt':plan['attempt'],'workflow':WORKFLOW,
            'app_sha256':plan['app_sha256'],'test_sha256':plan['test_sha256'],
            'signer_sha256':plan['signer_sha256'],
            'registry_ids':receipt['registry_ids'],
            'remote_artifacts':receipt['artifacts'],
            'device':device.as_dict(),
            'android_release': android_release.stdout.strip() if android_release.ok else None,
            'preexisting_packages':preexisting,
            'instrumentation':raw,'coverage':tests,
            'test_class':'com.dergoogler.mmrl.acceptance.OfficialDebugAcceptanceTest',
            'expected_test_count':3, 'package_path_check':installed.as_dict(),
            'instrumentation_returncode':child_returncode,
            'crash_anr':{'status':log_status,'matching_crash_or_anr':crash_found,
                         'fresh_boundary_verified':marker_pos>=0},
            'logs':{'instrumentation_receipt':str(evidence_path)},
            'limitations':['No physical-device result when the run has not executed',
                           'Privileged/root, LSPosed, module UI and repository display remain NOT_RUN'],
        }
        (session/'stdout.txt').write_text(child_stdout[-1024*1024:])
        (session/'stderr.txt').write_text(child_stderr[-1024*1024:])
        (session/'logcat-before.txt').write_text(before_text[-1024*1024:])
        (session/'logcat-after.txt').write_text(after_text[-1024*1024:])
        outcome['logs']['logcat_before']=str(session/'logcat-before.txt')
        outcome['logs']['logcat_after']=str(session/'logcat-after.txt')
        outcome['logs']['stdout']=str(session/'stdout.txt')
        outcome['logs']['stderr']=str(session/'stderr.txt')
        # Preserve AOPS03 crash/logcat/screenshots under its report directory.
        attached=evidence_path.parent/'android-instrumentation-evidence'
        if attached.exists():outcome['logs']['aops03_failure_evidence']=str(attached)
        with (session/'device-acceptance.json').open('x',encoding='utf-8') as out:
            os.fchmod(out.fileno(),0o600)
            json.dump(outcome,out,indent=2,sort_keys=True);out.write('\n')
        result=outcome
    finally:
        # Never remove an APK if another process replaced the staged file.
        for role,path in paths.items():
            if path.is_file() and not path.is_symlink() and _sha(path)==plan[role+'_sha256']:
                path.unlink()
    if result is None:_deny('R37 device execution did not yield a receipt')
    return result


def main(argv:list[str]|None=None)->None:
    p=argparse.ArgumentParser(description='R37 paired non-release MMRL debug artifact import + AOPS03 device acceptance')
    p.add_argument('command',choices=('plan','import','inspect','device'))
    p.add_argument('--repo',type=Path,default=Path.cwd())
    p.add_argument('--github-repo',default=REPO)
    p.add_argument('--run-id',type=int,required=True)
    p.add_argument('--attempt',type=int,required=True)
    p.add_argument('--source-sha',required=True)
    p.add_argument('--expected-app-sha256',required=True)
    p.add_argument('--expected-test-sha256',required=True)
    p.add_argument('--expected-signer-sha256',required=True)
    p.add_argument('--auth',choices=('auto','gh','env','none'),default='auto')
    p.add_argument('--allow-replace',action='store_true',help='Explicit permission to replace existing debug/test packages')
    args=p.parse_args(argv)
    root=args.repo.expanduser().resolve()
    stub={'repository':_slug(args.github_repo),'workflow':WORKFLOW,
        'run_id':args.run_id,'attempt':args.attempt,'source_sha':args.source_sha,
        'app_sha256':args.expected_app_sha256,'test_sha256':args.expected_test_sha256,
        'signer_sha256':args.expected_signer_sha256,
        'app_package':APP_PACKAGE,'test_package':TEST_PACKAGE}
    _check_keys(stub)
    if args.command=='inspect':
        result=inspect_local(root,stub)
    elif args.command=='device':
        result=_device_acceptance(root,stub,allow_replace=args.allow_replace)
    else:
        token,_=resolve_token(args.auth)
        client=GitHubActionsClient(token=token)
        plan=select(client,args.github_repo,args.run_id,args.attempt,args.source_sha,
                    args.expected_app_sha256,args.expected_test_sha256,args.expected_signer_sha256)
        result=({'status':'READ_ONLY_PLAN',**plan} if args.command=='plan'
                else import_pair(client,root,plan))
    print(json.dumps(result,sort_keys=True,indent=2))
    if args.command=='device' and result['status']!='PASS':
        raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except (GitHubActionsError, ValueError) as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(2) from None
