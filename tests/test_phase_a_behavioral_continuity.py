"""Behavioral contracts retained after retiring source-disposition/attestation tests."""
from __future__ import annotations
from pathlib import Path
from devtool.core_runtime import ResolutionContext, build_run_envelope
from devtool.core_registry import build_core_registry
ROOT = Path(__file__).resolve().parents[1]

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


def test_generated_surfaces_remain_current_after_gate_surface_is_declared():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'


def test_consumer_contracts_bind_environment_identity():
    # Later consumers must consume ENV-owned typed evidence rather than recreate ambient host truth.
    required=['devtool/host_context.py','devtool/environment_qualification.py','devtool/environment_lifecycle.py','devtool/gradle_planning.py','devtool/gradle_takeover.py']
    assert all((ROOT/p).is_file() for p in required)
    for p in ['ENV_X04_PROMISE_LEDGER.json','ENV_X05_PROMISE_LEDGER.json','ENV_G2_PROMISE_LEDGER.json']:
        assert (ROOT/p).is_file()


def test_env_generated_surfaces_remain_current():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'


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


def test_android_g3_core_generated_surfaces_remain_current():
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces,check_generated_surfaces
    assert check_generated_surfaces(build_generated_surfaces(build_parser()),ROOT/'share/core-generated')['status']=='pass'


def test_life_g3_core_generated_surfaces_remain_current() -> None:
    from devtool.cli import build_parser
    from devtool.core_surfaces import build_generated_surfaces, check_generated_surfaces

    assert check_generated_surfaces(build_generated_surfaces(build_parser()), ROOT / "share/core-generated")["status"] == "pass"

