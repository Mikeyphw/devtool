from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase39_root_execution_plan_model_tracks_shell_and_user_switch_candidates() -> None:
    plan = read("RootExecutionPlan.java")
    for symbol in (
        "RootExecutionPlan",
        "METHOD_RUNUSER",
        "METHOD_SU",
        "METHOD_DIRECT_ROOT",
        "fromProbe(RepositoryLocation location",
        "selectedShell",
        "userSwitchMethod",
        "rejectedShellCandidates",
        "rejectedUserSwitchCandidates",
        "directRootFallback",
        "root_execution_plan_direct_root_fallback",
        "root_execution_plan_su_fallback",
        "markerPrefix()",
    ):
        assert symbol in plan
    assert "zsh_available" in plan and "bash_available" in plan and "sh_available" in plan


def test_phase39_broker_uses_planner_instead_of_assuming_zsh_runuser() -> None:
    broker = read("RootCommandBroker.java")
    for symbol in (
        "selectChrootExecutionPlan",
        "chrootPlannerProbe",
        "ROOT_EXECUTION_PLAN_SELECTED",
        "ROOT_EXECUTION_PLAN_FALLBACK_SELECTED",
        "ROOT_EXECUTION_PLAN_PROBE_FAILED",
        "RootExecutionPlan.markerPrefix()",
        "runuser -u",
        "METHOD_SU",
        "rejected_user_switch_candidates",
    ):
        assert symbol in broker
    old_assumption = "exec -- zsh -lc"
    assert old_assumption not in broker
    assert "for candidate in zsh bash sh" in broker
    assert "METHOD_DIRECT_ROOT" not in broker
    assert "kv user_switch_method unavailable" in broker
    assert "exit 69" in broker
    assert "exec -- " in broker and " -c " in broker


def test_phase39_repository_doctor_exports_execution_plan_evidence() -> None:
    doctor = read("RepositoryDoctor.java")
    result = read("RepositoryDoctorResult.java")
    for symbol in (
        "selected_shell",
        "user_switch_method",
        "direct_root_fallback",
        "rejected_shell_candidates",
        "rejected_user_switch_candidates",
    ):
        assert symbol in doctor
        assert symbol in result
    assert "Executor plan: shell=" in result
    assert "selectedShell" in result and "userSwitchMethod" in result


def test_phase39_classifier_knows_execution_plan_fallbacks() -> None:
    classifier = read("DebugIssueClassifier.java")
    for issue in (
        "root_execution_plan_direct_root_fallback",
        "root_execution_plan_su_fallback",
        "root_execution_plan_probe_failed",
        "root_execution_plan_timeout",
    ):
        assert issue in classifier
