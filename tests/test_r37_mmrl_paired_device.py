"""Source-only contracts; no adb, network, root, Gradle or device mutation."""
from pathlib import Path
import json
import re
import sys
import types
import importlib.util
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'devtool/actions_r37_mmrl_device.py'


def load_module(monkeypatch):
    def install(name,**attrs):
        mod=types.ModuleType(name)
        for key,value in attrs.items():setattr(mod,key,value)
        monkeypatch.setitem(sys.modules,name,mod)
        return mod
    install('devtool',__path__=[str(ROOT/'devtool')])
    def deny(msg):raise ValueError(msg)
    def requires(value,expected,label):
        if not isinstance(value,dict) or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('mismatch '+label)
    def json_member(z,p):return json.loads(z.read(p))
    install('devtool.actions_r35_verified_import',_deny=deny,_json_member=json_member,
        _requires=requires,_verified_or_download=lambda *a:None,
        HEX40=re.compile(r'[a-f0-9]{40}\Z'),HEX64=re.compile(r'[a-f0-9]{64}\Z'))
    install('devtool.actions_artifacts',artifact_list=lambda *a:None,
        check_zip=lambda x:zipfile.ZipFile(x).testzip() is None)
    install('devtool.actions_android',android_collect=lambda *a,**k:None)
    install('devtool.artifact_registry',register_paths=lambda *a,**k:None,
        verify_record=lambda *a:None,registry_path=lambda p:p/'.devtool/artifacts/registry.json')
    install('devtool.github_actions',GitHubActionsClient=object,GitHubActionsError=ValueError,
        resolve_token=lambda x:(None,''),_slug=lambda x:x)
    spec=importlib.util.spec_from_file_location('devtool.actions_r37_mmrl_device',SOURCE)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_fixture(tmp_path, m):
    source='a'*40;digest='b'*64;sig='c'*64;graph='d'*64;workflow='e'*64
    app=b'fake APK app'  # Archive-chain tests operate on hashes, never pretend signing was checked.
    test=b'fake APK instrumentation'
    import hashlib
    app_sha=hashlib.sha256(app).hexdigest();test_sha=hashlib.sha256(test).hexdigest()
    run=345;attempt=1
    plan={'repository':m.REPO,'workflow':m.WORKFLOW,'source_sha':source,
          'run_id':run,'attempt':attempt,'app_sha256':app_sha,
          'test_sha256':test_sha,'signer_sha256':sig,
          'app_package':m.APP_PACKAGE,'test_package':m.TEST_PACKAGE}
    native={'kind':'devtool.actions.node-handoff','schema_version':2,'source_commit':source,
            'run_id':str(run),'attempt':attempt,'authenticated':False,'release_qualified':False,
            'graph_sha256':graph,'workflow_sha256':workflow,
            'files':[{'path':m.APP_MEMBER.removeprefix('payload/'),'sha256':app_sha,'size':len(app)}]}
    r36plan={'schema':'mmrl.r36.hosted-plan.v1','source_sha':source,'devtool_sha':m.DEVTOOL_SHA,
            'graph_sha256':graph,'release_qualified':False,'device_qualified':False}
    r36check={'schema':'mmrl.r36.hosted-verification.v1','status':'VERIFIED_DEBUG_NON_RELEASE',
              'source_sha':source,'run_id':run,'attempt':attempt,'apk_sha256':app_sha,
              'signer_sha256':sig,'graph_sha256':graph,'signature_independently_verified':True,
              'device_qualified':False,'release_qualified':False}
    pair={'schema':'mmrl.r37.hosted-pair.v1','status':'HOSTED_PAIR_VERIFIED_NON_DEVICE',
          'repository':m.REPO,'workflow':m.WORKFLOW,'source_sha':source,
          'run_id':run,'attempt':attempt,'devtool_sha':m.DEVTOOL_SHA,
          'app_test_compatible':True,'instrumentation_runner':m.TEST_PACKAGE+'/'+m.RUNNER,
          'target_package':m.APP_PACKAGE,'test_execution':'NOT_RUN',
          'remote_actions_artifact_authenticated':False,'device_qualified':False,
          'consumer_qualified':False,'release_qualified':False,
          'graph_sha256':graph,'r36_native_workflow_sha256':workflow,
          'app':{'package':m.APP_PACKAGE,'sha256':app_sha,'size':len(app),
                 'signer_sha256':sig,'signature_verified':True},
          'test':{'package':m.TEST_PACKAGE,'sha256':test_sha,'size':len(test),
                  'signer_sha256':sig,'signature_verified':True}}
    archives={x:tmp_path/f'{x}.zip' for x in m.ROLES}
    with zipfile.ZipFile(archives['app'],'w') as z:
        z.writestr(m.APP_MEMBER,app);z.writestr('receipt.json',json.dumps(native))
    with zipfile.ZipFile(archives['test'],'w') as z:z.writestr(m.TEST_MEMBER,test)
    with zipfile.ZipFile(archives['evidence'],'w') as z:
        for name,value in [('pair.json',pair),('r36-plan.json',r36plan),
            ('r36-verification.json',r36check),('r36-native-receipt.json',native)]:
            z.writestr(name,json.dumps(value))
    return plan,archives


def test_r37_source_does_not_invoke_gradle(monkeypatch):
    m=load_module(monkeypatch)
    source=SOURCE.read_text()
    assert 'gradlew' not in source
    assert '--skip-build' in source
    assert 'AndroidDeviceOperations.from_config' in source
    assert 'android_collect(' in source
    assert '_verified_or_download(' in source
    assert 'register_paths(' in source
    assert 'if any(preexisting.values()) and not allow_replace:' in source
    assert 'device_qualified' in source and 'release_qualified' in source
    assert m.TEST_PACKAGE.endswith('.debug.test')


def test_chain_accepts_single_exact_pair(tmp_path,monkeypatch):
    m=load_module(monkeypatch)
    plan,archives=make_fixture(tmp_path,m)
    got=m.validate_chain(plan,archives)
    assert got['app_sha256']==plan['app_sha256']
    assert got['test_sha256']==plan['test_sha256']
    assert got['device_qualified'] is False


def test_tampered_test_apk_rejected(tmp_path,monkeypatch):
    import pytest
    m=load_module(monkeypatch);plan,archives=make_fixture(tmp_path,m)
    with zipfile.ZipFile(archives['test'],'w') as z:z.writestr(m.TEST_MEMBER,b'substituted')
    with pytest.raises(ValueError,match='pinned SHA-256'):
        m.validate_chain(plan,archives)


def test_unreviewed_archive_member_rejected(tmp_path,monkeypatch):
    import pytest
    m=load_module(monkeypatch);plan,archives=make_fixture(tmp_path,m)
    with zipfile.ZipFile(archives['test'],'a') as z:z.writestr('unauthorized.txt',b'payload')
    with pytest.raises(ValueError,match='unexpected/duplicate'):
        m.validate_chain(plan,archives)


def test_wrong_source_and_weak_signer_rejected(tmp_path,monkeypatch):
    import pytest
    m=load_module(monkeypatch);plan,archives=make_fixture(tmp_path,m)
    with pytest.raises(ValueError,match='source SHA'):
        m.validate_chain({**plan,'source_sha':'f'*40},archives)
    with pytest.raises(ValueError,match='explicit exact'):
        m.validate_chain({**plan,'signer_sha256':'INVALID'},archives)


def test_stale_attempt_and_expired_artifact_rejected(monkeypatch):
    import pytest
    m=load_module(monkeypatch)
    class C:
        def workflow(self,*a):return {'id':12}
    c=C()
    row=lambda role:{'name':f'{m.ROLE_NAME[role]}-attempt1','run_id':33,
           'run_attempt':1,'head_sha':'a'*40,'expired':False,
           'digest':'sha256:'+'b'*64,'id':{'app':1,'test':2,'evidence':3}[role]}
    monkeypatch.setattr(sys.modules['devtool.actions_artifacts'],'artifact_list',lambda *a: {
        'remote_run':{'completed':True,'conclusion':'success','event':'workflow_dispatch',
                      'workflow_id':12,'run_attempt':1,'head_sha':'a'*40},
        'artifacts':[row(r) for r in m.ROLES]})
    m.artifact_list=sys.modules['devtool.actions_artifacts'].artifact_list
    params=(c,m.REPO,33,1,'a'*40,'d'*64,'e'*64,'f'*64)
    assert m.select(*params)['artifacts']['test']['id']==2
    expired=[row(r) for r in m.ROLES];expired[1]['expired']=True
    m.artifact_list=lambda *a:{'remote_run':{'completed':True,'conclusion':'success',
        'event':'workflow_dispatch','workflow_id':12,'run_attempt':1,'head_sha':'a'*40},'artifacts':expired}
    with pytest.raises(ValueError,match='untrusted'):
        m.select(*params)


def test_device_workload_is_exactly_scoped_and_non_gradle():
    source=SOURCE.read_text()
    assert "'--class','com.dergoogler.mmrl.acceptance.OfficialDebugAcceptanceTest'" in source
    assert "raw['executed']==3" in source
    assert "raw.get('passed')==3" in source
    assert "'--skip-build'" in source
    assert "'R37_BEGIN_'+secrets.token_hex(16)" in source
    assert "['log','-p','i'" in source
    assert "clear=False" in source
    assert "'debug_device_smoke_qualified':passed" in source
    assert "'device_qualified':False" in source
    assert "'lsposed_provider_readonly':'NOT_RUN'" in source


def test_cross_archive_native_receipt_mismatch_rejected(tmp_path,monkeypatch):
    import pytest
    m=load_module(monkeypatch);plan,archives=make_fixture(tmp_path,m)
    with zipfile.ZipFile(archives['evidence'],'r') as z:
        payload={name:z.read(name) for name in z.namelist()}
    native=json.loads(payload['r36-native-receipt.json']);native['run_id']='999'
    payload['r36-native-receipt.json']=json.dumps(native).encode()
    with zipfile.ZipFile(archives['evidence'],'w') as z:
        for name,value in payload.items():z.writestr(name,value)
    with pytest.raises(ValueError,match='native DAG receipts diverge'):
        m.validate_chain(plan,archives)


def test_duplicate_zip_entry_refused(tmp_path,monkeypatch):
    import pytest
    m=load_module(monkeypatch);plan,archives=make_fixture(tmp_path,m)
    with zipfile.ZipFile(archives['test'],'a') as z:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            z.writestr(m.TEST_MEMBER,b'replaced')
    with pytest.raises(ValueError,match='unexpected/duplicate'):
        m.validate_chain(plan,archives)
