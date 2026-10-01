from __future__ import annotations
import json, subprocess
from pathlib import Path
from devtool.core_runtime import ResolutionContext, build_run_envelope
from devtool.core_registry import build_core_registry
ROOT=Path(__file__).resolve().parents[1]

def _manifest(): return json.loads((ROOT/'CORE_G3_SOURCE_DISPOSITIONS.json').read_text())

def test_source_disposition_manifest_is_complete_final_and_specific():
    p=_manifest(); rows=p['rows']; assert p['schema_version']==2 and len(rows)==336
    ids=[r['source_id'] for r in rows]; assert len(set(ids))==336
    expected={f'{fam}-P{i:03d}' for fam in ('CLI','CFG','WRAP','RUN') for i in range(1,85)}
    assert set(ids)==expected
    allowed={'SATISFIED','SUPERSEDED','EXPORTED','RETIRED'}
    assert all(r['disposition'] in allowed for r in rows)
    assert all(r.get('mapped_core_promise','').startswith('CORE-P') for r in rows)
    assert all(r.get('mapped_core_text') and r.get('closure_reason') for r in rows)
    assert all(len(r.get('evidence',[]))>=2 for r in rows)
    # Reject the V1 failure mode: one blanket evidence list per source family.
    signatures={tuple(r['evidence'])+(r['mapped_core_promise'],) for r in rows}
    assert len(signatures)>=20
    assert p['mapping_method'].startswith('per-row semantic mapping')

def test_source_disposition_rows_preserve_canonical_source_identity():
    p=_manifest();
    assert len({(r['source_audit_sha256'],r['source_line'],r['source_row_sha256']) for r in p['rows']})==336
    assert all(len(r['source_row_sha256'])==64 and len(r['source_audit_sha256'])==64 for r in p['rows'])

def test_canonical_and_compatibility_paths_share_identity(tmp_path):
    canonical=build_run_envelope(ResolutionContext.from_argv(['apply','overlay','x.zip'],repo_root=tmp_path))
    compat=build_run_envelope(ResolutionContext.from_argv(['apply-overlay','x.zip'],repo_root=tmp_path))
    assert canonical.invocation.command_id==compat.invocation.command_id=='cli.command.apply.overlay'
    assert canonical.descriptor.command_digest==compat.descriptor.command_digest
    assert canonical.descriptor.config_digest==compat.descriptor.config_digest

def test_nested_identity_chain_is_not_prefix_truncated(tmp_path):
    env=build_run_envelope(ResolutionContext.from_argv(['core','runtime','--','validate'],repo_root=tmp_path))
    assert env.invocation.command_id=='cli.command.core.runtime'
    assert tuple(env.invocation.canonical_path)==('core','runtime')
    d=env.descriptor.to_dict(); assert d['command_id']=='cli.command.core.runtime'; assert d['scope_digest']

def test_registry_has_one_authority_for_aliases():
    r=build_core_registry(); a=r.resolve_command(['apply','overlay','x.zip']); b=r.resolve_command(['apply-overlay','x.zip'])
    assert a['command'] is b['command']

def test_fresh_process_wrapper_help_is_observational():
    before=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    cp=subprocess.run([str(ROOT/'devtoolw'),'--help'],cwd=ROOT,text=True,capture_output=True,timeout=30)
    assert cp.returncode==0, cp.stderr
    after=subprocess.run(['git','status','--porcelain=v1'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    assert after==before

def test_generated_surfaces_remain_current_after_gate_surface_is_declared():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'

def test_attestation_is_candidate_not_predeclared_pass():
    a=json.loads((ROOT/'CORE_G3_FINAL_ATTESTATION.json').read_text())
    assert a['status']=='candidate-awaiting-native-termux-qualification'; assert a['source_obligations']==336
    assert a['generated_surface_fixed_point_required'] is True
    assert len(a['workflow_nodes'])==7
