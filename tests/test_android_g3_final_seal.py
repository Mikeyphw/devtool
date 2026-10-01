from __future__ import annotations
import hashlib,json,re,subprocess,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def _disp(): return json.loads((ROOT/'ANDROID_G3_SOURCE_DISPOSITIONS.json').read_text())

def test_android_g3_source_dispositions_are_complete_final_and_specific():
    p=_disp(); rows=p['rows']; assert p['schema_version']==1 and p['source_obligations']==168 and len(rows)==168
    expected={f'AND-P{i:03d}' for i in range(1,85)}|{f'INST-P{i:03d}' for i in range(1,85)}
    assert {r['source_id'] for r in rows}==expected and len({r['source_id'] for r in rows})==168
    allowed={'SATISFIED','SUPERSEDED','EXPORTED','RETIRED'}
    assert all(r['disposition'] in allowed for r in rows)
    assert all(re.fullmatch(r'ANDROID-P(?:0[0-9][1-9]|0[1-9]0|[1-9][0-9]{2})',r['mapped_android_promise']) for r in rows)
    assert all(1<=int(r['mapped_android_promise'].split('P')[1])<=99 for r in rows)
    for r in rows:
        assert r['source_text'] and len(r['source_audit_sha256'])==64 and len(r['source_row_sha256'])==64
        assert r['mapping_basis'] and r['closure_reason'] and len(r['evidence'])>=3
        assert all((ROOT/e).exists() for e in r['evidence']),r['source_id']
        assert set(r['evidence_sha256'])==set(r['evidence']),r['source_id']
        assert all(hashlib.sha256((ROOT/e).read_bytes()).hexdigest()==r['evidence_sha256'][e] for e in r['evidence']),r['source_id']
        assert r['disposition']!='EXPORTED' or (r['export_owner'] and r['export_contract'])
    assert len({r['mapped_android_promise'] for r in rows})>=25
    assert len({tuple(r['evidence']) for r in rows})>=8
    assert len({(r['source_audit_sha256'],r['source_line'],r['source_row_sha256']) for r in rows})==168

def test_android_g3_explicit_exported_authorities_are_not_reclaimed():
    rows={r['source_id']:r for r in _disp()['rows']}
    expected={
      'AND-P002':'ENV','AND-P003':'ENV','AND-P004':'CORE','AND-P005':'EXEC','AND-P006':'CORE','AND-P008':'LIFE',
      'AND-P055':'LIFE','AND-P067':'ENV','AND-P068':'ENV','AND-P069':'ENV',
      'INST-P004':'ENV','INST-P005':'ENV','INST-P006':'EXEC','INST-P007':'EXEC','INST-P008':'CORE','INST-P009':'PRES',
      'INST-P010':'PRES','INST-P011':'CORE','INST-P028':'CORE','INST-P049':'ENV','INST-P050':'ENV','INST-P052':'EXEC',
      'INST-P053':'EXEC','INST-P061':'EXEC','INST-P069':'PRES',
    }
    assert {k:rows[k]['export_owner'] for k in expected}==expected
    assert all(rows[k]['disposition']=='EXPORTED' for k in expected)

def test_android_g3_attestation_binds_dispositions_and_prior_seals():
    a=json.loads((ROOT/'ANDROID_G3_FINAL_ATTESTATION.json').read_text())
    assert a['status']=='candidate-awaiting-native-termux-qualification' and a['source_obligations']==168 and a['merged_promises']==99
    assert a['workflow_nodes']==7 and a['next_gate']=='LIFE-G3 60/70'
    assert a['source_dispositions_sha256']==hashlib.sha256((ROOT/'ANDROID_G3_SOURCE_DISPOSITIONS.json').read_bytes()).hexdigest()
    items=[]
    for r in _disp()['rows']:
        for e,d in sorted(r['evidence_sha256'].items()): items.append(f"{r['source_id']}\0{e}\0{d}")
    assert a['evidence_root_sha256']==hashlib.sha256(('\n'.join(sorted(items))+'\n').encode()).hexdigest()
    assert a['evidence_bindings']==len(items)
    for rel,digest in a['prior_seals'].items(): assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==digest

def test_android_g3_cross_program_consumers_preserve_android_authority():
    required=[
      'ANDROID_X04_ADVANCED_EXECUTION_ADOPTION_REPORT.md','tests/test_android_x04_advanced_execution.py',
      'TEST_X04_HISTORICAL_REUSE_DISTRIBUTED_UX_CONVERGENCE_REPORT.md','tests/test_test_x04_convergence.py',
      'EXEC_X04_POST_CONSUMER_RESEAL_REPORT.md','tests/test_exec_x04_post_consumer_convergence.py',
      'DIST_X04_OPERATIONAL_ADOPTION_REPORT.md','tests/test_dist_x04_operational_adoption.py',
      'LIFE_X04_LIFECYCLE_CONVERGENCE_ADOPTION_REPORT.md','tests/test_life_x04_lifecycle_convergence.py',
      'PRES_G2_INTERACTION_RELIABILITY_MILESTONE_GATE_REPORT.md','tests/test_pres_g2_interaction_reliability_gate.py',
    ]
    assert all((ROOT/p).is_file() for p in required)

def test_android_g3_wrapper_workflow_is_seven_ordered_stages():
    cfg=tomllib.loads((ROOT/'.devtool.toml').read_text())
    cmd=cfg['wrapper']['commands']['android-g3']; assert cmd['workflow']=='roadmap-android-g3' and cmd['target']=='devtool'
    wf=cfg['targets']['devtool']['workflows']['roadmap-android-g3']
    assert [n['id'] for n in wf]==['syntax','source-dispositions','environment-device-authority','instrumentation-authority','advanced-adoption','cross-program-fixed-point','final-fresh-process']

def test_android_g3_core_generated_surfaces_remain_current():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces,check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'

def test_android_g3_gate_tooling_is_observational_on_construction_host():
    before=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    cp=subprocess.run(['python','-m','devtool.android_g3_gate'],cwd=ROOT,text=True,capture_output=True,env={**__import__('os').environ,'DEVTOOL_ANDROID_G3_ALLOW_TEST_HOST':'1'},timeout=120)
    assert cp.returncode==0,cp.stderr
    payload=json.loads([line for line in cp.stdout.splitlines() if line.lstrip().startswith('{')][-1]); assert payload['status']=='passed' and payload['matrix_complete'] is True and payload['retry_owner']=='EXO'
    after=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    assert after==before
