#!/usr/bin/env python3
"""Collect/authenticate exact-source FC08+G1 GitHub ZIPs (not a release seal)."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
import zipfile
from io import BytesIO

SCHEMA='devtool.fc08.r1r28.independent-host-g1-archive-review.v1'
REPO='Mikeyphw/devtool'
LANES={'fc08-real-github-actions-dag','fc08-real-linux-polyglot','fc08-real-windows-dotnet-msvc','fc08-real-macos-native','fc08-real-host-summary'}

def sha256(b: bytes):return hashlib.sha256(b).hexdigest()

def local_source(root:Path):
    def g(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True,timeout=15).strip()
    return {'sha':g('rev-parse','HEAD'),'tree':g('rev-parse','HEAD^{tree}')}

class StripCrossHostAuthorization(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old=urlsplit(req.full_url);new=urlsplit(newurl)
        if new.scheme!='https' or new.username or new.password:
            raise ValueError('UNSAFE_ARTIFACT_REDIRECT')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected and old.hostname != new.hostname:
            for key in tuple(redirected.headers):
                if key.lower() in ('authorization','cookie','proxy-authorization'):
                    del redirected.headers[key]
        return redirected

class Client:
    def __init__(self,token):
        if not token:raise ValueError('GITHUB_TOKEN_REQUIRED')
        self.token=token
        self.opener=build_opener(StripCrossHostAuthorization(),HTTPSHandler())
    def get(self,path,limit=16*1024*1024):
        if not re.fullmatch(r'/repos/Mikeyphw/devtool/(?:actions|commits)/[A-Za-z0-9_./?=&-]+',path):
            raise ValueError('UNREVIEWED_API_PATH')
        req=Request('https://api.github.com'+path,headers={
            'Authorization':'Bearer '+self.token,
            'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2022-11-28','User-Agent':'devtool-fc08-r1r28-review/1'})
        with self.opener.open(req,timeout=40) as r:
            raw=r.read(limit+1)
        if len(raw)>limit:raise ValueError('OVERSIZED_GITHUB_RESPONSE')
        return raw
    def json(self,path):
        return json.loads(self.get(path))

def verify_archive(raw:bytes, meta:dict, *, max_extract:int=12*1024*1024):
    digest=meta.get('digest')
    if not isinstance(digest,str) or not re.fullmatch('sha256:[0-9a-f]{64}',digest):
        raise ValueError('MISSING_ARCHIVE_DIGEST')
    if sha256(raw)!=digest.split(':',1)[1]:raise ValueError('ARCHIVE_SHA256_MISMATCH')
    with zipfile.ZipFile(BytesIO(raw)) as z:
        names=z.namelist()
        if not names or len(set(names))!=len(names) or len(names)>3500:
            raise ValueError('ARCHIVE_MEMBER_COUNT_INVALID')
        total=0
        for item in z.infolist():
            path=Path(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename or
                    (item.external_attr>>16)&0o170000 == 0o120000):
                raise ValueError('UNSAFE_ARCHIVE_MEMBER')
            total+=item.file_size
            if total>max_extract:raise ValueError('ARCHIVE_CONTENT_TOO_LARGE')
        read={name:z.read(name) for name in names if name.endswith('.json') and not name.startswith('__MACOSX/')}
    return {'archive_sha256':sha256(raw),'members':len(names),
            'json_entries':list(read),'json_payloads':read}

def verify_provider_runs(client,host_run:int,g1_run:int,src:dict):
    outcome={}
    for label,run_id in [('host',host_run),('g1',g1_run)]:
        run=client.json(f'/repos/{REPO}/actions/runs/{run_id}')
        if (run.get('head_sha')!=src['sha'] or run.get('run_attempt')!=1 or
            run.get('status')!='completed' or run.get('conclusion')!='success' or
            run.get('repository',{}).get('full_name')!=REPO):
            raise ValueError('GITHUB_RUN_NOT_SAME_SOURCE_PASS')
        jobs=client.json(f'/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100')
        rows=jobs.get('jobs') or []
        expected=5 if label=='host' else 25
        if (jobs.get('total_count')!=expected or len(rows)!=expected or
            any(j.get('conclusion')!='success' or j.get('run_attempt')!=1 for j in rows)):
            raise ValueError('GITHUB_JOBS_INCOMPLETE_OR_FAILED')
        arts=client.json(f'/repos/{REPO}/actions/runs/{run_id}/artifacts?per_page=100')
        items=arts.get('artifacts') or []
        if (arts.get('total_count')!=expected or len(items)!=expected or
            any(x.get('expired') or x.get('workflow_run',{}).get('head_sha')!=src['sha'] for x in items)):
            raise ValueError('GITHUB_ARTIFACTS_MISSING_OR_STALE')
        names={x['name'] for x in items}
        suffix='-attempt1'
        if label=='host':
            if names!={x+suffix for x in LANES}:raise ValueError('HOST_ARTIFACT_NAMES_INVALID')
        elif names!={'devtool-full-pytest-summary',*[f'devtool-full-shard-{i}' for i in range(1,25)]}:
            raise ValueError('G1_ARTIFACT_NAMES_INVALID')
        outcome[label]={'run_id':run_id,'jobs':rows,'artifacts':items}
    commit=client.json(f'/repos/{REPO}/commits/{src["sha"]}')
    if commit.get('commit',{}).get('tree',{}).get('sha')!=src['tree']:
        raise ValueError('GITHUB_GIT_TREE_MISMATCH')
    return outcome

def review(client,host_run:int,g1_run:int,source:dict,archive_dir:Path, *, fetch_archives:bool):
    metadata=verify_provider_runs(client,host_run,g1_run,source)
    evidence=[]
    for lane,run in metadata.items():
        for item in run['artifacts']:
            name=item['name'];archive=archive_dir/(name+'.zip')
            if fetch_archives:
                raw=client.get(f'/repos/{REPO}/actions/artifacts/{item["id"]}/zip')
                archive.parent.mkdir(parents=True,exist_ok=True)
                archive.write_bytes(raw)
            else:
                if archive.is_symlink() or not archive.is_file():raise ValueError('MISSING_OR_UNSAFE_LOCAL_ARCHIVE')
                raw=archive.read_bytes()
            valid=verify_archive(raw,item)
            if lane=='host':
                expected_json='host-summary.json' if name.startswith('fc08-real-host-summary') else name.removeprefix('fc08-real-').removesuffix('-attempt1')+'.json'
                if not any(Path(x).name==expected_json for x in valid['json_entries']):
                    raise ValueError('HOST_RECEIPT_MISSING')
                for filename,content in valid['json_payloads'].items():
                    payload=json.loads(content)
                    if payload.get('release_qualified') is not False:
                        raise ValueError('HOST_RELEASE_FLAG_UNSAFE')
                    identity=payload.get('identity',{})
                    if identity.get('sha')!=source['sha'] or identity.get('attempt')!=1 or identity.get('run_id')!=host_run:
                        raise ValueError('HOST_RECEIPT_SOURCE_MISMATCH')
            evidence.append({'name':name,'github_artifact_id':item['id'],'archive_sha256':valid['archive_sha256'],
                             'archive_members':valid['members'],'run_id':run['run_id']})
    return {'schema':SCHEMA,'source':source,'reviewed_archives':evidence,
            'archive_count':len(evidence),'all_provider_zip_digests_match':True,
            'hosted_jobs_passed':5,'g1_jobs_passed':25,
            'g1_junit_independently_recounted':False,
            'raw_windows_trx_coverage_and_signed_binary_verified':False,
            'native_termux_qualified':False,'consumer_qualified':False,
            'fc08_overall_qualified':False,'release_qualified':False,
            'qualification':'INTEGRITY_REVIEW_ONLY_NO_RELEASE'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path.cwd());ap.add_argument('--host-run',type=int,required=True)
    ap.add_argument('--g1-run',type=int,required=True);ap.add_argument('--archive-dir',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--fetch-archives',action='store_true')
    args=ap.parse_args()
    try:
        token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if not token:
            p=subprocess.run(['gh','auth','token'],capture_output=True,text=True,timeout=12)
            token=p.stdout.strip() if p.returncode==0 else None
        client=Client(token)
        result=review(client,args.host_run,args.g1_run,local_source(args.root),args.archive_dir,
                      fetch_archives=args.fetch_archives)
        if args.out.is_symlink():raise ValueError('UNSAFE_OUTPUT')
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    except (OSError,ValueError,HTTPError,URLError,subprocess.SubprocessError,zipfile.BadZipFile,json.JSONDecodeError) as exc:
        print('BLOCKED_FC08_R1R28_EVIDENCE_'+type(exc).__name__,file=sys.stderr)
        return 2
    print('PASS_FC08_R1R28_HOST_G1_ARCHIVE_INTEGRITY_NOT_RELEASE')
    return 0
if __name__=='__main__':sys.exit(main())
