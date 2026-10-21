"""NEXT-02 provenance falsification and read-only bridge acceptance."""
import hashlib
import json
from pathlib import Path

import pytest
from devtool.evidence_kernel import (DOMAINS, EvidenceError, validate_evidence,
    normalize_test_report, host_eligibility)
from devtool.artifact_evidence import (typed_artifact_verify, ArtifactEvidenceError)


def receipt():
    return {'schema_version':1, 'kind':'devtool.typed-evidence',
      'source':{'repository':'owner/repo','commit':'a'*40,'tree':'b'*40,
                'target':'demo','job':'test','node':'unit'},
      'execution':{'request_id':'req-001','workflow':'go-ci','run_id':11,'attempt':2,
                   'exo_run_id':'a'*32},
      'host':{'os':'linux','arch':'x86_64','toolchain':'go','provisioned':True,'authority':''},
      'artifacts':[{'role':'test-package','type':'zip','path':'build/app.zip','sha256':'e'*64,
                    'signature_state':'UNSIGNED','producer':'hosted-ci'}],
      'tests':{'format':'junit','total':3,'failed':0,'skipped':1,
               'report_sha256':'c'*64,'required':True},
      'gates':{d:{'state':'UNKNOWN','reason':'no owner verification','authority':''} for d in DOMAINS}}


def test_valid_shape_never_authenticates_external_claims():
    rec=receipt()
    for d in DOMAINS:rec['gates'][d]['state']='PASS'
    result=validate_evidence(rec,expected={'source.commit':'a'*40,'execution.attempt':2})
    assert not result['trusted_producer_verified']
    assert not result['formal_release_qualified']
    assert all(g['state']=='UNKNOWN' for g in result['effective_gates'].values())

@pytest.mark.parametrize('field,value',[
    ('source.commit','c'*40),('source.tree','f'*40),('execution.run_id',12),
    ('execution.attempt',1),('source.target','wrong')])
def test_exact_identity_fails_closed(field,value):
    with pytest.raises(EvidenceError,match='mismatch'):
        validate_evidence(receipt(),expected={field:value})

@pytest.mark.parametrize('mutation',[
    lambda r:r['execution'].update(attempt=None),
    lambda r:r['source'].update(unreviewed='foo'),
    lambda r:r['gates'].pop('release'),
    lambda r:r['tests'].update(total=0,failed=0,skipped=0),
    lambda r:r['tests'].update(total=3,failed=1),
    lambda r:r['artifacts'][0].update(path='../outside.apk'),
    lambda r:r['artifacts'][0].update(sha256='f'),
    lambda r:r['host'].update(provisioned='yes'),
])
def test_schema_malformations_rejected(mutation):
    r=receipt(); r['gates']['test']['state']='PASS'; mutation(r)
    if r['tests']['failed'] == 0 and r['tests']['total'] == 3 and r['execution']['attempt']==2 and r['artifacts'][0]['path']=='build/app.zip' and len(r['artifacts'][0]['sha256'])==64 and r['host']['provisioned'] is True and 'unreviewed' not in r['source'] and len(r['gates'])==len(DOMAINS):
        # This case is covered by a nonzero-failure case below.
        pass
    with pytest.raises(EvidenceError):validate_evidence(r)

@pytest.mark.parametrize('meta,runner,eligible',[
    ({},'command',True),({'ci_toolchain':'go'},'go',True),
    ({'device_required':True},'command',False),
    ({'root_required':True},'python',False),
    ({'release_signed':True},'go',False),
    ({'signing_required':True},'command',False),
    ({'ci_host_os':'windows'},'command',False),
    ({'ci_host_arch':'aarch64'},'command',False),
    ({'ci_toolchain':'dotnet'},'command',False),
    ({'device_required':'false'},'command',False),
    ({},'android',False),
])
def test_host_admission(meta,runner,eligible):
    view=host_eligibility(meta,runner=runner,host_os='linux',host_arch='x86_64',toolchains=('go','python-shell'))
    assert view['eligible'] == eligible
    assert not view['qualified_release']

@pytest.mark.parametrize('fmt,source,total,failed,skipped',[
 ('junit','<testsuite><testcase name="a"/><testcase name="b"><failure/></testcase><testcase name="c"><skipped/></testcase></testsuite>',3,1,1),
 ('pytest-junit','<testsuites><testsuite><testcase name="x"/></testsuite></testsuites>',1,0,0),
 ('js-junit','<testsuite><testcase name="x"/></testsuite>',1,0,0),
 ('trx','<TestRun><Results><UnitTestResult testId="x" executionId="y" outcome="Passed"/><UnitTestResult testId="z" executionId="q" outcome="Failed"/></Results></TestRun>',2,1,0),
 ('go-json','{"Action":"pass","Test":"TestOne"}\n{"Action":"fail","Test":"TestTwo"}\n',2,1,0),
 ('cargo-json','{"type":"test","event":"ok","name":"one"}\n{"type":"test","event":"ignored","name":"two"}\n',2,0,1),
])
def test_report_adapters(tmp_path,fmt,source,total,failed,skipped):
    f=tmp_path/'report';f.write_text(source)
    out=normalize_test_report(f,fmt)
    assert (out['total'],out['failed'],out['skipped'])==(total,failed,skipped)
    assert out['state']==('FAIL' if failed else 'PASS')
    assert not out['release_qualified']

@pytest.mark.parametrize('fmt,source',[
  ('junit','<testsuite tests="10"/>'),('junit','<testsuite><testcase>'),
  ('trx','<TestRun><Results/></TestRun>'),
  ('go-json','{"Action":"fail","Package":"x"}\n'),
  ('cargo-json','{"type":"test","event":"unknown"}\n'),
])
def test_unverifiable_reports_block(tmp_path,fmt,source):
    f=tmp_path/'report';f.write_text(source)
    with pytest.raises(EvidenceError):normalize_test_report(f,fmt)


def _registry(root, payload, *, digest=None, identity=None):
    artifact=root/'outputs'/'output.bin';artifact.parent.mkdir();artifact.write_bytes(payload)
    digest=digest or hashlib.sha256(payload).hexdigest()
    entry={'id':'artifact-1','path':'outputs/output.bin','sha256':digest,
           'type':'bin','metadata':{'remote_repository':'owner/repo',
                    'remote_head_sha':'a'*40,'remote_run_id':17,'remote_run_attempt':3,
                    **(identity or {})}}
    folder=root/'.devtool'/'artifacts';folder.mkdir(parents=True)
    (folder/'registry.json').write_text(json.dumps({'schema_version':2,'items':[entry]}))
    return artifact,folder/'registry.json'


def test_registered_digest_read_only(tmp_path):
    _,registry=_registry(tmp_path,b'hello')
    before=registry.read_bytes()
    got=typed_artifact_verify(tmp_path,'artifact-1',repository='owner/repo',commit='a'*40,run_id=17,attempt=3)
    assert got['integrity']['state']=='PASS'
    assert got['authenticity']['state']=='UNKNOWN'
    assert got['signature']['state']=='UNKNOWN'
    assert not got['formal_release_qualified']
    assert before==registry.read_bytes()
    assert not (tmp_path/'.devtool'/'artifacts'/'registry.lock').exists()

@pytest.mark.parametrize('modifier',[
    lambda artifact,reg:artifact.write_bytes(b'changed'),
    lambda artifact,reg: reg.write_text(reg.read_text().replace('"remote_run_attempt": 3','"remote_run_attempt": 2')),
    lambda artifact,reg: artifact.rename(artifact.with_name('old')),
])
def test_artifact_tampering_refused(tmp_path,modifier):
    a,r=_registry(tmp_path,b'hello');modifier(a,r)
    with pytest.raises(ArtifactEvidenceError):
        typed_artifact_verify(tmp_path,'artifact-1',run_id=17,attempt=3)


def test_artifact_symlink_escape_refused(tmp_path):
    a,_=_registry(tmp_path,b'hello')
    a.unlink();a.symlink_to('/etc/hosts')
    with pytest.raises(ArtifactEvidenceError,match='symlink'):
        typed_artifact_verify(tmp_path,'artifact-1')


def test_lineage_federation_separate_authority():
    from devtool.evidence_lineage import project_lineage
    view={'phase':'ACTIONS-R04','read_only':True,'repository':'owner/repo','source_sha':'a'*40,
          'pipeline_id':'e'*32, 'rows':[{'id':'exo','kind':'local-invoke','state':'LOCAL_OBSERVED','exo_run_id':'b'*32},
            {'id':'ci','kind':'github-result','state':'CI_PASS_UNVERIFIED','run_id':12,'run_attempt':1},
            {'id':'artifact','kind':'github-archive','state':'ARCHIVE_LOCAL_VERIFIED','run_id':12,'run_attempt':1}]}
    fed={'kind':'federation_producer_consumer_dag_contract','schema_version':1,
         'nodes':[{'key':'f'}], 'cross_repository_edges':[], 'source_lock_digest':'c'*64}
    out=project_lineage(view,fed)
    assert out['rows'][2]['domain_state']=='PASS'
    assert out['rows'][1]['domain_state']=='UNKNOWN'
    assert out['federation']['state']=='UNKNOWN'
    assert out['rows'][0]['exo_run_id']=='b'*32
    assert not out['remote_authenticated'] and not out['formal_release_qualified']


def test_lineage_unmatched_run_attempt_fails():
    from devtool.evidence_lineage import project_lineage
    view={'phase':'ACTIONS-R04','read_only':True,'repository':'owner/repo','source_sha':'a'*40,
          'rows':[{'id':'remote','kind':'github-result','state':'CI_PASS_UNVERIFIED','run_id':12,'run_attempt':None}]}
    with pytest.raises(EvidenceError,match='attempt'):
        project_lineage(view)


def test_graph_host_admission_resolves_target_local_metadata():
    from types import SimpleNamespace as Obj
    from devtool.evidence_kernel import graph_host_admission
    job=Obj(runner='command',metadata={'device_required':True})
    target=Obj(metadata={},jobs={'probe':job})
    ref=Obj(kind='job',resolution_scope='target',resolution_local_name='probe',name='probe')
    node=Obj(key='demo.ci.probe', target='demo', workflow='ci', runner='',ref=ref)
    graph=Obj(target='demo',workflow='ci',topological_order=lambda:(node,))
    config=Obj(targets={'demo':target},jobs={})
    out=graph_host_admission(config,graph,host_os='linux',host_arch='x86_64',toolchains=('python-shell',))
    assert not out['all_eligible'] and out['nodes'][0]['eligibility']['state']=='BLOCKED'
    job.metadata={'read_only':True, 'ci_toolchain':'python-shell'}
    assert graph_host_admission(config,graph,host_os='linux',host_arch='x86_64',toolchains=('python-shell',))['all_eligible']
