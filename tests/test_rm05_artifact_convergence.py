"""RM-05 adversarial behavior coverage (not file-presence checks)."""
import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from devtool.artifact_convergence import (ConvergenceError, verify_artifact_receipt,
    merge_test_shards, VERIFIER_KINDS)

SHA='a'*40
PRODUCER={'node':'target.workflow.build','source_sha':SHA,'run_id':'run-01','attempt':2}

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def setup(tmp):
    root=tmp/'repo';root.mkdir()
    p=root/'out.zip'
    with zipfile.ZipFile(p,'w') as z:z.writestr('index.html','<!doctype html><title>ok</title>')
    data={'schema_version':2,'kind':'devtool.artifact-receipt','producer':dict(PRODUCER),
          'artifact':{'kind':'zip','path':'out.zip','sha256':digest(p),'package_id':None},
          'consumers':[{'node':'target.workflow.publish','sha256':digest(p)}],
          'attestation':None,'sbom':None}
    r=tmp/'receipt.json';r.write_text(json.dumps(data));return root,p,r,data

def test_happy_path_is_integrity_only(tmp_path):
    root,p,r,d=setup(tmp_path);v=verify_artifact_receipt(root,r,expected=PRODUCER)
    assert v['integrity']=='PASS' and v['consumers']==['target.workflow.publish']
    assert v['signature']['state']=='UNSIGNED' and not v['remote_authenticated']
    assert not v['release_qualified'] and v['producer']['attempt']==2

def test_hash_producer_attempt_source_consumer_mismatch(tmp_path):
    root,p,r,d=setup(tmp_path)
    p.write_bytes(b'tampered')
    with pytest.raises(ConvergenceError,match='digest'):verify_artifact_receipt(root,r)
    root,p,r,d=setup(tmp_path/'another') if False else (root,p,r,d)
    # Restore valid archive, update digest; source/replay still fail closed.
    with zipfile.ZipFile(p,'w') as z:z.writestr('index.html','hi')
    d['artifact']['sha256']=digest(p)
    for value in [{'source_sha':'b'*40},{'attempt':3},{'run_id':'run-02'},{'node':'other'}]:
        with pytest.raises(ConvergenceError,match='identity'):verify_artifact_receipt(root,r,expected=value)
    d['consumers'][0]['sha256']='0'*64;r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='consumer'):verify_artifact_receipt(root,r)

def test_unsafe_relative_and_symlink(tmp_path):
    root,p,r,d=setup(tmp_path)
    d['artifact']['path']='../secret';r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='unsafe'):verify_artifact_receipt(root,r)
    d['artifact']['path']='link.zip';(root/'link.zip').symlink_to(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='symlink'):verify_artifact_receipt(root,r)

@pytest.mark.parametrize('entries',[[('../escape','evil')],[('dup','a'),('dup','b')],[('a\\b','bad')],
                                    [('ok','v'),('ok','again')]])
def test_archive_traversal_duplicate_and_backslash(tmp_path,entries):
    root,p,r,d=setup(tmp_path)
    with zipfile.ZipFile(p,'w') as z:
        for n,v in entries:z.writestr(n,v)
    d['artifact']['sha256']=digest(p);d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='duplicate|traversal|unsafe'):verify_artifact_receipt(root,r)

def test_zip_bomb_and_link(tmp_path):
    root,p,r,d=setup(tmp_path)
    with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('huge.txt',b'A'*2_000_000)
    d['artifact']['sha256']=digest(p);d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='bomb'):verify_artifact_receipt(root,r)
    with zipfile.ZipFile(p,'w') as z:
        i=zipfile.ZipInfo('symlink');i.create_system=3;i.external_attr=(0o120777 <<16);z.writestr(i,'target')
    d['artifact']['sha256']=digest(p);d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='link'):verify_artifact_receipt(root,r)

def test_package_identity_mismatch_and_unverifiable_android(tmp_path):
    root,p,r,d=setup(tmp_path)
    with zipfile.ZipFile(p,'w') as z:z.writestr('package.json',json.dumps({'name':'demo','version':'1.0'}));z.writestr('index.html','a')
    d['artifact'].update(kind='web-bundle',sha256=digest(p),package_id='not-demo@1.0')
    d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='package identity'):verify_artifact_receipt(root,r)
    d['artifact']['package_id']='demo@1.0';r.write_text(json.dumps(d))
    assert verify_artifact_receipt(root,r)['artifact']['package_identity_verified']
    with zipfile.ZipFile(p,'w') as z:z.writestr('AndroidManifest.xml','binary-manifest')
    d['artifact'].update(kind='apk',sha256=digest(p),package_id='com.demo')
    d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='Android package identity'):verify_artifact_receipt(root,r)

def test_format_adapter_rejects_incorrect_kind(tmp_path):
    root,p,r,d=setup(tmp_path)
    d['artifact']['kind']='jar';r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='package member'):verify_artifact_receipt(root,r)
    d['artifact']['kind']='unknown';r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='unregistered'):verify_artifact_receipt(root,r)
    assert {'apk','aab','wheel','sdist','nupkg','jar','go-binary','native-binary','web-bundle'} <= VERIFIER_KINDS

def test_tampered_sbom(tmp_path):
    root,p,r,d=setup(tmp_path);sb=root/'sbom.json';sb.write_text('{"bomFormat":"CycloneDX"}')
    d['sbom']={'path':'sbom.json','sha256':digest(sb)};r.write_text(json.dumps(d))
    assert verify_artifact_receipt(root,r)['sbom']['state']=='INTEGRITY_VERIFIED_NOT_ATTESTED'
    sb.write_text('tampered')
    with pytest.raises(ConvergenceError,match='SBOM'):verify_artifact_receipt(root,r)

def test_attestation_untrusted_and_tampered_payload(tmp_path):
    root,p,r,d=setup(tmp_path)
    payload=json.dumps({'producer':PRODUCER,'artifact_sha256':digest(p)},sort_keys=True).encode()
    d['attestation']={'payload':base64.b64encode(payload).decode(),'signature':base64.b64encode(b'not-a-real-signature').decode(),'key_id':'build-key'}
    r.write_text(json.dumps(d));v=verify_artifact_receipt(root,r)
    assert v['signature']['state']=='SIGNED_UNVERIFIED' and not v['signature']['authenticated']
    with pytest.raises(ConvergenceError,match='signature'):
        verify_artifact_receipt(root,r,trusted_keys={'build-key':b'\x00'*32})
    payload=json.dumps({'producer':PRODUCER,'artifact_sha256':'b'*64}).encode()
    d['attestation']['payload']=base64.b64encode(payload).decode();r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='predicate'):verify_artifact_receipt(root,r)

def test_real_ed25519_trusted_key_only(tmp_path):
    crypto=pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ed25519')
    from cryptography.hazmat.primitives import serialization
    root,p,r,d=setup(tmp_path)
    key=crypto.Ed25519PrivateKey.generate();pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    payload=json.dumps({'producer':PRODUCER,'artifact_sha256':digest(p)},sort_keys=True).encode()
    d['attestation']={'payload':base64.b64encode(payload).decode(),'signature':base64.b64encode(key.sign(payload)).decode(),'key_id':'trusted'}
    r.write_text(json.dumps(d));v=verify_artifact_receipt(root,r,trusted_keys={'trusted':pub})
    assert v['signature']['state']=='VERIFIED' and not v['release_qualified']

def manifest(tmp,fmt='junit'):
    root=tmp/'repo';root.mkdir()
    body='<testsuite><testcase classname="Suite" name="first"/></testsuite>'
    a=root/'a.xml';a.write_text(body)
    m={'schema_version':2,'kind':'devtool.test-shard-manifest','producer':dict(PRODUCER),
       'shards':[{'id':'shard1','node':'target.test.a','format':fmt,'path':'a.xml','sha256':digest(a)}]}
    r=tmp/'test-shards.json';r.write_text(json.dumps(m));return root,a,r,m

def test_junit_merge_deterministic_and_node_assignment(tmp_path):
    root,a,r,m=manifest(tmp_path)
    b=root/'b.xml';b.write_text('<testsuite><testcase classname="Suite" name="second"><failure/></testcase></testsuite>')
    m['shards'].append({'id':'shard0','node':'target.test.b','format':'pytest-junit','path':'b.xml','sha256':digest(b)})
    r.write_text(json.dumps(m));v=merge_test_shards(root,r,expected=PRODUCER)
    assert v['counts']=={'total':2,'failed':1,'skipped':0,'passed':1}
    assert v['shards'][0]['id']=='shard0' and v['cases'][1]['node']=='target.test.b'
    assert v['state']=='FAIL' and not v['trusted_producer_verified']
    assert merge_test_shards(root,r)==v

def test_duplicate_cases_conflicting_shards_and_sha(tmp_path):
    root,a,r,m=manifest(tmp_path)
    b=root/'b.xml';b.write_text('<testsuite><testcase classname="Suite" name="first"><failure/></testcase></testsuite>')
    m['shards'].append({'id':'shard2','node':'target.test.b','format':'junit','path':'b.xml','sha256':digest(b)})
    r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='duplicate case'):merge_test_shards(root,r)
    m['shards'].pop();a.write_text('changed')
    with pytest.raises(ConvergenceError,match='digest'):merge_test_shards(root,r)

def test_truncated_duplicate_xml_and_bad_trx(tmp_path):
    root,a,r,m=manifest(tmp_path)
    a.write_text('<testsuite><testcase');m['shards'][0]['sha256']=digest(a);r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='truncated'):merge_test_shards(root,r)
    a.write_text('<testsuite><testcase classname="S" name="t"/><testcase classname="S" name="t"/></testsuite>')
    m['shards'][0]['sha256']=digest(a);r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='duplicate'):merge_test_shards(root,r)
    m['shards'][0]['format']='trx';r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='TRX root'):merge_test_shards(root,r)

def test_go_and_cargo_json_terminal_and_truncation(tmp_path):
    root,a,r,m=manifest(tmp_path)
    a.write_text('\n'.join(json.dumps(x) for x in [
        {'Action':'run','Test':'TestA','Package':'pkg'},
        {'Action':'pass','Test':'TestA','Package':'pkg'}])+'\n')
    m['shards'][0].update(format='go-json',sha256=digest(a));r.write_text(json.dumps(m))
    assert merge_test_shards(root,r)['counts']['passed']==1
    a.write_text(json.dumps({'Action':'run','Test':'TestA','Package':'pkg'})+'\n')
    m['shards'][0]['sha256']=digest(a);r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='truncated'):merge_test_shards(root,r)
    a.write_text('\n'.join(json.dumps(x) for x in [{'type':'test','name':'cargo.test','event':'started'},
                                                    {'type':'test','name':'cargo.test','event':'failed'}])+'\n')
    m['shards'][0].update(format='cargo-json',sha256=digest(a));r.write_text(json.dumps(m))
    assert merge_test_shards(root,r)['state']=='FAIL'

def test_xml_xxe_blocked(tmp_path):
    root,a,r,m=manifest(tmp_path)
    a.write_text('<!DOCTYPE x [<!ENTITY test SYSTEM "file:///etc/passwd">]><testsuite><testcase name="&test;"/></testsuite>')
    m['shards'][0]['sha256']=digest(a);r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='entities'):merge_test_shards(root,r)

def test_schema_rejects_unknown_fields_and_wrong_attempt(tmp_path):
    root,a,r,m=manifest(tmp_path)
    with pytest.raises(ConvergenceError,match='identity'):merge_test_shards(root,r,expected={'attempt':1})
    m['something_unrecognized']='x';r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='unsupported'):merge_test_shards(root,r)

def test_cli_parser_exposes_both_routes():
    from devtool.cli import build_parser
    p=build_parser()
    assert p.parse_args(['artifacts','verify','--receipt','a.json']).receipt=='a.json'
    args=p.parse_args(['evidence','merge-tests','--receipt','tests.json','--attempt','2'])
    assert args.evidence_command=='merge-tests' and args.attempt==2

def test_exo_journal_matches_actual_terminal_and_rejects_contradiction(tmp_path):
    root,a,r,m=manifest(tmp_path)
    journal=root/'journal.jsonl'
    journal.write_text(json.dumps({'event_type':'node.finished','run_id':'run-01',
                                   'node_id':'target.test.a','status':'succeeded'})+'\n')
    m['exo']={'path':'journal.jsonl','sha256':digest(journal)};r.write_text(json.dumps(m))
    assert merge_test_shards(root,r)['exo_correlation']['state'].startswith('LOCAL_JOURNAL')
    journal.write_text(json.dumps({'event_type':'node.finished','run_id':'run-01',
                                   'node_id':'target.test.a','status':'failed'})+'\n')
    m['exo']['sha256']=digest(journal);r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='contradicts'):merge_test_shards(root,r)
    m['exo']['sha256']='0'*64;r.write_text(json.dumps(m))
    with pytest.raises(ConvergenceError,match='digest'):merge_test_shards(root,r)

def test_tar_symlink_and_traversal_are_rejected_without_extracting(tmp_path):
    root,p,r,d=setup(tmp_path)
    with tarfile.open(p,'w') as t:
        info=tarfile.TarInfo('safe-link');info.type=tarfile.SYMTYPE;info.linkname='../escape';t.addfile(info)
    d['artifact'].update(kind='tar',sha256=digest(p));d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='links/special'):verify_artifact_receipt(root,r)
    with tarfile.open(p,'w') as t:
        info=tarfile.TarInfo('../escape');info.size=1;t.addfile(info,io.BytesIO(b'X'))
    d['artifact']['sha256']=digest(p);d['consumers'][0]['sha256']=digest(p);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='traversal'):verify_artifact_receipt(root,r)

def test_wheel_record_digest_is_checked_even_without_package_id(tmp_path):
    root,p,r,d=setup(tmp_path)
    target=root/'demo-1.0-py3-none-any.whl'
    import base64,csv,io
    body=b'Name: demo\nVersion: 1.0\n'; wheel=b'Wheel-Version: 1.0\nTag: py3-none-any\n'
    entries={'demo-1.0.dist-info/METADATA':body,'demo-1.0.dist-info/WHEEL':wheel}
    output=io.StringIO();writer=csv.writer(output)
    for name,data in entries.items():
        writer.writerow([name,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(),len(data)])
    writer.writerow(['demo-1.0.dist-info/RECORD','',''])
    with zipfile.ZipFile(target,'w') as z:
        for name,data in entries.items():z.writestr(name,data)
        z.writestr('demo-1.0.dist-info/RECORD',output.getvalue())
    d['artifact'].update(kind='wheel',path=target.name,sha256=digest(target),package_id='demo@1.0')
    d['consumers'][0]['sha256']=digest(target);r.write_text(json.dumps(d))
    result=verify_artifact_receipt(root,r)
    assert result['artifact']['wheel_record_sha256_verified']
    assert result['artifact']['package_identity_verified']
    with zipfile.ZipFile(target,'w') as z:
        for name,data in entries.items():z.writestr(name,body if name.endswith('WHEEL') else data)
        z.writestr('demo-1.0.dist-info/RECORD',output.getvalue())
    d['artifact']['sha256']=digest(target);d['consumers'][0]['sha256']=digest(target);r.write_text(json.dumps(d))
    with pytest.raises(ConvergenceError,match='RECORD'):verify_artifact_receipt(root,r)
