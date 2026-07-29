from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase36_trace_model_has_wrapper_sentinel_checkpoints() -> None:
    trace = read("RunCommandTrace.java")
    for checkpoint in (
        "TERMUX_WRAPPER_STARTED",
        "SHELL_RESOLVED",
        "TOKEN_MARKERS_OK",
        "TOKEN_MARKERS_MISSING",
        "WORKDIR_CHECKED",
        "WORKDIR_MISSING",
        "ENV_READY",
        "COMMAND_FOUND",
        "COMMAND_LOOKUP_SKIPPED",
        "COMMAND_NOT_FOUND",
        "EXEC_STARTING",
        "EXIT_CODE_RECORDED",
        "FIRST_OUTPUT",
    ):
        assert checkpoint in trace
    assert "case TERMUX_WRAPPER_STARTED" in trace
    assert "case EXIT_CODE_RECORDED" in trace


def test_phase36_termux_wrapper_sentinel_builds_authenticated_broadcasts() -> None:
    sentinel = read("TermuxWrapperSentinel.java")
    for symbol in (
        "WRAPPER_STARTED",
        "SHELL_RESOLVED",
        "TOKEN_MARKERS_OK",
        "WORKDIR_CHECKED",
        "ENV_READY",
        "COMMAND_FOUND",
        "EXEC_STARTING",
        "EXIT_CODE_RECORDED",
    ):
        assert symbol in sentinel
    assert "wrapperPreamble" in sentinel
    assert "wrapperChecks" in sentinel
    assert "exitReporter" in sentinel
    assert "--es trace_checkpoint" in sentinel
    assert "--es trace_message" in sentinel
    assert "--es wrapper_stage" in sentinel
    assert "--es wrapper_command" in sentinel
    assert "__devtool_wrapper_now_ms" in sentinel
    assert "DEVTOOL_ANDROID_HUD_TOKEN" in sentinel
    assert "DEVTOOL_ANDROID_HUD_COMPONENT" in sentinel


def test_phase36_termux_runner_wraps_command_without_losing_exit_code() -> None:
    termux = read("TermuxCommandRunner.java")
    assert "TermuxWrapperSentinel.wrapperPreamble" in termux
    assert "TermuxWrapperSentinel.wrapperChecks" in termux
    assert "TermuxWrapperSentinel.exitReporter" in termux
    assert "primaryCommand(cleanCommand)" in termux
    assert "\"primary_command\", primaryCommand" in termux
    assert "shell.append(\"( \").append(command).append(\" )\")" in termux


def test_phase36_primary_command_detection_knows_devtool_and_repo_utilities() -> None:
    termux = read("TermuxCommandRunner.java")
    for command in (
        "devtool-androidos",
        "devtool",
        "git",
        "python3",
        "chrootctl",
        "runuser",
    ):
        assert f'"{command}"' in termux
    assert "lower.contains(\" \" + candidate + \" \")" in termux


def test_phase36_debug_classifier_knows_wrapper_failure_shapes() -> None:
    classifier = read("DebugIssueClassifier.java")
    for key in (
        "termux_wrapper_markers_missing",
        "termux_workdir_missing",
        "termux_command_not_found_before_exec",
        "termux_exec_starting",
        "termux_wrapper_exit_code_recorded",
    ):
        assert key in classifier
