"""ACTIONS-04: artifact/run identity, token-isolated redirects, ZIP trust and registry."""
from __future__ import annotations
import hashlib, io, json, os, stat, subprocess, zipfile
from pathlib import Path
from urllib.error import HTTPError
from types import SimpleNamespace
import pytest
from devtool.github_actions import GitHubActionsClient, GitHubActionsError, actions_cli, resolve_repository
from devtool.actions_artifacts import artifact_list, download_artifact, verify_download, check_zip, _signed_host
from devtool.artifact_registry import load_registry
from devtool.cli import build_parser

SLUG='owner/repo'; RID=225; AID=77; SHA='a'*40

class Response:
    status=200
    headers={}
    def __init__(self, value):self.io=io.BytesIO(json.dumps(value).encode() if isinstance(value,dict) else value)
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return self.io.read(n)
class Opener:
    def __init__(self,*items):self.items=list(items);self.requests=[]
    def open(self,req,timeout):
        self.requests.append(req)
        if not self.items:raise AssertionError('unplanned API request')
        item=self.items.pop(0)
        if isinstance(item,Exception):raise item
        return item

def remote():
    return {'id':RID,'repository':{'full_name':SLUG},'status':'completed','conclusion':'success',
            'head_sha':SHA,'run_attempt':2,'workflow_id':99}
def archive():
    # The same fixture is generated separately for download and verification.
    # ZipFile.writestr(str, ...) otherwise adds wall-clock timestamps, changing
    # SHA-256 if the calls straddle a ZIP two-second timestamp boundary.
    blob = io.BytesIO()
    entry = zipfile.ZipInfo('build/app-debug.apk', date_time=(2020, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(blob, 'w') as archive:
        archive.writestr(entry, b'APK SAMPLE')
    return blob.getvalue()


def test_artifact_fixture_is_stable_across_time(monkeypatch):
    # Simulate the original source of flakiness without sleeping for a boundary.
    import time
    monkeypatch.setattr(time, 'time', lambda: 1_700_000_000)
    first = archive()
    monkeypatch.setattr(time, 'time', lambda: 1_700_000_061)
    second = archive()
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
def artifact(data=None):
    b=archive() if data is None else data
    return {'id':AID,'name':'android-debug','workflow_run':{'id':RID,'head_sha':SHA},
            'size_in_bytes':len(b),'digest':'sha256:'+hashlib.sha256(b).hexdigest(),
            'expired':False}
def redirect(url='https://results.actions.githubusercontent.com/random?signature=secret'):
    return HTTPError(f'https://api.github.com/repos/{SLUG}/actions/artifacts/{AID}/zip',302,'Found',{'Location':url},None)
def repo(tmp_path):
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    subprocess.run(['git','-C',str(tmp_path),'config','user.name','Test'],check=True)
    subprocess.run(['git','-C',str(tmp_path),'config','user.email','test@example.invalid'],check=True)
    return tmp_path

def test_detect_repo_from_current_git_origin(tmp_path):
    repo(tmp_path)
    subprocess.run(['git','-C',str(tmp_path),'remote','add','origin','git@github.com:Mikeyphw/skybind.git'],check=True)
    assert resolve_repository('',tmp_path)=='Mikeyphw/skybind'
    assert resolve_repository('owner/override',tmp_path)=='owner/override'

def test_new_parser_uses_auto_origin_no_slug_required():
    for command in ('artifacts','download','verify'):
        args=['actions',command,'--run-id',str(RID)]
        if command!='artifacts':args+=['--artifact-id',str(AID)]
        obj=build_parser().parse_args(args)
        assert obj.github_repo=='' and obj.actions_command==command

def test_run_artifact_list_rejects_other_run_or_source():
    good=artifact()
    op=Opener(Response(remote()),Response({'artifacts':[good]}))
    out=artifact_list(GitHubActionsClient(opener=op),SLUG,RID)
    assert out['artifacts'][0]['head_sha']==SHA
    assert '/actions/runs/225/artifacts?' in op.requests[1].full_url
    bad=dict(good, workflow_run={'id':RID+1,'head_sha':SHA})
    with pytest.raises(GitHubActionsError,match='bound'):
        artifact_list(GitHubActionsClient(opener=Opener(Response(remote()),Response({'artifacts':[bad]}))),SLUG,RID)
    bad=dict(good, workflow_run={'id':RID,'head_sha':'b'*40})
    with pytest.raises(GitHubActionsError,match='commit'):
        artifact_list(GitHubActionsClient(opener=Opener(Response(remote()),Response({'artifacts':[bad]}))),SLUG,RID)

def test_download_registers_verified_archive_and_no_credentials_on_signed_url(tmp_path):
    b=archive();op=Opener(Response(remote()),Response(artifact()),redirect(),Response(b))
    result=download_artifact(GitHubActionsClient('SENSITIVE-TOKEN',opener=op),SLUG,RID,AID,repo(tmp_path))
    assert result['download_verified'] and not result['qualified_release'] and not result['extracted']
    assert Path(result['file']).read_bytes()==b
    assert (Path(result['file']).stat().st_mode & 0o777)==0o600
    assert op.requests[2].get_header('Authorization')=='Bearer SENSITIVE-TOKEN'
    assert op.requests[3].get_header('Authorization') is None
    assert 'signature' not in json.dumps(result)
    records=load_registry(tmp_path)['items']; assert len(records)==1
    assert records[0]['metadata']['remote_head_sha']==SHA
    assert records[0]['metadata']['remote_run_id']==RID
    assert records[0]['metadata']['state']=='downloaded-unqualified'
    assert records[0]['metadata']['qualified_release'] is False
    out=verify_download(GitHubActionsClient(opener=Opener(Response(remote()),Response(artifact()))),SLUG,RID,AID,tmp_path)
    assert out['archive_verified'] and not out['qualified_release']
    with pytest.raises(GitHubActionsError,match='already downloaded'):
        download_artifact(GitHubActionsClient(opener=Opener(Response(remote()),Response(artifact()),redirect(),Response(b))),SLUG,RID,AID,tmp_path)

def test_mismatch_is_discarded_and_not_registered(tmp_path):
    op=Opener(Response(remote()),Response(artifact()),redirect(),Response(b'not original'))
    with pytest.raises(GitHubActionsError,match='sha256 mismatch'):
        download_artifact(GitHubActionsClient(opener=op),SLUG,RID,AID,repo(tmp_path))
    assert not load_registry(tmp_path)['items']
    assert not list((tmp_path/'.devtool/artifacts/github-downloads').glob('*'))

def test_trust_boundaries_before_download(tmp_path):
    cases=[dict(artifact(),expired=True),dict(artifact(),digest=None),
           dict(artifact(),workflow_run={'id':RID+1,'head_sha':SHA})]
    for bad in cases:
        op=Opener(Response(remote()),Response(bad))
        with pytest.raises(GitHubActionsError):
            download_artifact(GitHubActionsClient(opener=op),SLUG,RID,AID,repo(tmp_path))
        assert len(op.requests)==2

@pytest.mark.parametrize('unsafe',['https://evil.example/a','http://results.actions.githubusercontent.com/a',
 'https://username:pass@results.actions.githubusercontent.com/a',
 'https://results.actions.githubusercontent.com:444/a',
 'https://objects.githubusercontent.com.evil.org/a'])
def test_redirect_host_allowlist(unsafe):
    assert not _signed_host(unsafe)
    assert _signed_host('https://resultssample.blob.core.windows.net/abc?sig=SENSITIVE')

def test_redirect_is_never_followed_with_authorization():
    op=Opener(redirect('https://attacker.example/data'))
    with pytest.raises(GitHubActionsError,match='Untrusted'):
        from devtool.actions_artifacts import _download_blob
        _download_blob(GitHubActionsClient('s',opener=op),'https://attacker.example/data',Path('/not-used'))
    assert not op.requests

def test_zip_traversal_symlink_duplicate_and_crc(tmp_path):
    for filename in ('../esc.txt','/abs.txt','C:/evil','dir\\evil'):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr(filename,'x')
        target=tmp_path/'unsafe.zip';target.write_bytes(b.getvalue())
        with pytest.raises(GitHubActionsError):check_zip(target)
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        i=zipfile.ZipInfo('link');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(i,'target')
    target.write_bytes(b.getvalue())
    with pytest.raises(GitHubActionsError,match='symbolic'):check_zip(target)
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('a.txt','one');z.writestr('a.txt','two')
    target.write_bytes(b.getvalue())
    with pytest.raises(GitHubActionsError,match='duplicate'):check_zip(target)

def test_registered_archive_tampering_fails_closed(tmp_path):
    b=archive();r=repo(tmp_path)
    result=download_artifact(GitHubActionsClient(opener=Opener(Response(remote()),Response(artifact()),redirect(),Response(b))),SLUG,RID,AID,r)
    Path(result['file']).write_bytes(b'corruption')
    with pytest.raises(GitHubActionsError,match='integrity'):
        verify_download(GitHubActionsClient(opener=Opener(Response(remote()),Response(artifact()))),SLUG,RID,AID,r)
