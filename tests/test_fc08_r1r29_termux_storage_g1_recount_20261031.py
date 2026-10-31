"""R1R29 contracts: original ZIP+JUnit evidence, safe Android storage probe."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import pytest

ROOT=Path(__file__).resolve().parents[1]

def tool(name):
    p=ROOT/'tools'/name
    spec=importlib.util.spec_from_file_location('reviewed_fc08_r1r29_'+name.replace('.','_'),p)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert Path(module.__file__).resolve()==p.resolve()
    return module

recount=tool('fc08_r1r29_g1_recount.py')
storage=tool('fc08_r1r29_storage_probe.py')
native=tool('fc08_r1r28_termux.py')
evidence=tool('fc08_r1r28_evidence.py')
SHA='a'*40

def fake_archives(tmp_path):
    manifest=[]
    inventory='f'*64
    for i in range(1,25):
        module=f'tests/test_shard_{i:02d}.py'
        root=ET.Element('testsuites');suite=ET.SubElement(root,'testsuite',tests='1',failures='0',errors='0',skipped='0')
        ET.SubElement(suite,'testcase',name='test_ok',classname=module)
        xml=ET.tostring(root)
        log=b'1 passed\n'
        row={'module':module,'status':'PASS','exit_code':0,'failures':0,'errors':0,'skipped':0,
             'tests':1,'junit':'junit/module-001.xml','junit_sha256':hashlib.sha256(xml).hexdigest(),
             'log':'logs/module-001.log','log_sha256':hashlib.sha256(log).hexdigest()}
        receipt={'shard':i,'shards':24,'source_sha':SHA,'repository':'Mikeyphw/devtool',
                 'success':True,'complete':True,'formal_release_qualified':False,
                 'inventory_sha256':inventory,'modules_expected':[module],'modules':[row]}
        name=f'devtool-full-shard-{i}'
        path=tmp_path/(name+'.zip')
        with ZipFile(path,'w') as z:
            z.writestr('receipt.json',json.dumps(receipt));z.writestr('junit/module-001.xml',xml)
            z.writestr('logs/module-001.log',log)
        manifest.append({'name':name,'run_id':456,'archive_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    name='devtool-full-pytest-summary';path=tmp_path/(name+'.zip')
    with ZipFile(path,'w') as z:z.writestr('summary.json',json.dumps({
        'status':'PASS','source_sha':SHA,'expected_shards':24,'present_shards':24,
        'covered_modules':24,'total_modules':24,'tests':24,'formal_release_qualified':False,
        'failures':0,'errors':0,'skipped':0,'issues':[]}))
    manifest.append({'name':name,'run_id':456,'archive_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    for i in range(5):manifest.append({'name':f'fc08-real-lane-{i}-attempt1','run_id':123,'archive_sha256':'e'*64})
    return {'source':{'sha':SHA,'tree':'b'*40},'archive_count':30,
            'all_provider_zip_digests_match':True,'release_qualified':False,'reviewed_archives':manifest}

def test_recount_24_original_shard_archives_and_summary(tmp_path):
    review=fake_archives(tmp_path)
    result=recount.recount(tmp_path,review)
    assert result['g1_junit_independently_recounted'] is True
    assert result['primary_tests_recounted']==24
    assert result['unique_modules_recounted']==24
    assert len(result['shard_proofs'])==24
    assert result['release_qualified'] is False

def test_recount_rejects_archive_mutation_even_with_valid_internal_junit(tmp_path):
    review=fake_archives(tmp_path)
    with ZipFile(tmp_path/'devtool-full-shard-3.zip','a') as z:z.writestr('extra','bad')
    with pytest.raises(ValueError,match='ARCHIVE_DIGEST_MISMATCH'):
        recount.recount(tmp_path,review)

def test_recount_rejects_junit_digest_mismatch_and_recounts_not_just_summary(tmp_path):
    review=fake_archives(tmp_path)
    path=tmp_path/'devtool-full-shard-2.zip'
    with ZipFile(path,'r') as z:members={name:z.read(name) for name in z.namelist()}
    members['junit/module-001.xml']=b'<invalid/>'
    with ZipFile(path,'w') as z:
        for name,raw in members.items():z.writestr(name,raw)
    next(x for x in review['reviewed_archives'] if x['name']=='devtool-full-shard-2')['archive_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='MODULE_JUNIT_LOG_HASH_MISMATCH'):
        recount.recount(tmp_path,review)

@pytest.mark.parametrize('wrong',[{'archive_count':29}, {'release_qualified':True},
                                   {'all_provider_zip_digests_match':False}])
def test_recount_requires_authenticated_prior_provider_receipts(tmp_path,wrong):
    review=fake_archives(tmp_path);review.update(wrong)
    with pytest.raises(ValueError,match='PRIOR_GITHUB_REVIEW_NOT_AUTHENTICATED'):
        recount.recount(tmp_path,review)

def test_junit_primary_count_handles_pytest_subtests_without_false_positive():
    xml=b'<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"><testcase name="first"/><testcase name="second"/></testsuite></testsuites>'
    assert recount._primary_cases(xml)==2

@pytest.mark.parametrize('bad',[b'<!DOCTYPE x [ <!ENTITY e SYSTEM "file:///etc/passwd"> ]><testsuite/>',
                                  b'<testsuite failures="1" errors="0" skipped="0"><testcase name="bad"/></testsuite>',
                                  b'<testsuite failures="0" errors="0" skipped="0"><testcase name="bad"><failure/></testcase></testsuite>'])
def test_recount_fails_closed_on_unsafe_or_failed_junit(bad):
    with pytest.raises(ValueError):recount._primary_cases(bad)

def test_storage_probe_is_read_only_and_redacts_artifact_path(tmp_path):
    source=tmp_path/'token=secret-zip.zip';source.write_bytes(b'original archive')
    before=source.read_bytes()
    outcome=storage.probe(source,require_native=False)
    assert source.read_bytes()==before
    assert outcome['artifact']['bytes_read_from_artifact']==0
    assert outcome['artifact']['source_deleted'] is False
    assert outcome['canary'] is None
    assert 'token=secret' not in json.dumps(outcome)
    assert outcome['release_qualified'] is False

def test_storage_probe_refuses_symlink_and_preserves_target(tmp_path):
    target=tmp_path/'precious.zip';target.write_bytes(b'keep')
    link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(ValueError,match='SYMLINK'):
        storage.probe(link,require_native=False)
    assert target.read_bytes()==b'keep' and link.is_symlink()

def test_storage_canary_requires_consent_then_removes_only_own_file(tmp_path):
    neighbor=tmp_path/'user-download.zip';neighbor.write_bytes(b'private')
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        storage.canary(tmp_path,approved=False,require_native=False)
    proof=storage.canary(tmp_path,approved=True,require_native=False)
    assert proof['status']=='PASS' and proof['canary_removed']
    assert neighbor.read_bytes()==b'private'
    assert not list(tmp_path.glob('.fc08-r1r29-owned-*.probe'))

def test_storage_cannot_qualify_cleanup_of_actual_user_artifact(tmp_path):
    artifact=tmp_path/'actual.zip';artifact.write_bytes(b'private')
    result=storage.probe(artifact,directory=tmp_path,approve_canary=True,require_native=False)
    assert result['shared_storage_owned_canary_passed'] is True
    assert result['actual_shared_storage_cleanup_qualified'] is False
    assert artifact.read_bytes()==b'private'

def test_native_runner_keeps_device_and_transaction_separate():
    script=(ROOT/'tools/fc08_r1r28_termux.py').read_text()
    assert 'NATIVE_SOURCE_WORKTREE_DIRTY' in script
    assert 'NATIVE_OWNED_CLEANUP_INCOMPLETE' in script
    assert 'transaction_rollback_integration_qualified' in script
    assert "'device_signing_qualified':False" in script

def test_collector_offers_explicit_real_recount_and_source_binding():
    script=(ROOT/'tools/fc08_r1r28_evidence.py').read_text()
    assert "--recount-g1" in script
    assert "spec_from_file_location" in script
    assert "result['g1_junit_independently_recounted']=True" in script
    assert "'release_qualified':False" in script

native_accept=tool('fc08_r1r29_native_accept.py')

def test_consolidated_native_acceptance_refuses_non_termux_before_canary(tmp_path, monkeypatch):
    # This regression MUST be host-independent. Native validation itself runs
    # on actual Termux during transactional overlay apply, where platform
    # checks legitimately pass and the temporary worktree is intentionally dirty.
    # Force a non-ARM64 host rather than assuming pytest always runs on Ubuntu.
    import platform
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    artifact=tmp_path/'download.zip';artifact.write_bytes(b'keep')
    with pytest.raises(RuntimeError,match='NATIVE_TERMUX_ARM64_REQUIRED'):
        native_accept.accept(ROOT,artifact=artifact)
    assert artifact.read_bytes()==b'keep'

def test_consolidated_native_receipt_persists_false_signer_and_release_gates(tmp_path,monkeypatch):
    original=native_accept._load
    class FakeNative:
        def native_accept(self,root):
            return {'source':{'sha':SHA,'tree':'b'*40},'native_arm64_compilation_passed':True,
                    'release_qualified':False}
    class FakeStorage:
        def probe(self,artifact,**kwargs):
            return {'artifact':{'source_deleted':False},'shared_storage_owned_canary_passed':True,
                    'actual_shared_storage_cleanup_qualified':False}
    monkeypatch.setattr(native_accept,'_load',lambda name:FakeNative() if 'termux.py' in name else FakeStorage())
    artifact=tmp_path/'download.zip';artifact.write_bytes(b'keep')
    result=native_accept.accept(ROOT,artifact=artifact,probe_dir=tmp_path,approve_canary=True)
    assert result['native_termux_arm64_acceptance_passed']
    assert result['shared_storage_owned_canary_passed']
    assert all(result[k] is False for k in ('real_user_artifact_cleanup_qualified',
        'android_signing_qualified','device_installation_qualified','consumer_qualified',
        'fc08_overall_qualified','release_qualified'))
    assert artifact.read_bytes()==b'keep'
    out=tmp_path/'receipt.json';native_accept.save(out,result)
    assert json.loads(out.read_text())['source']['sha']==SHA
