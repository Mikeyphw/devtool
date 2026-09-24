from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.cli import main
from devtool.core.events import EventBus, RunFinished, RunStarted
from devtool.core.execution_state import ExecutionRunState, ExecutionSnapshot


def test_run_started_persists_invocation_origin_in_snapshot() -> None:
    bus = EventBus()
    bus.publish(RunStarted(
        run_id="wrap-origin",
        command="invoke",
        project="/repo",
        source="wrapper",
        metadata={
            "invocation_source": "wrapper",
            "invocation_command": "codegen",
            "wrapper_schema": 1,
            "wrapper_command": "codegen",
        },
    ))
    snapshot = bus.execution_store.snapshot("wrap-origin")
    assert snapshot is not None
    assert snapshot.invocation_source == "wrapper"
    assert snapshot.invocation_command == "codegen"
    assert snapshot.invocation_metadata["wrapper_schema"] == 1
    assert snapshot.to_dict()["invocation_source"] == "wrapper"
    assert snapshot.to_dict()["invocation_command"] == "codegen"
    assert snapshot.to_dict()["invocation_metadata"]["wrapper_command"] == "codegen"


def test_execution_snapshot_defaults_invocation_fields_for_legacy_state() -> None:
    snapshot = ExecutionSnapshot.from_state(ExecutionRunState(run_id="legacy"))
    document = snapshot.to_dict()
    assert snapshot.schema_version == 7
    assert document["invocation_source"] == ""
    assert document["invocation_command"] == ""
    assert document["invocation_metadata"] == {}


def test_cli_internal_wrapper_provenance_reaches_run_started(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import devtool.cli as cli

    events = []

    monkeypatch.setattr(cli, "publish_event", events.append)
    monkeypatch.setattr(cli, "finalize_execution_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_main_after_parse", lambda *args, **kwargs: 0)
    code = cli.main([
        "--repo",
        str(tmp_path),
        "--internal-invocation-source",
        "wrapper",
        "--internal-invocation-command",
        "codegen",
        "--internal-wrapper-schema",
        "1",
        "invoke",
        "codegen",
    ])
    assert code == 0
    assert events
    event = events[0]
    assert event.source == "wrapper"
    assert event.command == "invoke"
    assert event.metadata["invocation_source"] == "wrapper"
    assert event.metadata["invocation_command"] == "codegen"
    assert event.metadata["wrapper_schema"] == "1"
    assert event.metadata["wrapper_command"] == "codegen"


def test_cli_rejects_unsafe_internal_invocation_tokens(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import devtool.cli as cli

    monkeypatch.setattr(cli, "_main_after_parse", lambda *args, **kwargs: 0)
    code = cli.main([
        "--repo",
        str(tmp_path),
        "--internal-invocation-source",
        "wrapper with spaces",
        "build",
    ])
    assert code == 1
