from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase44_guided_fix_model_maps_known_failures_to_safe_actions() -> None:
    model = read("DebugGuidedFix.java")
    for symbol in (
        "DebugGuidedFix",
        "BUNDLE_KEY",
        "from(Context context, DebugWorkbenchHealth.Snapshot snapshot)",
        "supportSummary(Context context)",
        "whatHappened",
        "whatItMeans",
        "evidence",
        "nextAction",
        "actionKind",
        "plainText()",
        "termux_run_command_permission_missing",
        "termux_wrapper_never_started",
        "bridge_token_mismatch",
        "workdir_missing",
        "devtool_binary_missing",
        "run_started_no_progress",
        "repo_path_wrong_namespace",
        "repo_doctor_runuser_failed",
        "repo_doctor_shell_missing",
        "repo_doctor_git_missing",
        "repo_operation_orphaned",
        "validation_stale_revision",
        "notification_blocked",
    ):
        assert symbol in model
    for forbidden in ("git reset", "git clean", "rm -rf", "deleteRepository"):
        assert forbidden not in model


def test_phase44_debug_workbench_surfaces_guided_fix_cards_and_actions() -> None:
    activity = read("DebugWorkbenchActivity.java")
    for symbol in (
        "Guided Fixes",
        "guidedFixesCard(snapshot)",
        "DebugGuidedFix.from(this, snapshot)",
        "What happened:",
        "What it means:",
        "Evidence:",
        "Safe next action:",
        "performGuidedFixAction",
        "Copy diagnosis",
        "Copy guided fixes",
        "Run bridge roundtrip probe",
        "Open repository doctor",
        "Open Validation Workbench",
        "notification_settings",
    ):
        assert symbol in activity
    health_index = activity.index("content.addView(healthCard(snapshot))")
    guided_index = activity.index("content.addView(guidedFixesCard(snapshot))")
    launcher_index = activity.index("content.addView(launcherCard())")
    assert health_index < guided_index < launcher_index
    for forbidden in ("git reset", "git clean", "rm -rf", "Hard reset", "Discard all"):
        assert forbidden not in activity


def test_phase44_guided_fix_support_bundle_section_is_exported() -> None:
    support = read("SupportBundle.java")
    assert 'root.put("guided_fixes", DebugGuidedFix.supportSummary(app))' in support
    assert 'root.put("debug_workbench", DebugWorkbenchHealth.supportSummary(app))' in support


def test_phase44_guided_fixes_reuse_existing_doctor_and_trace_sources() -> None:
    model = read("DebugGuidedFix.java")
    for symbol in (
        "DebugIssueClassifier.classifyMessage",
        "bridge_probe",
        "RepositoryPathResolver.resolve",
        "RepoQueueRecovery.assess",
        "RepoQueueRecovery.plainText",
        "ValidationSessionRecord",
        "RunRecord",
        "DebugEventRecord",
    ):
        assert symbol in model
    assert "Git will never start correctly until Termux/root/chroot path metadata is aligned." in model
    assert "User switching failed before Git started." in model
