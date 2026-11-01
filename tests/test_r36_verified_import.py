"""R36 R2: reviewed real-consumer two-archive authenticated import contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from devtool.actions_r36_verified_import import (
    PROFILES, DEVTOOL_SHA, select, validate_chain, import_exact_run,
    inspect_local,
)
from devtool.github_actions import GitHubActionsError
from devtool.artifact_registry import load_registry

SOURCE = 'a'*40
SIGNER = 'b'*64
RID = 41272202977
APK = b'not-an-installable-apk-unique-test-synthetic'*100
APKSHA = hashlib.sha256(APK).hexdigest()
GRAPH = 'c'*64
WORKFLOW_HASH = 'd'*64


def make_zip(filename: Path, data: dict):
    with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        for name, value in data.items():
            out.writestr(name, json.dumps(value, sort_keys=True) if isinstance(value, dict) else value)


def fixture(tmp_path: Path, profile='mmrl'):
    cfg=PROFILES[profile]
    package=cfg['package'] or 'io.github.test.dustui'
    plan={'profile':profile, 'repository':cfg['repository'],'workflow':cfg['workflow'],
          'workflow_id':22, 'run_id':RID,'attempt':1,'source_sha':SOURCE,
          'signer_sha256':SIGNER,'package':package, 'expected_apk_sha256':APKSHA,
          'artifacts':{'apk':{'id':101,'digest':'sha256:'+'f'*64},
                       'evidence':{'id':102,'digest':'sha256:'+'e'*64}}}
    path='payload/'+cfg['output']
    receipt={'schema_version':2,'kind':'devtool.actions.node-handoff',
            'source_commit':SOURCE,'attempt':1,'run_id':str(RID),'producer_job':'node-01',
            'producer_node':cfg['producer_node'], 'graph_sha256':GRAPH,
            'workflow_sha256':WORKFLOW_HASH,'authenticated':False,
            'release_qualified':False,'files':[{'path':cfg['output'],'sha256':APKSHA,'size':len(APK)}]}
    plan_doc={'schema':cfg['plan_schema'],'status':cfg['plan_status'],
              'source_sha':SOURCE,'devtool_sha':DEVTOOL_SHA,
              'graph_sha256':GRAPH,'output':cfg['output'],'tasks':cfg['tasks'],
              'release_qualified':False,'device_qualified':False}
    if profile=='mmrl':
        plan_doc.update(package=package,artifact_independently_authenticated=False)
    else:
        plan_doc.update(authenticated_artifact=False)
    evidence={'plan.json':plan_doc}
    if profile=='mmrl':
        evidence['verification.json']={
            'schema':cfg['verification_schema'],'status':'VERIFIED_DEBUG_NON_RELEASE',
            'source_sha':SOURCE,'run_id':RID,'attempt':1,'devtool_sha':DEVTOOL_SHA,
            'graph_sha256':GRAPH,'tasks':cfg['tasks'],'output':cfg['output'],
            'package':package,'apk_sha256':APKSHA,'apk_size':len(APK),
            'signer_sha256':SIGNER,'signature_independently_verified':True,
            'package_independently_verified':True,
            'artifact_independently_authenticated':False,
            'release_qualified':False,'device_qualified':False}
    apk_zip=tmp_path/'apk.zip';evidence_zip=tmp_path/'evidence.zip'
    make_zip(apk_zip,{'receipt.json':receipt,path:APK})
    make_zip(evidence_zip,evidence)
    return plan, {'apk':apk_zip,'evidence':evidence_zip}


class Client:
    def __init__(self, profile='mmrl', source=SOURCE, attempt=1, conclusion='success', event='workflow_dispatch'):
        self.profile=profile;self.source=source;self.attempt=attempt
        self.conclusion=conclusion;self.event=event
    def get_run(self, slug, run):
        return {'id':RID,'status':'completed','repository':{'full_name':PROFILES[self.profile]['repository']},
                'conclusion':self.conclusion,'event':self.event,'workflow_id':22,
                'head_sha':self.source,'run_attempt':self.attempt}
    def workflow(self,slug,wf):return {'id':22}
    def run_artifacts(self,slug,run):
        cfg=PROFILES[self.profile]
        return [{'id':i,'name':cfg[name+'_role']+'-attempt1',
                 'size_in_bytes':900,'digest':'sha256:'+'d'*64,'expired':False,
                 'workflow_run':{'id':RID,'head_sha':self.source}}
                for i,name in ((101,'apk'),(102,'evidence'))]


@pytest.mark.parametrize('profile',list(PROFILES))
def test_profile_archive_chain(profile,tmp_path):
    plan,archives=fixture(tmp_path,profile)
    out=validate_chain(plan,archives)
    assert out['apk_sha256']==APKSHA
    assert out['graph_sha256']==GRAPH
    assert out['release_qualified'] is False


@pytest.mark.parametrize('profile',list(PROFILES))
def test_remote_admission_is_source_attempt_workflow_and_signer_pinned(profile):
    p=PROFILES[profile];package=p['package'] or 'io.github.test.dustui'
    out=select(Client(profile),profile,p['repository'],RID,1,SOURCE,SIGNER,package,APKSHA)
    assert out['artifacts']['apk']['id']==101
    for client in (Client(profile,attempt=2),Client(profile,source='f'*40),
                   Client(profile,conclusion='failure'),Client(profile,event='push')):
        with pytest.raises(GitHubActionsError):
            select(client,profile,p['repository'],RID,1,SOURCE,SIGNER,package,APKSHA)
    for broken in ('','e'*63):
        with pytest.raises(GitHubActionsError):
            select(Client(profile),profile,p['repository'],RID,1,SOURCE,broken,package,APKSHA)
    with pytest.raises(GitHubActionsError):
        select(Client(profile),profile,p['repository']+'-fork',RID,1,SOURCE,SIGNER,package,APKSHA)


@pytest.mark.parametrize('profile',list(PROFILES))
@pytest.mark.parametrize('role,member,key,value',[
    ('apk','receipt.json','run_id','123'),
    ('apk','receipt.json','producer_job','node-02'),
    ('apk','receipt.json','source_commit','f'*40),
    ('apk','receipt.json','authenticated',True),
    ('evidence','plan.json','tasks',[':app:assembleRelease']),
    ('evidence','plan.json','devtool_sha','f'*40),
    ('evidence','plan.json','release_qualified',True),
])
def test_provenance_and_dag_tamper_rejected(profile,role,member,key,value,tmp_path):
    p,zips=fixture(tmp_path,profile)
    with zipfile.ZipFile(zips[role]) as z:
        members={name:z.read(name) for name in z.namelist()}
    obj=json.loads(members[member]);obj[key]=value;members[member]=json.dumps(obj).encode()
    make_zip(zips[role],members)
    with pytest.raises(GitHubActionsError):validate_chain(p,zips)


def test_mmrl_producer_signature_claim_mismatch_rejected(tmp_path):
    p,zips=fixture(tmp_path)
    with zipfile.ZipFile(zips['evidence']) as z:members={n:z.read(n) for n in z.namelist()}
    receipt=json.loads(members['verification.json']);receipt['signer_sha256']='e'*64
    members['verification.json']=json.dumps(receipt)
    make_zip(zips['evidence'],members)
    with pytest.raises(GitHubActionsError):validate_chain(p,zips)


@pytest.mark.parametrize('profile',list(PROFILES))
def test_modified_apk_bytes_and_unreviewed_members_rejected(profile,tmp_path):
    p,zips=fixture(tmp_path,profile);path='payload/'+PROFILES[profile]['output']
    with zipfile.ZipFile(zips['apk']) as z:members={n:z.read(n) for n in z.namelist()}
    members[path]=APK+b'evil';make_zip(zips['apk'],members)
    with pytest.raises(GitHubActionsError):validate_chain(p,zips)
    p,zips=fixture(tmp_path,profile)
    with zipfile.ZipFile(zips['apk']) as z:members={n:z.read(n) for n in z.namelist()}
    members['../unexpected.apk']=b'evil';make_zip(zips['apk'],members)
    with pytest.raises(GitHubActionsError):validate_chain(p,zips)


def test_import_uses_existing_inspector_and_registry_and_is_idempotent(tmp_path,monkeypatch):
    from devtool import actions_r36_verified_import as mod
    p,zips=fixture(tmp_path)
    monkeypatch.setattr(mod,'_verified_or_download',lambda client,plan,role,root:{'file':str(zips[role])})
    def sdk(client,slug,root,**kwargs):
        assert kwargs['materialize'] is False and kwargs['download'] is False
        assert kwargs['variant']=='debug' and kwargs['member']=='payload/'+PROFILES['mmrl']['output']
        assert kwargs['expected_source_sha']==SOURCE and kwargs['expected_run_attempt']==1
        return {'android':{'sha256':APKSHA,'size':len(APK),'package':p['package'],
                           'signer_sha256':SIGNER,'signature_verified':True,'manifest_verified':True}}
    monkeypatch.setattr(mod,'android_collect',sdk)
    result=import_exact_run(Client(),tmp_path,p)
    assert result['status']=='IMPORTED_VERIFIED_DEBUG'
    assert result['remote_actions_artifact_authenticated'] is True
    assert result['release_qualified'] is False
    assert len(load_registry(tmp_path)['items'])==1
    assert inspect_local(tmp_path,p)['local_bytes_reverified'] is True
    assert import_exact_run(Client(),tmp_path,p)['registry_id']==result['registry_id']
    assert len(load_registry(tmp_path)['items'])==1
    # A different pinned archive identity may not silently inherit imported trust.
    wrong={**p,'artifacts':{**p['artifacts'],'apk':{**p['artifacts']['apk'],'id':999}}}
    with pytest.raises(GitHubActionsError):inspect_local(tmp_path,wrong)


def test_reject_signature_failing_inspector_without_imported_receipt(tmp_path,monkeypatch):
    from devtool import actions_r36_verified_import as mod
    p,zips=fixture(tmp_path)
    monkeypatch.setattr(mod,'_verified_or_download',lambda c,p,r,root:{'file':str(zips[r])})
    monkeypatch.setattr(mod,'android_collect',lambda *a,**k:{'android':{
        'sha256':APKSHA,'size':len(APK),'package':p['package'],
        'signer_sha256':'e'*64,'signature_verified':True,'manifest_verified':True}})
    with pytest.raises(GitHubActionsError):import_exact_run(Client(),tmp_path,p)
    assert not load_registry(tmp_path)['items']


def test_existing_r35_interface_exposes_r36_without_interfering_with_r34():
    from devtool import actions_r35_verified_import as r35
    import inspect
    source=inspect.getsource(r35.main)
    assert "if '--layout' in sys.argv" in source
    assert "sys.argv[idx + 1] != 'r36'" in source
    assert 'r36_main' in source
    assert 'p.add_argument(\'command\', choices=(\'plan\', \'import\', \'inspect\'))' in source
