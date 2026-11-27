"""W4 bounded native integrity/regression gate; full hosted CI24 remains mandatory."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'.devtool.toml').is_file():
    raise SystemExit('W4_NATIVE_SOURCE_ROOT_UNVERIFIED')
sys.path.insert(0,str(ROOT))
TESTS=(
    'tests/test_actions_r05_operator.py',
    'tests/test_apply_terminal_cleanup_hardening.py',
    'tests/test_arch_ov08_tui_cleanup.py',
    'tests/test_artifact_lifecycle_ui.py',
    'tests/test_artifact_validation_fail_closed_hotfix.py',
    'tests/test_cli.py',
    'tests/test_cli_global_yes.py',
    'tests/test_clipboard.py',
    'tests/test_complete_feature_guides.py',
    'tests/test_completion_and_manpage.py',
    'tests/test_core_x04_generated_surfaces.py',
    'tests/test_delivery_base_rich_projection.py',
    'tests/test_delivery_ci_evidence.py',
    'tests/test_delivery_cli_surface.py',
    'tests/test_delivery_m4_integration.py',
    'tests/test_delivery_remote_logs.py',
    'tests/test_delivery_rich.py',
    'tests/test_delivery_session.py',
    'tests/test_dotnet_progress.py',
    'tests/test_dt_hotfix_m6.py',
    'tests/test_fc02_transaction_closure.py',
    'tests/test_g0a_executable_overlay.py',
    'tests/test_g0c1_native_terminal.py',
    'tests/test_g0c2_operator_status.py',
    'tests/test_ga00_security_baseline.py',
    'tests/test_ga01_scoped_auth.py',
    'tests/test_ga02_validator_inventory.py',
    'tests/test_m6_live.py',
    'tests/test_m6_receipt_logs.py',
    'tests/test_native_delivery_continuity_m3.py',
    'tests/test_native_delivery_contract_v3.py',
    'tests/test_native_delivery_m5.py',
    'tests/test_native_runtime_preflight_m2.py',
    'tests/test_operator_event_feed.py',
    'tests/test_operator_rich.py',
    'tests/test_operator_workspace.py',
    'tests/test_overlay_r06d_recovery_plan.py',
    'tests/test_pres_g1_textual_replacement_proof_gate.py',
    'tests/test_pres_g2_interaction_reliability_gate.py',
    'tests/test_pres_g2_interaction_reliability_remediation.py',
    'tests/test_pres_g2r_concrete_runtime_requalification.py',
    'tests/test_pres_g3_final_presentation_seal.py',
    'tests/test_pres_h02_textual_apply_host.py',
    'tests/test_pres_r01_execution_presentation.py',
    'tests/test_pres_r02_textual_control_center_app.py',
    'tests/test_pres_r03_textual_parity_qualification.py',
    'tests/test_pres_ux01_textual_workspace_revamp.py',
    'tests/test_pres_x01_presentation_surfaces.py',
    'tests/test_pres_x02_presentation_layouts.py',
    'tests/test_pres_x03_cross_surface_rendering.py',
    'tests/test_pres_x04_textual_tui.py',
    'tests/test_pres_x05_textual_feature_parity.py',
    'tests/test_pres_x06_textual_default_replacement.py',
    'tests/test_pres_x07_textual_replacement_hardening.py',
    'tests/test_pres_x08_textual_control_center.py',
    'tests/test_qualification_workflows.py',
    'tests/test_roadmap_delivery_audit.py',
    'tests/test_source_bound_pres_exec_migration.py',
    'tests/test_test_count_projection_hotfix.py',
    'tests/test_ui03_ui04_preferences_tokens_copy.py',
    'tests/test_ui07_ui08_confirmation_notification_terminal.py',
    'tests/test_validation_manifest_skip_contract.py',
    'tests/test_w3_native_validation.py',
    'tests/test_w4_shared_inline.py',
    'tests/test_w4_f04_workspace.py',
    'tests/test_hf01_fixture_portability.py',
    'tests/test_rm08_completion.py',
    'tests/test_native_ci24_policy.py',
)

# Explicit owner-side scope; hosted W3, W4 complement and CI24 remain exhaustive.
NATIVE_MODULES = (
    'tests/test_native_delivery_contract_v3.py',
    'tests/test_native_delivery_continuity_m3.py',
    'tests/test_native_runtime_preflight_m2.py',
    'tests/test_artifact_validation_fail_closed_hotfix.py',
    'tests/test_validation_manifest_skip_contract.py',
    'tests/test_fc02_transaction_closure.py',
    'tests/test_ga00_security_baseline.py',
    'tests/test_ga01_scoped_auth.py',
    'tests/test_clipboard.py',
    'tests/test_overlay_r06d_recovery_plan.py',
    'tests/test_w3_native_validation.py',
    'tests/test_hf01_fixture_portability.py',
    'tests/test_w4_f04_workspace.py',
    'tests/test_native_ci24_policy.py',
)

REVIEWED_CHANGES = (
    '.github/workflows/devtool-dn03-r3-source-qualification.yml',
    '.github/workflows/devtool-dn03-r4-source-qualification.yml',
    '.github/workflows/devtool-dn03-r6-trx-source-qualification.yml',
    '.github/workflows/devtool-dn03-source-qualification.yml',
    '.github/workflows/devtool-portable-ci-qualification.yml',
    '.github/workflows/devtool-w02-portable-dotnet-source.yml',
    'devtool/actions_graph_export.py',
    'devtool/actions_runs.py',
    'devtool/ci_tree_evidence.py',
    'devtool/delivery_remote_logs.py',
    'devtool/display_graphemes.py',
    'devtool/github_actions.py',
    'devtool/github_app.py',
    'devtool/hooks/engine.py',
    'devtool/host_context.py',
    'devtool/operator_rich.py',
    'devtool/operator_workspace.py',
    'devtool/postmerge_ci.py',
    'devtool/qualification_workflows.py',
    'devtool/run_store.py',
    'devtool/workspace_actions.py',
    'devtool/workspace_viewport.py',
    'docs/DEVTOOL_IMPROVEMENTS.md',
    'docs/DEVTOOL_UNIFIED_ROADMAP.md',
    'docs/DEVTOOL_W4_RICH_ACCEPTANCE.md',
    'examples/actions-g5/go/smoke_test.go',
    'examples/actions-g5/rust/src/lib.rs',
    'tests/_compat.py',
    'tests/test_actions_09_release_setup.py',
    'tests/test_actions_a04_runtime.py',
    'tests/test_android_unsigned_first_hotfix.py',
    'tests/test_android_x02_execution_snapshot_device_fabric.py',
    'tests/test_artifact_finalize.py',
    'tests/test_core_x04_generated_surfaces.py',
    'tests/test_desktop_flat_ui.py',
    'tests/test_env_x01_host_privilege_foundation.py',
    'tests/test_env_x05_gradle_takeover.py',
    'tests/test_hf01_fixture_portability.py',
    'tests/test_native_ci24_policy.py',
    'tests/test_operator_workspace.py',
    'tests/test_rem09_core_runtime_ipc_hooks.py',
    'tests/test_rem13_ui_desktop_truth_actions.py',
    'tests/test_repair04_horizontal_apply_mobile.py',
    'tests/test_rm08_completion.py',
    'tests/test_rm15_hf02_release_unification.py',
    'tests/test_w4_f04_workspace.py',
    'tests/w3_validation_support.py',
    'tests/w4_native_validate.py',
)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--hosted-extra',action='store_true')
    mode.add_argument('--full-local',action='store_true',help='Explicit comprehensive local validation; not used by CI24 delivery')
    args=parser.parse_args()
    if not TESTS or any(not (ROOT/p).is_file() for p in TESTS):
        raise SystemExit('W4_NATIVE_REQUIRED_TEST_MISSING')
    if args.hosted_extra:
        from tests.w3_native_validate import TESTS as W3
        import pytest
        extra=sorted(set(TESTS)-set(W3))
        if not extra:raise SystemExit('W4_HOSTED_INVENTORY_EMPTY')
        raise SystemExit(pytest.main(['-q',*extra]))
    from tests.w3_validation_support import run_local
    if args.full_local:
        raise SystemExit(run_local(ROOT,TESTS,standard=True))
    raise SystemExit(run_local(ROOT,TESTS,reviewed_changes=REVIEWED_CHANGES,reviewed_native_modules=NATIVE_MODULES))
