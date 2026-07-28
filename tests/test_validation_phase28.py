from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.cli import _normalize_command_aliases, build_parser, main
from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.validation_control import (
    EXIT_CONTROL_UNAVAILABLE,
    EXIT_SESSION_BLOCKED,
    EXIT_STALE_REVISION,
    require_revision,
    status_api_payload,
)
from devtool.validation_sessions import (
    ValidationControlRequestError,
    append_event,
    clear_control_request,
    create_session,
    load_active_session,
    load_session,
    mark_phase_running,
    read_events,
    record_phase_result,
    save_session,
    status_payload,
    write_control_request,
)


def make_project(root: Path) -> ProjectConfig:
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
    (root / ".devtool.toml").write_text('[project]\nkind="android"\nproject="."\n', encoding="utf-8")
    return ProjectConfig(repo_root=root, project_root=str(root), kind="android")


def validation_specs() -> list[CommandSpec]:
    return [
        CommandSpec(
            "preflight",
            ["true"],
            metadata={"validation_phase_name": "preflight", "validation_depends_on": []},
        ),
        CommandSpec(
            "compile",
            ["true"],
            metadata={"validation_phase_name": "compile", "validation_depends_on": ["preflight"]},
        ),
        CommandSpec(
            "lint",
            ["true"],
            metadata={"validation_phase_name": "lint", "validation_depends_on": ["compile"]},
        ),
    ]


def parse_single_json(output: str) -> dict[str, object]:
    return json.loads(output.strip())


def test_parser_exposes_phase28_nested_actions_and_scopes() -> None:
    parser = build_parser()
    samples = {
        "run": ["validate", "run", "--scope", "remaining", "--json"],
        "selected": ["validate", "run", "--scope", "selected", "--phase", "compile", "--phase", "lint", "--json"],
        "pause": ["validate", "pause", "--if-revision", "7", "--json"],
        "stop": ["validate", "stop", "--if-revision", "7", "--json"],
        "events": ["validate", "events", "--after-event", "abc", "--json"],
    }
    parsed = {}
    for name, argv in samples.items():
        args = parser.parse_args(argv)
        _normalize_command_aliases(args)
        parsed[name] = args
    assert parsed["run"]._devtool_validation_api_action == "run"
    assert parsed["run"].validation_run_scope == "remaining"
    assert parsed["selected"].validation_run_scope == "selected"
    assert parsed["selected"].phase == ["compile", "lint"]
    assert parsed["pause"].command == "validation-pause"
    assert parsed["stop"].command == "validation-stop"
    assert parsed["events"].command == "validation-events"


def test_revision_guard_reports_stale_tokens(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    assert require_revision(session, session.revision) == session.revision
    with pytest.raises(Exception, match="revision is stale"):
        require_revision(session, session.revision - 1)


def test_pause_request_is_idempotent_and_conflicting_control_is_rejected(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    phase = mark_phase_running(session, session.phases[0].key)
    revision = session.revision

    first = write_control_request(session, "pause-after-phase", expected_revision=revision)
    duplicate = write_control_request(session, "pause-after-phase", expected_revision=revision)
    assert duplicate["request_id"] == first["request_id"]
    assert duplicate["reused"] is True
    assert first["active_attempt_id"] == phase.current_attempt_id
    assert status_payload(session)["effective_state"] == "pausing"

    with pytest.raises(ValidationControlRequestError) as raised:
        write_control_request(session, "stop-now", expected_revision=session.revision)
    assert raised.value.reason == "control_unavailable"
    assert clear_control_request(session, request_id=first["request_id"])


def test_event_cursor_returns_contiguous_pages(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    initial = read_events(session)
    assert initial and initial[0]["type"] == "session-created"
    ids = []
    for index in range(5):
        ids.append(append_event(session, "test-event", data={"index": index})["event_id"])

    first_page = read_events(session, after_event_id=ids[0], limit=2)
    assert [event["data"]["index"] for event in first_page] == [1, 2]
    second_page = read_events(session, after_event_id=first_page[-1]["event_id"], limit=2)
    assert [event["data"]["index"] for event in second_page] == [3, 4]


def test_worker_save_advances_past_external_control_revision(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    worker_session = create_session(config, validation_specs())
    phase = mark_phase_running(worker_session, worker_session.phases[0].key)
    worker_revision = worker_session.revision

    control_session = load_session(tmp_path, worker_session.session_id)
    request = write_control_request(
        control_session,
        "pause-after-phase",
        expected_revision=worker_revision,
        requested_by="android-app",
    )
    assert request["accepted_revision"] > worker_revision

    recorded = record_phase_result(
        worker_session,
        phase.key,
        OperationResult("preflight", ["true"], 0, 0.01, "ok", []),
    )
    durable = load_session(tmp_path, worker_session.session_id)
    assert recorded.status == "passed"
    assert durable.revision > request["accepted_revision"]


def test_status_json_is_read_only_and_uses_blocked_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    session.phases[0].status = "failed"
    session.status = "blocked"
    save_session(session, active=True)
    before_revision = session.revision

    rc = main(["--repo", str(tmp_path), "--no-clipboard", "validate", "status", "--json"])
    payload = parse_single_json(capsys.readouterr().out)
    reloaded = load_active_session(tmp_path, required=True)

    assert rc == EXIT_SESSION_BLOCKED
    assert payload["accepted"] is True
    assert payload["reason"] == "session_blocked"
    assert payload["state"] == "blocked"
    assert payload["data"]["control_api"]["api_version"] == 1
    assert reloaded.revision == before_revision


def test_stale_mutation_returns_stable_error_envelope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())

    rc = main([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "reset", "--all",
        "--if-revision", str(session.revision - 1), "--json",
    ])
    payload = parse_single_json(capsys.readouterr().out)

    assert rc == EXIT_STALE_REVISION
    assert payload["accepted"] is False
    assert payload["reason"] == "stale_revision"
    assert payload["data"]["expected_revision"] == session.revision - 1
    assert payload["data"]["current_revision"] == session.revision


def test_stop_control_targets_active_attempt_and_conflicts_are_safe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    phase = mark_phase_running(session, session.phases[0].key)

    rc = main([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "stop",
        "--if-revision", str(session.revision), "--json",
    ])
    payload = parse_single_json(capsys.readouterr().out)
    assert rc == 0
    assert payload["effective_state"] == "stopping"
    request = payload["data"]["request"]
    assert request["active_attempt_id"] == phase.current_attempt_id

    conflict_rc = main([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "pause",
        "--if-revision", str(payload["revision"]), "--json",
    ])
    conflict = parse_single_json(capsys.readouterr().out)
    assert conflict_rc == EXIT_CONTROL_UNAVAILABLE
    assert conflict["accepted"] is False
    assert conflict["reason"] == "control_unavailable"


def test_selected_scope_runs_dependency_closure_and_emits_one_json_envelope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_project(tmp_path)
    specs = validation_specs()
    session = create_session(config, specs)

    def fake_run(_config, requested_specs, _args, **_kwargs):
        results = [
            OperationResult(item.name, list(item.argv), 0, 0.01, "ok", [], metadata=dict(item.metadata))
            for item in requested_specs
        ]
        return results, "\n".join(item.name for item in results)

    with patch("devtool.cli._validation_specs_for_args", return_value=specs), patch(
        "devtool.cli._run_specs_for_args", side_effect=fake_run
    ), patch("devtool.cli.render_summary"):
        rc = main([
            "--repo", str(tmp_path), "--no-clipboard", "validate", "run",
            "--scope", "selected", "--phase", "lint",
            "--if-revision", str(session.revision), "--json",
        ])

    output = capsys.readouterr().out.strip()
    payload = parse_single_json(output)
    assert rc == 0
    assert payload["accepted"] is True
    assert payload["action"] == "run"
    assert payload["data"]["scope"] == "selected"
    phase_results = [item["name"] for item in payload["data"]["results"] if item["name"] != "validation-session-complete"]
    assert phase_results == ["preflight", "compile", "lint"]
    assert payload["state"] in {"validated", "completed"}


def test_invalid_scope_contract_returns_invalid_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    rc = main([
        "--repo", str(tmp_path), "--no-clipboard", "validate", "run",
        "--scope", "through", "--if-revision", str(session.revision), "--json",
    ])
    payload = parse_single_json(capsys.readouterr().out)
    assert rc == 2
    assert payload["accepted"] is False
    assert payload["reason"] == "invalid_request"
    assert "requires --through" in payload["message"]


def test_status_payload_can_include_event_cursor(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    session = create_session(config, validation_specs())
    append_event(session, "custom", data={"value": 1})
    payload = status_api_payload(session, include_events=True, event_limit=10)
    assert payload["events"][-1]["type"] == "custom"
    assert payload["event_cursor"]["next_event_id"] == payload["events"][-1]["event_id"]
