"""G1 artifact guards; no network and no test-signing credentials."""
from __future__ import annotations
import hashlib
import io
import os
from pathlib import Path
import subprocess
import zipfile

import pytest
from devtool.actions_artifacts import _metadata, check_zip, download_artifact, verify_download
from devtool.artifact_registry import load_registry
from devtool.github_actions import GitHubActionsError

REPO='owner/repo';SHA='a'*40

class OfflineClient:
    def __init__(self, archive: bytes, attempt: int = 1, artifact_attempt=None):
        self.archive=archive
        self.attempt=attempt
        self.artifact_attempt=artifact_attempt
        self._opener=self
        self.timeout=2
        self.requests=[]
    def get_run(self, repo, identity):
        return {'id':identity,'repository':{'full_name':repo},'status':'completed',
                'conclusion':'success','head_sha':SHA,'run_attempt':self.attempt,'workflow_id':77}
    def get_artifact(self, repo, identity):
        parent={'id':50,'head_sha':SHA}
        if self.artifact_attempt is not None:parent['run_attempt']=self.artifact_attempt
        return {'id':identity,'name':'test','workflow_run':parent,
                'size_in_bytes':len(self.archive),'expired':False,
                'digest':'sha256:'+hashlib.sha256(self.archive).hexdigest()}
    def artifact_download_location(self, repo, identity):
        return 'https://results.actions.githubusercontent.com/test?sig=redacted'
    def open(self, request, timeout):
        assert request.get_header('Authorization') is None
        self.requests.append(request)
        class Stream:
            status=200
            def __init__(self, blob):self._io=io.BytesIO(blob)
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,size):return self._io.read(size)
        return Stream(self.archive)


def archive(files=(('dist/result.bin',b'ok'),)):
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,'w') as z:
        for name, data in files:z.writestr(name,data)
    return buff.getvalue()


def repo(tmp_path):
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    return tmp_path


@pytest.mark.parametrize('name',['a//b','a/./b','a///b','a/../b','./a','a\\b'])
def test_zip_aliases_fail_closed(tmp_path,name):
    file=tmp_path/'artifact.zip'
    file.write_bytes(archive(((name,b'ok'),)))
    with pytest.raises(GitHubActionsError):check_zip(file)


def test_case_distinct_generic_archive_is_conservative(tmp_path):
    path=tmp_path/'artifact.zip'
    path.write_bytes(archive((('Foo.txt',b'a'),('foo.txt',b'b'))))
    with pytest.raises(GitHubActionsError,match='duplicate'):
        check_zip(path)


def test_artifact_api_attempt_mismatch_blocks_before_download(tmp_path):
    data=archive()
    c=OfflineClient(data,attempt=2,artifact_attempt=1)
    with pytest.raises(GitHubActionsError,match='attempt'):
        download_artifact(c,REPO,50,5,repo(tmp_path))
    assert c.requests==[]
    assert not load_registry(tmp_path)['items']


def test_artifact_attempt_change_invalidates_registered_archive(tmp_path):
    data=archive()
    c=OfflineClient(data,attempt=1)
    dest=repo(tmp_path)
    result=download_artifact(c,REPO,50,5,dest)
    assert Path(result['file']).is_file()
    assert result['qualified_release'] is False
    assert result['zip_validation']['members']==1
    assert verify_download(c,REPO,50,5,dest)['archive_verified']
    c.attempt=2
    with pytest.raises(GitHubActionsError,match='different workflow attempt'):
        verify_download(c,REPO,50,5,dest)


def test_artifact_metadata_does_not_invent_missing_attempt():
    remote={'id':50,'run_attempt':3,'head_sha':SHA}
    raw={'id':5,'name':'test','workflow_run':{'id':50,'head_sha':SHA},
         'size_in_bytes':1,'expired':False,'digest':'sha256:'+('f'*64)}
    assert _metadata(raw,REPO,remote)['run_attempt']==3
    raw['workflow_run']['run_attempt']=3
    assert _metadata(raw,REPO,remote)['run_attempt']==3
    raw['workflow_run']['run_attempt']=2
    with pytest.raises(GitHubActionsError,match='attempt'):
        _metadata(raw,REPO,remote)
