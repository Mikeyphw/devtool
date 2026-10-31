#!/usr/bin/env python3
"""R1R29 independently recount G1 JUnit/log originals from authenticated ZIPs.

Requires a preceding R1R28 GitHub metadata+SHA256 review. No API calls, no
release qualification and no guesswork from green job badges.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

SCHEMA = 'devtool.fc08.r1r29.g1-independent-primary-junit-recount.v1'
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_read(archive: Path, expected_sha: str) -> dict[str, bytes]:
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('MISSING_OR_UNSAFE_ARCHIVE')
    raw = archive.read_bytes()
    if _sha(raw) != expected_sha:
        raise ValueError('ARCHIVE_DIGEST_MISMATCH')
    with ZipFile(archive) as z:
        infos = z.infolist()
        if not infos or len(infos) > 3000 or len({i.filename for i in infos}) != len(infos):
            raise ValueError('ARCHIVE_MEMBERS_INVALID')
        total = 0
        payload = {}
        for info in infos:
            name = info.filename
            path = Path(name)
            if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
                or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('UNSAFE_ARCHIVE_MEMBER')
            total += info.file_size
            if total > 12 * 1024 * 1024:
                raise ValueError('ARCHIVE_UNCOMPRESSED_SIZE_EXCEEDED')
            if name.endswith(('.xml', '.json', '.log')):
                payload[name] = z.read(info)
        return payload


def _primary_cases(raw: bytes) -> int:
    if not 0 < len(raw) <= 2 * 1024 * 1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('UNSAFE_JUNIT_XML')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError('INVALID_JUNIT_XML') from None
    if root.tag not in ('testsuites', 'testsuite'):
        raise ValueError('INVALID_JUNIT_ROOT')
    suites = [root] if root.tag == 'testsuite' else list(root)
    if not suites or any(s.tag != 'testsuite' for s in suites):
        raise ValueError('INVALID_JUNIT_SUITE')
    for s in suites:
        for k in ('failures', 'errors', 'skipped'):
            if int(s.attrib.get(k, '-1')) != 0:
                raise ValueError('NONPASSING_JUNIT_SUITE')
    cases = list(root.iter('testcase'))
    if not cases or any(not x.attrib.get('name') or any(c.tag in ('failure','error','skipped') for c in x) for x in cases):
        raise ValueError('NONPASSING_OR_EMPTY_JUNIT_CASES')
    return len(cases)


def recount(archive_dir: Path, prior: dict) -> dict:
    src = prior.get('source')
    if (prior.get('all_provider_zip_digests_match') is not True or
        prior.get('archive_count') != 30 or not isinstance(src,dict) or
        not isinstance(src.get('sha'),str) or not SHA.fullmatch(src['sha']) or
        prior.get('release_qualified') is not False):
        raise ValueError('PRIOR_GITHUB_REVIEW_NOT_AUTHENTICATED')
    entries = prior.get('reviewed_archives')
    if not isinstance(entries,list) or len(entries)!=30 or any(not isinstance(e,dict) for e in entries):
        raise ValueError('PRIOR_ARCHIVE_INVENTORY_INVALID')
    byname = {e.get('name'):e for e in entries}
    if len(byname)!=30:
        raise ValueError('DUPLICATE_ARCHIVE_IDENTITY')
    required = {f'devtool-full-shard-{i}' for i in range(1,25)}|{'devtool-full-pytest-summary'}
    if not required <= set(byname):
        raise ValueError('G1_ARCHIVES_NOT_COMPLETE')
    prior_run_ids = {byname[n].get('run_id') for n in required}
    if len(prior_run_ids)!=1 or not all(type(x) is int and x>0 for x in prior_run_ids):
        raise ValueError('G1_RUN_ID_INCONSISTENT')
    for n in required:
        if not isinstance(byname[n].get('archive_sha256'),str) or not DIGEST.fullmatch(byname[n]['archive_sha256']):
            raise ValueError('MISSING_VERIFIED_GITHUB_DIGEST')
    modules_seen = set()
    total = 0
    inventories=set()
    shard_proofs=[]
    for i in range(1,25):
        name=f'devtool-full-shard-{i}'
        files = _safe_read(archive_dir/(name+'.zip'),byname[name]['archive_sha256'])
        try:
            receipt=json.loads(files['receipt.json'])
        except (KeyError,ValueError,UnicodeError):
            raise ValueError('INVALID_SHARD_RECEIPT') from None
        if (receipt.get('shard')!=i or receipt.get('shards')!=24 or
            receipt.get('source_sha')!=src['sha'] or
            receipt.get('repository')!='Mikeyphw/devtool' or
            receipt.get('success') is not True or receipt.get('complete') is not True or
            receipt.get('formal_release_qualified') is not False):
            raise ValueError('SHARD_SOURCE_OR_STATUS_MISMATCH')
        inv=receipt.get('inventory_sha256');inventories.add(inv)
        records=receipt.get('modules');expected=receipt.get('modules_expected')
        if (not isinstance(records,list) or not isinstance(expected,list) or
            len(records)!=len(expected) or not records):
            raise ValueError('SHARD_MODULES_MISSING')
        current=set()
        shard_tests=0
        for row in records:
            if not isinstance(row,dict):raise ValueError('INVALID_MODULE_ROW')
            name_mod=row.get('module')
            if (not isinstance(name_mod,str) or name_mod not in expected or
                name_mod in current or name_mod in modules_seen or
                row.get('status')!='PASS' or row.get('exit_code')!=0 or
                any(row.get(k)!=0 for k in ('failures','errors','skipped'))):
                raise ValueError('MODULE_DUPLICATE_OR_NOT_PASSING')
            current.add(name_mod)
            junit=row.get('junit');log=row.get('log')
            if (not isinstance(junit,str) or not re.fullmatch(r'junit/module-[0-9]{3}\.xml',junit) or
                not isinstance(log,str) or not re.fullmatch(r'logs/module-[0-9]{3}\.log',log)):
                raise ValueError('MODULE_PATH_INVALID')
            if junit not in files or log not in files or _sha(files[junit])!=row.get('junit_sha256') or _sha(files[log])!=row.get('log_sha256'):
                raise ValueError('MODULE_JUNIT_LOG_HASH_MISMATCH')
            cases=_primary_cases(files[junit])
            if type(row.get('tests')) is not int or cases!=row['tests']:
                raise ValueError('JUNIT_PRIMARY_CASE_COUNT_MISMATCH')
            shard_tests+=cases
        if current!=set(expected) or len(current)!=len(expected):
            raise ValueError('SHARD_INVENTORY_MISMATCH')
        modules_seen.update(current)
        total+=shard_tests
        shard_proofs.append({'shard':i,'modules':len(current),'primary_tests':shard_tests,
                             'archive_sha256':byname[name]['archive_sha256']})
    if len(inventories)!=1 or not all(isinstance(x,str) and DIGEST.fullmatch(x) for x in inventories):
        raise ValueError('INVENTORY_DIGEST_INCONSISTENT')
    sname='devtool-full-pytest-summary'
    sf=_safe_read(archive_dir/(sname+'.zip'),byname[sname]['archive_sha256'])
    try: summary=json.loads(sf['summary.json'])
    except (KeyError,ValueError,UnicodeError):raise ValueError('INVALID_G1_SUMMARY') from None
    if (summary.get('status')!='PASS' or summary.get('source_sha')!=src['sha'] or
        summary.get('expected_shards')!=24 or summary.get('present_shards')!=24 or
        summary.get('covered_modules')!=len(modules_seen) or
        summary.get('total_modules')!=len(modules_seen) or
        summary.get('tests')!=total or
        summary.get('formal_release_qualified') is not False or
        any(summary.get(k)!=0 for k in ('failures','errors','skipped')) or
        summary.get('issues')!=[]):
        raise ValueError('G1_SUMMARY_RECOUNT_MISMATCH')
    return {'schema':SCHEMA,'source_sha':src['sha'],'g1_run_id':next(iter(prior_run_ids)),
            'primary_tests_recounted':total,'unique_modules_recounted':len(modules_seen),
            'shards_verified':24,'inventory_sha256':next(iter(inventories)),
            'shard_proofs':shard_proofs,
            'original_zip_sha256_and_junit_log_bytes_verified':True,
            'g1_junit_independently_recounted':True,
            'termux_qualified':False,'raw_windows_signed_artifacts_verified':False,
            'fc08_overall_qualified':False,'release_qualified':False}


def _save(path: Path,obj:dict)->None:
    if path.is_symlink() or (path.exists() and not path.is_file()):raise ValueError('UNSAFE_OUTPUT')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.fc08-r1r29-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(obj,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.lexists(temp):os.unlink(temp)


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--archives',type=Path,required=True)
    p.add_argument('--provider-review',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.provider_review.is_symlink() or a.provider_review.stat().st_size>2*1024*1024:
            raise ValueError('UNSAFE_REVIEW_FILE')
        result=recount(a.archives,json.loads(a.provider_review.read_text()))
        _save(a.out,result)
    except (OSError,ValueError,TypeError,BadZipFile,ET.ParseError):
        print('BLOCKED_FC08_R1R29_G1_INDEPENDENT_RECOUNT',file=sys.stderr)
        return 2
    print('PASS_FC08_R1R29_G1_JUNIT_RECOUNT_NON_RELEASE')
    return 0
if __name__=='__main__':raise SystemExit(main())
