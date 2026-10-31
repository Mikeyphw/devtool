"""ANDROID-R31: fail-closed, read-only Android consumer adoption evidence.

This gate does NOT fetch GitHub artifacts or assert device/release acceptance.
The upstream ACTIONS-04/05 download, ZIP, Android signer and manifest checks must
have succeeded. Recheck local bytes against the immutable artifact registry and
an explicit operator-held source/run/certificate pin before issuing a handoff.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from .github_actions import GitHubActionsError

_SHA40=re.compile(r'[a-f0-9]{40}\Z')
_SHA256=re.compile(r'[a-f0-9]{64}\Z')
_PACKAGE=re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z')
_REPO=re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z')
_WORKFLOW=re.compile(r'(?:\.github/workflows/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.ya?ml\Z')
_CONSUMERS=frozenset({'relatorio','telegram'})


def _deny(reason:str)->None:
    # Never embed untrusted paths, tokens, or receipt bodies in errors.
    raise GitHubActionsError('ANDROID_R31_'+reason)


def _integer(v: Any)->bool:
    return type(v) is int and v>0


def _git(repo:Path,*args:str)->str:
    try:
        p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,timeout=15,check=False)
    except (OSError,subprocess.SubprocessError):
        _deny('SOURCE_UNAVAILABLE')
    if p.returncode:_deny('SOURCE_UNAVAILABLE')
    return p.stdout.strip()


def _regular(path:Path)->None:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
        _deny('FILE_UNSAFE')


def _read_registry(repo:Path)->list[dict[str,Any]]:
    """Read registry without load_registry's corrupt-registry backup side effects."""
    folder=repo/'.devtool'/'artifacts'
    for part in (repo/'.devtool',folder):
        if part.is_symlink():_deny('REGISTRY_PATH_UNSAFE')
    file=folder/'registry.json'
    _regular(file)
    try:
        if file.stat().st_size>16*1024*1024:_deny('REGISTRY_OVERSIZED')
        document=json.loads(file.read_text('utf-8'))
    except (OSError,ValueError,UnicodeError):
        _deny('REGISTRY_INVALID')
    if not isinstance(document,dict) or not isinstance(document.get('items'),list):
        _deny('REGISTRY_INVALID')
    return document['items']


def _hash(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def handoff(repo:Path, collected:dict[str,Any], *, consumer:str, repository:str,
            source_sha:str, run_id:int, run_attempt:int, package:str,
            signer_sha256:str, workflow:str, variant:str, fmt:str='apk',
            role:str='application') -> dict[str,Any]:
    """Read-only gate over an ACTIONS-05 materialized+registered collection receipt."""
    if consumer not in _CONSUMERS:_deny('CONSUMER_UNREVIEWED')
    if (not _REPO.fullmatch(repository) or not _SHA40.fullmatch(source_sha) or
        not _SHA256.fullmatch(signer_sha256) or not _PACKAGE.fullmatch(package) or
        not _WORKFLOW.fullmatch(workflow) or not _integer(run_id) or
        not _integer(run_attempt) or fmt not in ('apk','aab') or
        role not in ('application','instrumentation') or
        not re.fullmatch(r'[a-z][A-Za-z0-9]{0,60}',variant)):
        _deny('INVALID_OPERATOR_PINS')
    if role!='application':_deny('ROLE_NOT_SUPPORTED_FOR_CONSUMER')
    if not isinstance(collected,dict):_deny('COLLECT_RECEIPT_INVALID')
    root=repo.resolve(strict=True)
    if (_git(root,'rev-parse','HEAD') != source_sha or
        _git(root,'status','--porcelain','--untracked-files=all')):
        _deny('SOURCE_NOT_CLEAN_OR_BOUND')
    expected={
        'phase':'ACTIONS-05','repository':repository,'run_id':run_id,
        'remote_head_sha':source_sha,'remote_attempt':run_attempt,
        'workflow':workflow,'archive_verified':True,'qualified_release':False,
    }
    if any(type(collected.get(k)) is not type(v) or collected[k]!=v for k,v in expected.items()):
        _deny('COLLECT_SOURCE_OR_RUN_MISMATCH')
    if not _integer(collected.get('artifact_id')) or not _integer(collected.get('remote_workflow_id')):
        _deny('COLLECT_SOURCE_OR_RUN_MISMATCH')
    android=collected.get('android')
    if not isinstance(android,dict):_deny('PACKAGE_EVIDENCE_MISSING')
    expected_android={
        'package':package,'signer_sha256':signer_sha256,'format':fmt,'variant':variant,
        'signature_verified':True,'manifest_verified':True,'qualified_release':False,
    }
    if any(type(android.get(k)) is not type(v) or android[k]!=v for k,v in expected_android.items()):
        _deny('PACKAGE_SIGNER_OR_MANIFEST_MISMATCH')
    digest=android.get('sha256');size=android.get('size')
    if not isinstance(digest,str) or not _SHA256.fullmatch(digest) or not _integer(size):
        _deny('PACKAGE_DIGEST_INVALID')
    if collected.get('release_gate')!='not-implemented':
        _deny('FALSE_RELEASE_CLAIM')
    # Bind only exact canonical artifact storage; never open a caller-controlled path.
    if not isinstance(collected.get('package_file'),str):_deny('PACKAGE_FILE_MISSING')
    folder=root/'.devtool'/'artifacts'/'android-verified'
    for p in (root/'.devtool',root/'.devtool'/'artifacts',folder):
        if p.is_symlink():_deny('PACKAGE_STORAGE_UNSAFE')
    file=Path(collected['package_file'])
    if (not file.is_absolute() or file.is_symlink() or file.parent.resolve()!=folder or
        file != folder/(f"github-run{run_id}-artifact{collected['artifact_id']}-{digest[:16]}.{fmt}")):
        _deny('PACKAGE_FILE_NOT_CANONICAL')
    _regular(file)
    if file.stat().st_size!=size or _hash(file)!=digest:_deny('PACKAGE_BYTES_CHANGED')
    records=[row for row in _read_registry(root) if isinstance(row,dict) and
             row.get('id')==collected.get('registry_id')]
    if len(records)!=1:_deny('REGISTRY_RECORD_MISSING_OR_DUPLICATE')
    record=records[0]
    if record.get('sha256')!=digest or root/str(record.get('path',''))!=file:
        _deny('REGISTRY_PACKAGE_MISMATCH')
    meta=record.get('metadata')
    if not isinstance(meta,dict):_deny('REGISTRY_METADATA_MISSING')
    evidence={
        'producer':'github-actions','state':'android-inspected-unqualified',
        'remote_repository':repository,'remote_run_id':run_id,
        'remote_run_attempt':run_attempt,'remote_workflow_id':collected['remote_workflow_id'],
        'remote_head_sha':source_sha,'remote_artifact_id':collected['artifact_id'],
        'workflow':workflow,'package':package,'signer_sha256':signer_sha256,
        'format':fmt,'variant':variant,'package_sha256':digest,
        'signature_verified':True,'qualified_release':False,'apk_role':role,
    }
    if any(type(meta.get(k)) is not type(v) or meta[k]!=v for k,v in evidence.items()):
        _deny('REGISTRY_EVIDENCE_MISMATCH')
    return {
        'schema':'devtool.android.r31.consumer-handoff.v1',
        'status':'PASS_UNQUALIFIED',
        'consumer':consumer,'repository':repository,'source_sha':source_sha,
        'remote_run_id':run_id,'remote_run_attempt':run_attempt,
        'remote_workflow_id':collected['remote_workflow_id'],
        'artifact_id':collected['artifact_id'],'artifact_registry_id':collected['registry_id'],
        'package':package,'signer_sha256':signer_sha256,'package_sha256':digest,
        'variant':variant,'format':fmt,'apk_role':role,
        'archive_digest_reverified_here':False,
        'signed_package_file_rehashed_here':True,
        'canonical_registry_metadata_reverified':True,
        'upstream_android_signature_and_manifest_verified':True,
        'dispatch_count_new':None,
        'consumer_build_integration_qualified':False,
        'consumer_installation_qualified':False,'device_qualified':False,
        'release_qualified':False,'fc09_qualified':False,
    }
