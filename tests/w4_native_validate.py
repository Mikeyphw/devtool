"""W4 native UI regression, with unchanged full W3 and exhaustive CI24 gates."""
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
)

REVIEWED_CHANGES=(
    '.github/workflows/devtool-ga-source-acceptance.yml',
    'completions/_devtool',
    'devtool/actions_runs.py',
    'devtool/cli.py',
    'devtool/completion.py',
    'devtool/core_cli_schema.json',
    'devtool/delivery_remote_logs.py',
    'devtool/delivery_rich.py',
    'devtool/display_graphemes.py',
    'devtool/github_actions.py',
    'devtool/inline_live.py',
    'devtool/m6_live.py',
    'devtool/m6_operation_view.py',
    'devtool/operator_rich.py',
    'devtool/operator_workspace.py',
    'devtool/posix_input.py',
    'devtool/qualification_workflows.py',
    'devtool/rich_workspace_entrypoint.py',
    'devtool/source_bound_proofs.py',
    'devtool/tui_textual.py',
    'docs/DEVTOOL_AI_AGENT_GUIDE.md',
    'docs/DEVTOOL_HUMAN_GUIDE.md',
    'docs/DEVTOOL_IMPROVEMENTS.md',
    'docs/DEVTOOL_UNIFIED_ROADMAP.md',
    'docs/DEVTOOL_W3_FOUNDATIONAL_ACCEPTANCE.md',
    'docs/DEVTOOL_W4_RICH_ACCEPTANCE.md',
    'share/core-generated/config-schema.json',
    'share/core-generated/devtool-core.1',
    'share/core-generated/machine-help.json',
    'share/core-generated/manifest.json',
    'share/core-generated/reference.md',
    'tests/test_delivery_remote_logs.py',
    'tests/test_delivery_rich.py',
    'tests/test_m6_live.py',
    'tests/test_operator_workspace.py',
    'tests/test_pres_g1_textual_replacement_proof_gate.py',
    'tests/test_pres_g2r_concrete_runtime_requalification.py',
    'tests/test_pres_g3_final_presentation_seal.py',
    'tests/test_pres_x06_textual_default_replacement.py',
    'tests/test_pres_x07_textual_replacement_hardening.py',
    'tests/test_qualification_workflows.py',
    'tests/test_w3_native_validation.py',
    'tests/test_w4_shared_inline.py',
    'tests/w3_validation_support.py',
    'tests/w4_native_validate.py',
)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hosted-extra',action='store_true')
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
    raise SystemExit(run_local(ROOT,TESTS,standard=True,reviewed_changes=REVIEWED_CHANGES))
