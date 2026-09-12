from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._compat import portable_shell_argv

from devtool.cli import build_parser, main
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.runners import run_specs
from devtool.validation_failures import classify_validation_failure
from devtool.validation_sessions import (
    SESSION_SCHEMA_VERSION,
    ValidationSessionError,
    accept_plan_change,
    create_session,
    load_active_session,
    load_session,
    mark_phase_running,
    reconcile_inputs,
    record_phase_result,
    required_checkpoint_satisfied,
    select_phase_keys,
    session_path,
    sync_plan,
)


def make_project(root: Path) -> None:
    (root / "app/src/main/kotlin").mkdir(parents=True)
    (root / "app/src/test/kotlin").mkdir(parents=True)
    (root / "app/src/main/res/layout").mkdir(parents=True)
    (root / "app/src/main/kotlin/Main.kt").write_text("class Main\n", encoding="utf-8")
    (root / "app/src/test/kotlin/MainTest.kt").write_text("class MainTest\n", encoding="utf-8")
    (root / "app/src/main/res/layout/main.xml").write_text("<FrameLayout/>\n", encoding="utf-8")
    (root / "app/src/main/AndroidManifest.xml").write_text("<manifest package='demo'/>\n", encoding="utf-8")
    (root / "settings.gradle.kts").write_text('rootProject.name="Demo"\ninclude(":app")\n', encoding="utf-8")
    (root / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (root / "app/build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)


def config_for(root: Path, *, minimum_phase: str = "finalize") -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="android",
        runner_options={"validation": {"resumable": True}},
    )


def spec(name: str, *argv: str, depends_on: list[str] | None = None, domains: list[str] | None = None) -> CommandSpec:
    metadata: dict[str, object] = {"validation_phase_name": name}
    if depends_on is not None:
        metadata["validation_depends_on"] = depends_on
    if domains is not None:
        metadata["validation_input_domains"] = domains
    return CommandSpec(name, list(argv or ("true",)), metadata=metadata)


def test_dependency_closure_and_phase_only_guard(tmp_path: Path) -> None:
    make_project(tmp_path)
    session = create_session(
        config_for(tmp_path),
        [spec("preflight"), spec("dependencies"), spec("compile"), spec("package")],
        persist=False,
    )
    selected = select_phase_keys(session, phase="package")
    assert [session.phase_by_key(key).name for key in selected] == [
        "preflight", "dependencies", "compile", "package"
    ]
    with pytest.raises(ValidationSessionError, match="unsatisfied dependencies"):
        select_phase_keys(session, phase="package", exact_phase=True)


def test_minimum_phase_must_exist_and_pass(tmp_path: Path) -> None:
    make_project(tmp_path)
    config = config_for(tmp_path)
    with pytest.raises(ValidationSessionError, match="cannot reach"):
        create_session(
            config,
            [spec("compile")],
            apply_context={"minimum_phase": "package"},
            persist=False,
        )
    session = create_session(
        config,
        [spec("compile"), spec("package")],
        apply_context={"minimum_phase": "package"},
        persist=False,
    )
    assert not required_checkpoint_satisfied(session)
    for phase in session.phases:
        phase.status = "passed"
    assert required_checkpoint_satisfied(session)


def test_required_phase_removal_requires_explicit_acceptance(tmp_path: Path) -> None:
    make_project(tmp_path)
    config = config_for(tmp_path)
    session = create_session(config, [spec("compile"), spec("package")], persist=False)
    for phase in session.phases:
        phase.status = "passed"
    with patch("devtool.validation_sessions.save_session"):
        with pytest.raises(ValidationSessionError, match="changed incompatibly"):
            sync_plan(session, [spec("compile")])
        assert session.status == "recovery-required"
        accept_plan_change(session, [spec("compile")], reason="package phase intentionally removed")
    assert session.plan_drift_status == "accepted"
    assert session.apply_context["accepted_plan_changes"][-1]["reason"] == "package phase intentionally removed"


def test_command_change_invalidates_only_graph_dependents(tmp_path: Path) -> None:
    make_project(tmp_path)
    session = create_session(
        config_for(tmp_path),
        [spec("compile", "gradle", "compile"), spec("unit-test", "gradle", "test"), spec("lint", "gradle", "lint")],
        persist=False,
    )
    for phase in session.phases:
        phase.status = "passed"
    with patch("devtool.validation_sessions.save_session"):
        sync_plan(
            session,
            [spec("compile", "gradle", "compile"), spec("unit-test", "gradle", "testRelease"), spec("lint", "gradle", "lint")],
        )
    assert [phase.status for phase in session.phases] == ["passed", "pending", "passed"]


def test_domain_change_invalidates_consumers_and_dependents(tmp_path: Path) -> None:
    make_project(tmp_path)
    config = config_for(tmp_path)
    session = create_session(
        config,
        [
            spec("compile", domains=["source", "config"]),
            spec("unit-test", domains=["source", "tests", "config"]),
            spec("lint", domains=["source", "resources", "config"]),
        ],
        persist=False,
    )
    for phase in session.phases:
        phase.status = "passed"
    (tmp_path / "app/src/test/kotlin/MainTest.kt").write_text("class ChangedTest\n", encoding="utf-8")
    with patch("devtool.validation_sessions.save_session"):
        changed = reconcile_inputs(session, config)
    assert changed == "tests"
    assert [phase.status for phase in session.phases] == ["passed", "pending", "passed"]


def test_phase_success_is_discarded_when_inputs_change_during_attempt(tmp_path: Path) -> None:
    make_project(tmp_path)
    session = create_session(config_for(tmp_path), [spec("compile", domains=["source"])], persist=False)
    phase = mark_phase_running(session, session.phases[0].key)
    (tmp_path / "app/src/main/kotlin/Main.kt").write_text("class Changed\n", encoding="utf-8")
    recorded = record_phase_result(session, phase.key, OperationResult("compile", ["true"], 0, 1.0, "ok", []))
    assert recorded.status == "stale"
    assert recorded.last_verdict == "inputs-changed"
    assert recorded.attempt_history[-1].inputs_changed
    assert session.status == "paused"


@pytest.mark.parametrize(
    ("result", "verdict", "retryable"),
    [
        (OperationResult("test", ["gradle"], 1, 1.0, "There were failing tests", [], failure_kind="test_failure"), "test-failed", False),
        (OperationResult("compile", ["gradle"], 137, 1.0, "Killed by earlyoom under memory pressure", []), "resource-failed", True),
        (OperationResult("compile", ["gradle"], 1, 1.0, "Gradle build daemon disappeared unexpectedly (it may have been killed or may have crashed)", []), "resource-failed", True),
        (OperationResult("compile", ["gradle"], 137, 1.0, "process disappeared", []), "environment-failed", True),
        (OperationResult("compile", ["gradle"], 130, 1.0, "Interrupted by user", []), "interrupted", True),
        (OperationResult("compile", ["gradle"], 127, 1.0, "sdkmanager: command not found", []), "toolchain-missing", False),
    ],
)
def test_failure_classifier(result: OperationResult, verdict: str, retryable: bool) -> None:
    classified = classify_validation_failure(result)
    assert classified.verdict == verdict
    assert classified.retryable is retryable


def test_runner_supervisor_terminates_child_and_emits_heartbeat(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    probes = 0
    heartbeats = 0

    def supervisor():
        nonlocal probes
        probes += 1
        return ("resource", "test guard") if probes >= 1 else None

    def heartbeat():
        nonlocal heartbeats
        heartbeats += 1

    started = time.monotonic()
    results, raw = run_specs(
        config,
        [CommandSpec("sleep", portable_shell_argv("printf 'started\n'; sleep 30"))],
        verbose=False,
        supervisor_probe=supervisor,
        heartbeat_callback=heartbeat,
    )
    assert time.monotonic() - started < 8
    assert results[0].returncode == 75
    assert results[0].failure_kind == "resource_interrupted"
    assert results[0].metadata["supervisor_events"][0]["kind"] == "resource"
    assert "validation supervisor stopped process" in raw.lower()
    assert heartbeats >= 1


def test_cli_automatically_retries_resource_failure_with_safe_profile(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    calls = 0

    def fake_run(_config, specs, _args, **_kwargs):
        nonlocal calls
        calls += 1
        current = specs[0]
        if calls == 1:
            result = OperationResult(
                current.name,
                list(current.argv),
                75,
                0.1,
                "Killed by earlyoom under memory pressure",
                [],
                failure_kind="memory-pressure",
                metadata=dict(current.metadata),
            )
        else:
            result = OperationResult(current.name, list(current.argv), 0, 0.1, "ok", [], metadata=dict(current.metadata))
        return [result], result.output

    with patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
        rc = main([
            "--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next",
            "--max-resource-retries", "1",
        ])
    assert rc == 0
    session = load_active_session(tmp_path, required=True)
    first = session.phases[0]
    assert first.status == "passed"
    assert len(first.attempt_history) == 2
    assert first.attempt_history[0].verdict == "resource-failed"
    assert first.attempt_history[1].automatic_retry
    assert first.attempt_history[1].resource_profile == "android-low-power"


def test_cli_no_auto_retry_leaves_resource_interruption_retryable(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")

    def fake_run(_config, specs, _args, **_kwargs):
        current = specs[0]
        result = OperationResult(
            current.name,
            list(current.argv),
            75,
            0.1,
            "memory pressure",
            [],
            failure_kind="memory_guard",
            metadata=dict(current.metadata),
        )
        return [result], result.output

    with patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
        rc = main([
            "--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next", "--no-auto-retry",
        ])
    assert rc == 0
    session = load_active_session(tmp_path, required=True)
    assert session.phases[0].status == "interrupted"
    assert len(session.phases[0].attempt_history) == 1


def test_cli_prompts_to_retry_resource_interruption_when_interactive(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    calls = 0

    def fake_run(_config, specs, _args, **_kwargs):
        nonlocal calls
        calls += 1
        current = specs[0]
        if calls == 1:
            result = OperationResult(
                current.name,
                list(current.argv),
                75,
                0.1,
                "Gradle build daemon disappeared unexpectedly (it may have been killed or may have crashed)",
                [],
                failure_kind="resource_interrupted",
                metadata=dict(current.metadata),
            )
        else:
            result = OperationResult(
                current.name,
                list(current.argv),
                0,
                0.1,
                "ok",
                [],
                metadata=dict(current.metadata),
            )
        return [result], result.output

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("devtool.cli.Confirm.ask", return_value=True) as confirm,
        patch("devtool.cli.render_summary"),
        patch("devtool.cli._run_specs_for_args", side_effect=fake_run),
    ):
        rc = main([
            "--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next", "--no-auto-retry",
        ])

    assert rc == 0
    assert calls == 2
    confirm.assert_called_once()
    session = load_active_session(tmp_path, required=True)
    first = session.phases[0]
    assert first.status == "passed"
    assert len(first.attempt_history) == 2
    assert first.attempt_history[0].verdict == "resource-failed"
    assert not first.attempt_history[1].automatic_retry
    assert first.attempt_history[1].resource_profile == "android-low-power"


def test_cli_prompts_to_retry_memory_guard_with_guard_disabled(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    calls = 0
    seen_metadata: list[dict] = []
    seen_argv: list[list[str]] = []

    def fake_run(_config, specs, _args, **_kwargs):
        nonlocal calls
        calls += 1
        current = specs[0]
        seen_metadata.append(dict(current.metadata))
        seen_argv.append(list(current.argv))
        if calls == 1:
            result = OperationResult(
                current.name,
                list(current.argv),
                75,
                0.1,
                "warning: Emergency memory guard stopped Gradle after hard emergency floor: 165 MiB available",
                [],
                failure_kind="memory_guard",
                metadata=dict(current.metadata),
            )
        else:
            result = OperationResult(
                current.name,
                list(current.argv),
                0,
                0.1,
                "ok",
                [],
                metadata=dict(current.metadata),
            )
        return [result], result.output

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("devtool.cli.Confirm.ask", return_value=True) as confirm,
        patch("devtool.cli.render_summary"),
        patch("devtool.cli._run_specs_for_args", side_effect=fake_run),
    ):
        rc = main(["--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next"])

    assert rc == 0
    assert calls == 2
    assert "memory guard disabled" in str(confirm.call_args.args[0]).lower()
    assert seen_metadata[1]["validation_memory_guard_mb"] == 0
    assert seen_metadata[1]["memory_guard_mb"] == 0
    assert seen_metadata[1]["validation_memory_guard_disabled_retry"] is True
    if "--memory-guard-mb" in seen_argv[1]:
        assert seen_argv[1][seen_argv[1].index("--memory-guard-mb") + 1] == "0"
    session = load_active_session(tmp_path, required=True)
    first = session.phases[0]
    assert first.status == "passed"
    assert len(first.attempt_history) == 2
    assert first.attempt_history[0].verdict == "resource-failed"
    assert first.attempt_history[1].resource_profile == "memory-guard-disabled"


def test_cli_memory_guard_retry_prompt_decline_keeps_phase_retryable(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    calls = 0

    def fake_run(_config, specs, _args, **_kwargs):
        nonlocal calls
        calls += 1
        current = specs[0]
        result = OperationResult(
            current.name,
            list(current.argv),
            75,
            0.1,
            "warning: Emergency memory guard stopped Gradle after hard emergency floor: 165 MiB available",
            [],
            failure_kind="memory_guard",
            metadata=dict(current.metadata),
        )
        return [result], result.output

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("devtool.cli.Confirm.ask", return_value=False) as confirm,
        patch("devtool.cli.render_summary"),
        patch("devtool.cli._run_specs_for_args", side_effect=fake_run),
    ):
        rc = main(["--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next"])

    assert rc == 0
    assert calls == 1
    confirm.assert_called_once()
    session = load_active_session(tmp_path, required=True)
    assert session.phases[0].status == "interrupted"
    assert len(session.phases[0].attempt_history) == 1


def test_cli_memory_guard_retry_prompt_skips_non_interactive_mode(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    calls = 0

    def fake_run(_config, specs, _args, **_kwargs):
        nonlocal calls
        calls += 1
        current = specs[0]
        result = OperationResult(
            current.name,
            list(current.argv),
            75,
            0.1,
            "warning: Emergency memory guard stopped Gradle after hard emergency floor: 165 MiB available",
            [],
            failure_kind="memory_guard",
            metadata=dict(current.metadata),
        )
        return [result], result.output

    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("devtool.cli.Confirm.ask") as confirm,
        patch("devtool.cli.render_summary"),
        patch("devtool.cli._run_specs_for_args", side_effect=fake_run),
    ):
        rc = main(["--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next"])

    assert rc == 0
    assert calls == 1
    confirm.assert_not_called()
    session = load_active_session(tmp_path, required=True)
    assert session.phases[0].status == "interrupted"



def test_cleanup_failure_does_not_replace_phase_verdict(tmp_path: Path) -> None:
    make_project(tmp_path)
    (tmp_path / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")

    def fake_run(_config, specs, _args, **_kwargs):
        current = specs[0]
        phase_result = OperationResult(current.name, list(current.argv), 0, 0.1, "phase ok", [], metadata=dict(current.metadata))
        cleanup_result = OperationResult(
            "gradle-daemon-shutdown", ["gradle", "--stop"], 127, 0.1, "cleanup failed", [],
            failure_kind="cleanup_failure", metadata={"cleanup": True},
        )
        return [phase_result, cleanup_result], "phase ok\ncleanup failed"

    with patch("devtool.cli.render_summary"), patch("devtool.cli._run_specs_for_args", side_effect=fake_run):
        rc = main(["--repo", str(tmp_path), "--no-clipboard", "validate", "start", "--next"])
    assert rc == 0
    session = load_active_session(tmp_path, required=True)
    assert session.phases[0].status == "passed"
    assert session.phases[0].last_verdict == "passed"


def test_schema_v2_session_migrates_to_graph_schema(tmp_path: Path) -> None:
    make_project(tmp_path)
    session = create_session(config_for(tmp_path), [spec("compile")], persist=True)
    path = session_path(tmp_path, session.session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    payload.pop("plan_fingerprint", None)
    payload.pop("required_checkpoint_keys", None)
    for phase in payload["phases"]:
        phase.pop("depends_on", None)
        phase.pop("input_domains", None)
        phase.pop("scope", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_session(tmp_path, session.session_id)
    assert loaded.as_dict()["schema_version"] == SESSION_SCHEMA_VERSION
    assert loaded.phases[0].input_domains
    assert loaded.phases[0].scope == "repository"


def test_parser_exposes_reset_acceptance_and_retry_policy() -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "continue", "--max-infrastructure-retries", "2", "--max-resource-retries", "3"])
    assert args.max_infrastructure_retries == 2
    assert args.max_resource_retries == 3
    reset = parser.parse_args(["validate", "reset", "--from-phase", "compile"])
    assert reset.validate_scope == "reset"
    accepted = parser.parse_args(["validate", "accept-plan-change", "--reason", "reviewed"])
    assert accepted.validate_scope == "accept-plan-change"
