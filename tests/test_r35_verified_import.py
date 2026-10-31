"""R35 targeted import, source/run/attempt, archive and idempotency negatives."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from devtool.actions_r35_verified_import import (
    select, validate_chain, import_exact_run, inspect_local, APK_NAME,
)
from devtool.github_actions import GitHubActionsError
from devtool.artifact_registry import load_registry

SHA='a'*40
FIXTURE='b'*40
GRAPH='c'*64
SIGNER='d'*64
PACKAGE='org.devtool.r33.fixture'
RID=41230694663
ATT=1
APK=b'validated-fixture-apk-bytes-not-for-installation'*32
DIGEST=hashlib.sha256(APK).hexdigest()


def plan():
    return {'repository':'Mikeyphw/devtool','run_id':RID,'attempt':ATT,
            'source_sha':SHA,'workflow': 'devtool-android-r34-generated-hosted-dag.yml',
            'workflow_id':79,'package':PACKAGE,'signer_sha256':SIGNER,
            'artifacts':{name:{'id':idx,'digest':'sha256:'+str(idx)*64}
              for idx,name in enumerate(('node-01-evidence','node-02-evidence','debug-apk'),1)}}


def write_zip(path:Path, mapping:dict):
    with zipfile.ZipFile(path,'w') as z:
        for k,v in mapping.items():
            z.writestr(k,json.dumps(v,sort_keys=True) if isinstance(v,dict) else v)


def fixtures(tmp_path):
    p=plan()
    files={}
    source={'devtool_sha':SHA,'fixture_sha':FIXTURE}
    jobs=[{'id':'node-01','needs':[], 'node':'lint','provider':'android-gradle','outputs':[]},
          {'id':'node-02','needs':['node-01'],'node':'build','provider':'android-gradle',
            'outputs':['app/build/outputs/apk/debug/app-debug.apk']}]
    dag={'devtool_sha':SHA,'fixture_sha':FIXTURE,'graph_sha256':GRAPH,
         'jobs':jobs,'release_qualified':False,'device_qualified':False}
    for kind, node in (('lint','node-01'),('build','node-02')):
        result={'kind':'devtool.r33.live-node.v1','node':kind,'status':'PASS',
                'provider':'android-gradle','devtool_sha':SHA,'github_run_id':str(RID),
                'github_run_attempt':ATT,'fixture_sha':FIXTURE,'graph_sha256':GRAPH,
                'actual_gradle_execution':True,'native_dag_executed':True,
                'release_qualified':False,'device_qualified':False,
                'node_result':{'status':'LOCAL_JOB_SUCCEEDED','job_id':node,
                               'github_source':FIXTURE,'graph_sha256':GRAPH,
                               'run_id':str(RID),'attempt':ATT}}
        entries={'bootstrap.json':{'schema':'devtool.r34.bootstrap.v1',
                    'source_sha':SHA,'run_id':str(RID),'run_attempt':ATT,
                    'job':kind,'status':'NOT_QUALIFIED','release_qualified':False},
                 'source.json':source,'plan.json':dag,'result.json':result}
        if kind=='build':
            result['apk']={'package':PACKAGE,'variant':'debug','path':'app/build/outputs/apk/debug/app-debug.apk',
                           'sha256':DIGEST,'size':len(APK),'signer_certificate_sha256':SIGNER,
                           'debug_signed':True,'signing_release_authorized':False}
            entries['native-outputs/node-02/receipt.json']={'source_commit':FIXTURE,'graph_sha256':GRAPH,
                'run_id':str(RID),'attempt':ATT,'files':[{'path':result['apk']['path'],
                'sha256':DIGEST,'size':len(APK)}]}
            snap={'schema_version':2,'items':[{'id':'record-hosted','sha256':DIGEST}]}
            snap_bytes=json.dumps(snap,sort_keys=True).encode()
            entries['registry-snapshot.json']=snap_bytes
            entries['registry-evidence.json']={'schema':'devtool.r34.hosted-registry.v1',
              'status':'PASS_LOCAL_REGISTRY_VERIFIED','devtool_sha':SHA,
              'github_run_id':str(RID),'github_run_attempt':ATT,
              'fixture_sha':FIXTURE,'graph_sha256':GRAPH,'apk_sha256':DIGEST,
              'apk_size':len(APK),'package':PACKAGE,'signer_sha256':SIGNER,
              'local_registry_registered':True,'local_registry_verified':True,
              'remote_actions_artifact_authenticated':False,'release_qualified':False,
              'device_qualified':False,'record_id':'record-hosted',
              'registry_sha256':hashlib.sha256(snap_bytes).hexdigest()}
        path=tmp_path/(node+'.zip');write_zip(path,entries)
        files['node-01-evidence' if kind=='lint' else 'node-02-evidence']=path
    apk=tmp_path/'apk.zip';write_zip(apk,{APK_NAME:APK});files['debug-apk']=apk
    return p,files


class Client:
    def __init__(self,attempt=1,conclusion='success',source=SHA):
        self.attempt=attempt;self.conclusion=conclusion;self.source=source
    def get_run(self,slug,rid):
        return {'id':RID,'repository':{'full_name':'Mikeyphw/devtool'},
                'status':'completed','conclusion':self.conclusion,
                'event':'workflow_dispatch','workflow_id':79,
                'head_sha':self.source,'run_attempt':self.attempt}
    def workflow(self,slug,wf):return {'id':79}
    def run_artifacts(self,slug,rid):
        return [{'id':idx,'name':'devtool-r34-'+role+'-attempt1','size_in_bytes':1600,
                'digest':'sha256:'+'e'*64,'workflow_run':{'id':RID,'head_sha':self.source}}
                for idx,role in enumerate(('node-01-evidence','node-02-evidence','debug-apk'),1)]


def test_exact_run_read_only_admission_and_no_guessing():
    s=select(Client(),'Mikeyphw/devtool',RID,ATT,SHA,SIGNER)
    assert [s['artifacts'][r]['id'] for r in ('node-01-evidence','node-02-evidence','debug-apk')]==[1,2,3]
    for c in (Client(attempt=2),Client(conclusion='failure'),Client(source='f'*40)):
        with pytest.raises(GitHubActionsError): select(c,'Mikeyphw/devtool',RID,ATT,SHA,SIGNER)
    with pytest.raises(GitHubActionsError):select(Client(),'Mikeyphw/devtool',RID,ATT,SHA,'')


def test_exact_run_cross_role_validation(tmp_path):
    p,files=fixtures(tmp_path)
    a=validate_chain(p,files)
    assert a['apk_sha256']==DIGEST and a['graph_sha256']==GRAPH
    assert a['release_qualified'] is False


@pytest.mark.parametrize('role,member,key,value',[
 ('node-01-evidence','bootstrap.json','run_attempt',2),
 ('node-02-evidence','result.json','devtool_sha','f'*40),
 ('node-02-evidence','result.json','graph_sha256','f'*64),
 ('node-02-evidence','registry-evidence.json','signer_sha256','e'*64),
 ('node-02-evidence','registry-evidence.json','remote_actions_artifact_authenticated',True),
 ('node-01-evidence','result.json','status','FAIL'),
 ('node-02-evidence','registry-evidence.json','release_qualified',True),
])
def test_cross_role_tampering_fails_closed(tmp_path,role,member,key,value):
    p,files=fixtures(tmp_path)
    with zipfile.ZipFile(files[role]) as z:
        d={n:z.read(n) for n in z.namelist()}
    obj=json.loads(d[member]);obj[key]=value;d[member]=json.dumps(obj).encode()
    write_zip(files[role],d)
    with pytest.raises(GitHubActionsError):validate_chain(p,files)


def test_standalone_apk_tamper_and_path_refused(tmp_path):
    p,files=fixtures(tmp_path)
    write_zip(files['debug-apk'],{'app-debug.apk':APK+b'evil'})
    with pytest.raises(GitHubActionsError):validate_chain(p,files)
    write_zip(files['debug-apk'],{'../app-debug.apk':APK})
    with pytest.raises(GitHubActionsError):validate_chain(p,files)


def test_import_integrates_existing_collect_and_registry_and_is_idempotent(tmp_path,monkeypatch):
    from devtool import actions_r35_verified_import as mod
    p,files=fixtures(tmp_path)
    monkeypatch.setattr(mod,'_verified_or_download',lambda client,plan,role,root:{'file':str(files[role])})
    def inspect(client,slug,root,**kwargs):
        assert kwargs['materialize'] is False
        assert kwargs['expected_cert']==SIGNER
        assert kwargs['expected_source_sha']==SHA
        return {'android':{'sha256':DIGEST,'size':len(APK),'package':PACKAGE,
                           'signer_sha256':SIGNER,'signature_verified':True,
                           'manifest_verified':True}}
    monkeypatch.setattr(mod,'android_collect',inspect)
    out=import_exact_run(Client(),tmp_path,p)
    assert out['status']=='IMPORTED_VERIFIED_DEBUG'
    assert out['remote_actions_artifact_authenticated'] is True
    assert out['release_qualified'] is False
    assert len(load_registry(tmp_path)['items'])==1
    assert inspect_local(tmp_path,p)['local_bytes_reverified'] is True
    assert import_exact_run(Client(),tmp_path,p)['registry_id']==out['registry_id']
    assert len(load_registry(tmp_path)['items'])==1


def test_inspection_failure_never_registers_apk(tmp_path,monkeypatch):
    from devtool import actions_r35_verified_import as mod
    p,files=fixtures(tmp_path)
    monkeypatch.setattr(mod,'_verified_or_download',lambda *args:{'file':str(files[args[2]])})
    monkeypatch.setattr(mod,'android_collect',lambda *args,**kwargs:{'android':{
       'sha256':DIGEST,'size':len(APK),'package':PACKAGE,'signer_sha256':'e'*64,
       'signature_verified':True,'manifest_verified':True}})
    with pytest.raises(GitHubActionsError):import_exact_run(Client(),tmp_path,p)
    assert not load_registry(tmp_path)['items']
