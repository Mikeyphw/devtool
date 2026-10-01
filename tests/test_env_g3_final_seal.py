from __future__ import annotations
import json, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def _disp(): return json.loads((ROOT/'ENV_G3_SOURCE_DISPOSITIONS.json').read_text())

def test_source_dispositions_are_complete_final_and_specific():
    p=_disp(); rows=p['rows']; assert p['schema_version']==1 and len(rows)==252
    ids=[r['source_id'] for r in rows]; assert len(ids)==len(set(ids))==252
    expected={f'{fam}-P{i:03d}' for fam in ('HOST','GRADLE','ENVADD') for i in range(1,85)}
    assert set(ids)==expected
    allowed={'SATISFIED','SUPERSEDED','EXPORTED','RETIRED'}
    assert all(r['disposition'] in allowed for r in rows)
    assert all(r['mapped_env_promise'].startswith('ENV-P') for r in rows)
    assert all(r['closure_reason'] and len(r['evidence'])>=3 for r in rows)
    assert all(__import__('re').fullmatch(r'ENV-P\d{3}',r['mapped_env_promise']) for r in rows)
    for r in rows:
        owned=[e for e in r['evidence'] if not e.startswith('source:')]
        assert owned and all((ROOT/e).exists() for e in owned), r['source_id']
    assert all(r['disposition']!='EXPORTED' or r.get('export_owner') for r in rows)
    assert len({(r['mapped_env_promise'],tuple(r['evidence'][:2])) for r in rows})>=12
    assert len({r['mapped_env_promise'] for r in rows})>=10
    assert len({tuple(e for e in r['evidence'] if not e.startswith('source:')) for r in rows})>=10

def test_source_dispositions_preserve_canonical_identity():
    rows=_disp()['rows']
    assert len({(r['source_audit_sha256'],r['source_line'],r['source_row_sha256']) for r in rows})==252
    assert all(len(r['source_audit_sha256'])==64 and len(r['source_row_sha256'])==64 for r in rows)

def test_consumer_contracts_bind_environment_identity():
    # Later consumers must consume ENV-owned typed evidence rather than recreate ambient host truth.
    required=['devtool/host_context.py','devtool/environment_qualification.py','devtool/environment_lifecycle.py','devtool/gradle_planning.py','devtool/gradle_takeover.py']
    assert all((ROOT/p).is_file() for p in required)
    for p in ['ENV_X04_PROMISE_LEDGER.json','ENV_X05_PROMISE_LEDGER.json','ENV_G2_PROMISE_LEDGER.json']:
        assert (ROOT/p).is_file()

def test_core_generated_surfaces_remain_current():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'

def test_fresh_process_wrapper_help_is_observational():
    before=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    cp=subprocess.run([str(ROOT/'devtoolw'),'--help'],cwd=ROOT,text=True,capture_output=True,timeout=30)
    assert cp.returncode==0, cp.stderr
    after=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    assert after==before

def test_attestation_is_candidate_and_complete():
    a=json.loads((ROOT/'ENV_G3_FINAL_ATTESTATION.json').read_text())
    assert a['status']=='candidate-awaiting-native-termux-qualification'
    assert a['source_obligations']==252 and a['workflow_nodes']==7
    import hashlib
    assert a['source_dispositions_sha256']==hashlib.sha256((ROOT/'ENV_G3_SOURCE_DISPOSITIONS.json').read_bytes()).hexdigest()
    assert a['next_gate']=='ANDROID-G3 59/70'
