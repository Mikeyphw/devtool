"""R36: exact-run consumer APK import through R35/ACTIONS-04/ACTIONS-05 services.

A tightly reviewed two-archive adapter, not an alternate GitHub transport,
Android verifier, registry or release/signing authority. Explicit debug only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile
from typing import Any

from .actions_r35_verified_import import (
    _deny, _json_member, _requires, _sha, _verified_or_download, HEX40, HEX64,
)
from .actions_artifacts import artifact_list, check_zip
from .actions_android import android_collect
from .artifact_registry import register_paths, verify_record, registry_path
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug, resolve_token

# This is a reviewed consumer allowlist, not a dynamically selected workflow.
# Adding a consumer requires inspecting its actual workflow and ZIP receipts.
DEVTOOL_SHA = '246f168ebdef6d9b25c194268c810c877d099fa3'
PROFILES: dict[str, dict[str, Any]] = {
    'mmrl': {
        'repository': 'Mikeyphw/MMRL',
        'workflow': 'devtool-mmrl-r36-hosted-official-debug.yml',
        'apk_role': 'mmrl-r36-official-debug-native-dag',
        'evidence_role': 'mmrl-r36-evidence',
        'plan_schema': 'mmrl.r36.hosted-plan.v1',
        'plan_status': 'REVIEWED_OFFICIAL_DEBUG_ONLY',
        'verification_schema': 'mmrl.r36.hosted-verification.v1',
        'tasks': [':app:lintOfficialDebug', ':app:assembleOfficialDebug'],
        'output': 'app/build/outputs/apk/official/debug/MMRL-R36-OfficialDebug.apk',
        'producer_node': 'mmrl_hosted_r36.ci.01-job-official_debug',
        'package': 'com.mikeyphw.mmrl.debug',
    },
    'dustui': {
        'repository': 'Mikeyphw/dustui',
        'workflow': 'devtool-dustui-r36-hosted-debug.yml',
        'apk_role': 'dustui-r36-debug-native-dag',
        'evidence_role': 'dustui-r36-plan',
        'plan_schema': 'dustui.r36.hosted-plan.v1',
        'plan_status': 'REVIEWED_DEBUG_ONLY',
        'verification_schema': None,
        'tasks': [':app:lintDebug', ':app:assembleDebug'],
        'output': 'android/app/build/outputs/apk/debug/app-debug.apk',
        'producer_node': 'dustui_hosted_r36.ci.01-job-debug_apk',
        'package': None,
    },
}
ROLES = ('apk', 'evidence')
APK_LIMIT = 150 * 1024 * 1024


def select(client: GitHubActionsClient, profile: str, repository: str, run_id: int,
           attempt: int, source_sha: str, signer_sha: str,
           package: str, apk_sha: str) -> dict[str, Any]:
    if profile not in PROFILES:
        _deny('unreviewed R36 consumer profile')
    cfg = PROFILES[profile]
    repository = _slug(repository)
    if repository.lower() != cfg['repository'].lower():
        _deny('consumer repository not covered by reviewed profile')
    if (type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1
        or not isinstance(source_sha, str) or not HEX40.fullmatch(source_sha)
        or not isinstance(signer_sha, str) or not HEX64.fullmatch(signer_sha)
        or not isinstance(apk_sha, str) or not HEX64.fullmatch(apk_sha)
        or not isinstance(package, str) or not package or len(package) > 200
        or (cfg['package'] is not None and package != cfg['package'])):
        _deny('explicit run/attempt/source/package/signer/APK digest pins required')
    inventory = artifact_list(client, repository, run_id)
    remote = inventory['remote_run']
    workflow = client.workflow(repository, cfg['workflow'])
    if (remote.get('completed') is not True or remote.get('conclusion') != 'success'
        or remote.get('event') != 'workflow_dispatch'
        or remote.get('workflow_id') != workflow.get('id')
        or remote.get('run_attempt') != attempt or remote.get('head_sha') != source_sha):
        _deny('R36 remote run/workflow/attempt/source mismatch')
    names = {role: f"{cfg[role+'_role']}-attempt{attempt}" for role in ROLES}
    selected = {}
    for role, name in names.items():
        rows = [x for x in inventory['artifacts'] if x['name'] == name]
        if len(rows) != 1:
            _deny('missing or ambiguous R36 artifact: ' + role)
        item = rows[0]
        if (item.get('run_id') != run_id or item.get('run_attempt') != attempt
            or item.get('head_sha') != source_sha or item.get('expired') is not False
            or not isinstance(item.get('digest'), str)
            or not item['digest'].startswith('sha256:')
            or not HEX64.fullmatch(item['digest'][7:])):
            _deny('untrusted R36 artifact metadata: ' + role)
        selected[role] = item
    if selected['apk']['id'] == selected['evidence']['id']:
        _deny('duplicate R36 artifact identity')
    return {'schema': 'devtool.r36.import-plan.v1', 'profile': profile,
            'repository': repository, 'workflow': cfg['workflow'],
            'workflow_id': workflow['id'], 'run_id': run_id, 'attempt': attempt,
            'source_sha': source_sha, 'package': package, 'signer_sha256': signer_sha,
            'expected_apk_sha256': apk_sha, 'artifacts': selected,
            'release_qualified': False, 'device_qualified': False}


def _members(z: zipfile.ZipFile, expected: set[str], label: str) -> None:
    if set(z.namelist()) != expected or len(z.infolist()) != len(expected):
        _deny('R36 '+label+' archive members are missing/ambiguous/unreviewed')


def _hash_member(z: zipfile.ZipFile, member: str) -> tuple[str, int]:
    info = z.getinfo(member)
    if info.is_dir() or not 0 < info.file_size <= APK_LIMIT:
        _deny('invalid R36 APK member size')
    h=hashlib.sha256(); size=0
    with z.open(info) as src:
        for chunk in iter(lambda: src.read(1024*1024), b''):
            h.update(chunk); size += len(chunk)
            if size > APK_LIMIT: _deny('oversized R36 APK member')
    return h.hexdigest(), size


def validate_chain(plan: dict[str, Any], archives: dict[str, Path]) -> dict[str, Any]:
    if set(archives) != set(ROLES) or plan.get('profile') not in PROFILES:
        _deny('exactly two reviewed R36 archives required')
    cfg=PROFILES[plan['profile']]
    if (plan.get('repository', '').lower() != cfg['repository'].lower()
        or plan.get('workflow') != cfg['workflow']
        or not HEX40.fullmatch(str(plan.get('source_sha', '')))
        or not HEX64.fullmatch(str(plan.get('signer_sha256', '')))
        or not HEX64.fullmatch(str(plan.get('expected_apk_sha256', '')))
        or type(plan.get('attempt')) is not int or plan['attempt'] < 1
        or type(plan.get('run_id')) is not int or plan['run_id'] < 1
        or (cfg['package'] and plan.get('package') != cfg['package'])):
        _deny('R36 local plan provenance/profile mismatch')
    for path in archives.values(): check_zip(path)
    output=cfg['output']; member='payload/'+output
    with zipfile.ZipFile(archives['apk']) as apk, zipfile.ZipFile(archives['evidence']) as evidence:
        _members(apk, {'receipt.json', member}, 'APK')
        _members(evidence, {'plan.json', 'verification.json'} if cfg['verification_schema'] else {'plan.json'}, 'evidence')
        receipt=_json_member(apk, 'receipt.json')
        plan_doc=_json_member(evidence, 'plan.json')
        _requires(plan_doc, {'schema': cfg['plan_schema'], 'status': cfg['plan_status'],
            'source_sha': plan['source_sha'], 'devtool_sha': DEVTOOL_SHA,
            'tasks': cfg['tasks'], 'output': output,
            'graph_sha256': receipt.get('graph_sha256'), 'release_qualified': False,
            'device_qualified': False}, 'R36 hosted plan')
        if cfg['package'] and plan_doc.get('package') != plan['package']:
            _deny('R36 producer plan package mismatch')
        if (plan_doc.get('artifact_independently_authenticated',
                        plan_doc.get('authenticated_artifact')) is not False):
            _deny('R36 producer plan must not claim authenticated artifacts')
        graph=plan_doc['graph_sha256']
        if not HEX64.fullmatch(str(graph)):
            _deny('invalid R36 native graph digest')
        _requires(receipt, {'kind': 'devtool.actions.node-handoff', 'schema_version': 2,
            'source_commit': plan['source_sha'], 'attempt': plan['attempt'],
            'run_id': str(plan['run_id']), 'producer_job': 'node-01',
            'producer_node': cfg['producer_node'], 'graph_sha256': graph,
            'authenticated': False, 'release_qualified': False}, 'R36 native DAG handoff')
        if not HEX64.fullmatch(str(receipt.get('workflow_sha256'))):
            _deny('missing R36 hosted workflow graph checksum')
        digest,size=_hash_member(apk,member)
        if digest != plan['expected_apk_sha256']:
            _deny('R36 APK bytes do not match operator pinned SHA-256')
        files=receipt.get('files')
        if (not isinstance(files,list) or len(files)!=1 or not isinstance(files[0],dict)
            or any(files[0].get(k)!=v for k,v in {'path':output,'sha256':digest,'size':size}.items())):
            _deny('R36 native staging receipt does not match APK bytes')
        if cfg['verification_schema']:
            verification=_json_member(evidence,'verification.json')
            _requires(verification, {'schema': cfg['verification_schema'],
                'status': 'VERIFIED_DEBUG_NON_RELEASE', 'source_sha': plan['source_sha'],
                'run_id': plan['run_id'], 'attempt': plan['attempt'],
                'devtool_sha': DEVTOOL_SHA, 'graph_sha256': graph,
                'tasks': cfg['tasks'], 'output': output, 'package': plan['package'],
                'apk_sha256': digest, 'apk_size': size,
                'signer_sha256': plan['signer_sha256'],
                'signature_independently_verified': True,
                'package_independently_verified': True,
                'artifact_independently_authenticated': False,
                'release_qualified': False, 'device_qualified': False}, 'R36 hosted verification')
    return {'schema': 'devtool.r36.archive-chain.v1', 'profile': plan['profile'],
        'apk_sha256': digest,'apk_size':size,'apk_member':member,
        'graph_sha256':graph,'workflow_sha256':receipt['workflow_sha256'],
        'package':plan['package'],'signer_sha256':plan['signer_sha256'],
        'release_qualified':False,'device_qualified':False}


def _receipt_path(root: Path, plan: dict) -> Path:
    if (plan.get('profile') not in PROFILES or type(plan.get('run_id')) is not int
        or plan['run_id']<1 or type(plan.get('attempt')) is not int or plan['attempt']<1
        or not HEX40.fullmatch(str(plan.get('source_sha', '')))):
        _deny('unsafe R36 local receipt selector')
    root=root.resolve()
    base=root/'.devtool'/'artifacts'/'r36-imports'
    for x in (root/'.devtool',root/'.devtool'/'artifacts',base):
        if x.is_symlink():_deny('symlinked R36 storage boundary')
    return base/f"{plan['profile']}-run{plan['run_id']}-attempt{plan['attempt']}-{plan['source_sha'][:12]}.json"


def inspect_local(root: Path, plan: dict) -> dict[str, Any]:
    """Read only: must never rewrite registry or trust a bare receipt."""
    path=_receipt_path(root,plan)
    if not path.exists():
        if path.is_symlink():_deny('symlinked R36 receipt')
        return {'status':'NOT_IMPORTED','remote_actions_artifact_authenticated':False}
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        _deny('unsafe R36 receipt path')
    try:row=json.loads(path.read_text())
    except (OSError,ValueError,UnicodeError):_deny('malformed R36 receipt')
    required={'schema':'devtool.r36.import.v1','status':'IMPORTED_VERIFIED_DEBUG',
              'profile':plan['profile'],'repository':PROFILES[plan['profile']]['repository'],
              'run_id':plan['run_id'],'attempt':plan['attempt'],'source_sha':plan['source_sha'],
              'remote_actions_artifact_authenticated':True,
              'release_qualified':False,'device_qualified':False}
    _requires(row,required,'stored R36 receipt')
    for name in ('package','signer_sha256','expected_apk_sha256'):
        if name in plan and row.get({'expected_apk_sha256':'apk_sha256'}.get(name,name))!=plan[name]:
            _deny('R36 stored receipt pin mismatch: '+name)
    if 'artifacts' in plan and row.get('artifacts')!={role:{'id':plan['artifacts'][role]['id'],
        'digest':plan['artifacts'][role]['digest']} for role in ROLES}:
        _deny('R36 locally stored artifact IDs/digests differ from GitHub inventory')
    registry=registry_path(root)
    if registry.is_symlink() or not registry.is_file():_deny('missing canonical artifact registry')
    try:contents=json.loads(registry.read_text())
    except (ValueError,OSError,UnicodeError):_deny('invalid canonical registry JSON')
    items=contents.get('items') if isinstance(contents,dict) else None
    if not isinstance(items,list):_deny('invalid canonical registry items')
    record=next((x for x in items if isinstance(x,dict) and x.get('id')==row.get('registry_id')),None)
    if not record or record.get('sha256')!=row.get('apk_sha256'):
        _deny('R36 registry record identity/hash mismatch')
    metadata=record.get('metadata')
    if not isinstance(metadata,dict) or any(metadata.get(k)!=v for k,v in {
        'remote_repository':row['repository'],'remote_run_id':row['run_id'],
        'remote_run_attempt':row['attempt'],'remote_head_sha':row['source_sha'],
        'remote_actions_artifact_authenticated':True,'signer_sha256':row['signer_sha256'],
        'consumer_profile':row['profile'],'graph_sha256':row['graph_sha256']}.items()):
        _deny('R36 registry provenance mismatch')
    raw=root.resolve()/str(record.get('path',''));resolved=raw.resolve()
    if (raw.is_symlink() or resolved.parent!=path.parent.resolve() or not resolved.is_file()
        or _sha(resolved)!=row['apk_sha256']):
        _deny('R36 registered APK disappeared or was modified')
    return {**row,'local_bytes_reverified':True}


def import_exact_run(client:GitHubActionsClient,root:Path,plan:dict)->dict[str,Any]:
    """Import through the existing ACTIONS-04/ACTIONS-05/registry pipeline."""
    old=inspect_local(root,plan)
    if old.get('status')!='NOT_IMPORTED':return old
    verified={role:_verified_or_download(client,plan,role,root) for role in ROLES}
    archives={role:Path(row['file']) for role,row in verified.items()}
    chain=validate_chain(plan,archives)
    # Use ACTIONS-05 debug build-type inspection. The named variant is checked
    # against authenticated R36 plan + native receipt above; R36 does not ship
    # the separate android-variant-manifest required by the generic variant API.
    sdk=android_collect(client,plan['repository'],root,run_id=plan['run_id'],
        artifact_id=plan['artifacts']['apk']['id'],fmt='apk',variant='debug',
        expected_package=plan['package'],expected_cert=plan['signer_sha256'],
        member=chain['apk_member'],materialize=False,download=False,
        workflow=plan['workflow'],expected_source_sha=plan['source_sha'],
        expected_run_attempt=plan['attempt'])
    info=sdk['android']
    if (info.get('sha256')!=chain['apk_sha256'] or info.get('size')!=chain['apk_size']
        or info.get('package')!=plan['package'] or info.get('signer_sha256')!=plan['signer_sha256']
        or info.get('signature_verified') is not True or info.get('manifest_verified') is not True):
        _deny('R36 independent ACTIONS-05 package/signature inspection mismatch')
    report=_receipt_path(root,plan)
    report.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=report.with_suffix('.apk')
    if any(p.exists() or p.is_symlink() for p in (target,report)):
        _deny('existing/incomplete R36 import requires manual reconciliation')
    with zipfile.ZipFile(archives['apk']) as z,z.open(chain['apk_member']) as src,target.open('xb') as out:
        os.fchmod(out.fileno(),0o600)
        h=hashlib.sha256();size=0
        for chunk in iter(lambda:src.read(1024*1024),b''):
            size+=len(chunk)
            if size>APK_LIMIT:_deny('R36 extraction exceeds bound')
            out.write(chunk);h.update(chunk)
        out.flush();os.fsync(out.fileno())
    if h.hexdigest()!=chain['apk_sha256'] or size!=chain['apk_size']:
        _deny('R36 local extraction hash/size differs')
    metadata={'producer':'github-actions-r36-r35-verified-import',
        'state':'android-debug-exact-run-inspected-unqualified',
        'remote_repository':plan['repository'],'remote_run_id':plan['run_id'],
        'remote_run_attempt':plan['attempt'],'remote_head_sha':plan['source_sha'],
        'remote_workflow_id':plan['workflow_id'],'workflow':plan['workflow'],
        'remote_artifact_ids':{r:plan['artifacts'][r]['id'] for r in ROLES},
        'remote_archive_digests':{r:plan['artifacts'][r]['digest'] for r in ROLES},
        'consumer_profile':plan['profile'],'graph_sha256':chain['graph_sha256'],
        'workflow_sha256':chain['workflow_sha256'],'package':plan['package'],
        'variant':'debug','signer_sha256':plan['signer_sha256'],
        'signature_verified':True,'manifest_verified':True,
        'remote_actions_artifact_authenticated':True,
        'release_qualified':False,'device_qualified':False,
        'verification_status':'exact-run-archives-and-android-inspected'}
    created=register_paths(root,[target],metadata=metadata)
    if len(created)!=1 or created[0].get('sha256')!=chain['apk_sha256']:
        _deny('R36 canonical registry rejected verified APK')
    checked=verify_record(root,created[0]['id'])
    if checked.get('valid') is not True or checked.get('current_sha256')!=chain['apk_sha256']:
        _deny('R36 imported APK registry verification failed')
    row={**chain,'schema':'devtool.r36.import.v1','status':'IMPORTED_VERIFIED_DEBUG',
        'repository':plan['repository'],'run_id':plan['run_id'],
        'attempt':plan['attempt'],'source_sha':plan['source_sha'],
        'workflow':plan['workflow'],'workflow_id':plan['workflow_id'],
        'registry_id':created[0]['id'],
        'artifacts':{role:{'id':plan['artifacts'][role]['id'],
                          'digest':plan['artifacts'][role]['digest']} for role in ROLES},
        'remote_actions_artifact_authenticated':True,
        'apk_signature_independently_verified':True,
        'release_qualified':False,'device_qualified':False,'consumer_qualified':False}
    with report.open('x',encoding='utf8') as out:
        os.fchmod(out.fileno(),0o600)
        json.dump(row,out,indent=2,sort_keys=True)
        out.write('\n');out.flush();os.fsync(out.fileno())
    return row


def main(argv: list[str] | None=None) -> None:
    parser=argparse.ArgumentParser(description='R36 real-consumer authenticated debug APK import (non-release)')
    parser.add_argument('command',choices=('plan','import','inspect'))
    parser.add_argument('--profile',required=True,choices=tuple(PROFILES))
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--github-repo',required=True)
    parser.add_argument('--run-id',type=int,required=True)
    parser.add_argument('--attempt',type=int,required=True)
    parser.add_argument('--source-sha',required=True)
    parser.add_argument('--expected-signer-sha256',required=True)
    parser.add_argument('--expected-package',required=True)
    parser.add_argument('--expected-apk-sha256',required=True)
    parser.add_argument('--auth',choices=('auto','gh','env','none'),default='auto')
    args=parser.parse_args(argv)
    stub={'profile':args.profile,'repository':_slug(args.github_repo),'run_id':args.run_id,
        'attempt':args.attempt,'source_sha':args.source_sha,
        'signer_sha256':args.expected_signer_sha256,'package':args.expected_package,
        'expected_apk_sha256':args.expected_apk_sha256}
    root=args.repo.resolve()
    if args.command=='inspect':
        result=inspect_local(root,stub)
    else:
        token,_=resolve_token(args.auth)
        client=GitHubActionsClient(token=token)
        plan=select(client,args.profile,args.github_repo,args.run_id,args.attempt,
            args.source_sha,args.expected_signer_sha256,args.expected_package,args.expected_apk_sha256)
        result={'status':'READ_ONLY_PLAN',**plan,'remote_actions_artifact_authenticated':False} if args.command=='plan' else import_exact_run(client,root,plan)
    print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__':
    try:main()
    except GitHubActionsError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(2) from None
