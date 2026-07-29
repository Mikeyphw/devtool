from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase38_repository_doctor_model_classifies_path_namespace_and_runuser() -> None:
    result = read("RepositoryDoctorResult.java")
    for symbol in (
        "repo_path_wrong_namespace",
        "repo_doctor_path_missing",
        "repo_doctor_runuser_failed",
        "repo_doctor_git_missing",
        "repo_doctor_shell_missing",
        "repo_chroot_user_missing",
        "plainText()",
    ):
        assert symbol in result
    assert "canonicalPath.startsWith(RepositoryLocation.TERMUX_HOME)" in result
    assert "User switch failed before Git started" in result
    assert "path_exists" in result and "git_c_works" in result


def test_phase38_repository_doctor_runs_read_only_probe_and_persists_result() -> None:
    doctor = read("RepositoryDoctor.java")
    for symbol in (
        "RepositoryDoctor",
        "ACTION_UPDATED",
        "REPO_DOCTOR_REQUESTED",
        "REPO_DOCTOR_PASSED",
        "REPO_DOCTOR_WARNED",
        "REPO_DOCTOR_FAILED",
        "repositoryDoctor(location, payload, 24L)",
        "__DEVTOOL_REPO_DOCTOR__",
        "command -v git",
        "command -v runuser",
        "git -C",
        "runuser -u",
        "supportSummary",
    ):
        assert symbol in doctor
    assert "SharedPreferences" in doctor and "prefs(context).edit()" in doctor


def test_phase38_root_command_broker_has_separate_chroot_doctor_executor() -> None:
    broker = read("RootCommandBroker.java")
    for symbol in (
        "repositoryDoctor(RepositoryLocation location, String payload",
        "REPO_DOCTOR_EXECUTOR_SELECTED",
        "REPO_DOCTOR_CHROOT_ROOT_PROBE_SELECTED",
        "chrootDoctorShell",
        "exec -- /bin/sh -c",
    ):
        assert symbol in broker
    assert broker.index("chrootDoctorShell") > broker.index("repositoryDoctor")
    assert "Repository doctor will inspect chroot before user switching" in broker


def test_phase38_repository_detail_exposes_repo_manager_doctor() -> None:
    detail = read("RepositoryDetailActivity.java")
    for symbol in (
        "RepositoryDoctor.ACTION_UPDATED",
        "Repo Manager Doctor",
        "Run Doctor",
        "Copy diagnosis",
        "RepositoryDoctor.latest",
        "RepositoryDoctor.runAsync",
        "result.plainText()",
        "runuser=",
    ):
        assert symbol in detail


def test_phase38_support_bundle_and_classifier_include_repo_doctor() -> None:
    support = read("SupportBundle.java")
    classifier = read("DebugIssueClassifier.java")
    assert 'value.put("repository_doctor", RepositoryDoctor.supportSummary(context, repository));' in support
    for issue in (
        "repo_path_wrong_namespace",
        "repo_doctor_path_missing",
        "repo_doctor_runuser_failed",
        "repo_doctor_git_missing",
        "repo_doctor_shell_missing",
    ):
        assert issue in classifier
