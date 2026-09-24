from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from rich.console import Console

from devtool import cli
from devtool.completion import ZSH_COMPLETION
from devtool.completion_state import build_completion_state, completion_values
from devtool.config import load_project_config
from devtool.desktop.ui_semantics import build_desktop_semantic_view
from devtool.federation_observability import build_federation_observability, runtime_federation_projection
from devtool.ui_core.machine import machine_state_from_runtime
from devtool.ui_core.viewmodels import runtime_state_to_ui_snapshot
from devtool.tui import TerminalProfile, _dashboard


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(
        '''[project]
runner="command"
[federation]
schema=1
[federation.repositories.child]
path="../child"
[federation.groups.product]
repositories=["child"]
[federation.workflows.release]
depends_on=["child::release"]
''', encoding="utf-8")
    (child / ".devtool.toml").write_text(
        '''[project]
runner="command"
[workspace]
default_target="app"
[targets.app]
runner="command"
root="."
[targets.app.jobs.lint]
runner="command"
command=["echo","lint"]
[targets.app.workflows]
release=["job:lint"]
[wrapper.commands.build]
devtool="build"
target="app"
''', encoding="utf-8")
    return parent, child


def _execution() -> dict:
    return {
        "schema_version": 7,
        "run_id": "parent-run",
        "status": "running",
        "command": "federation run release",
        "project": "/parent",
        "repository_identity": {"root": "/parent", "fingerprint": "parent-fp"},
        "run_lineage": {
            "kind": "federation", "relationship": "coordinator",
            "child_run_id": "parent-run", "federation_workflow": "release",
            "source_repository": "parent-fp", "effective_repository": "parent-fp",
        },
        "nodes": [{
            "node_id": "federation:release:android",
            "label": "android::release",
            "kind": "federation-child",
            "status": "running",
            "depends_on": [],
            "blocked_by": [],
            "metadata": {
                "federation_step": "android",
                "repository": "android",
                "workflow": "release",
                "child_run_id": "child-run",
                "repository_identity": {"root": "/android", "fingerprint": "android-fp"},
                "run_lineage": {
                    "relationship": "child", "parent_run_id": "parent-run",
                    "child_run_id": "child-run", "federation_workflow": "release",
                    "federation_step": "android", "repository": "android",
                },
                "federation_child_repository": "android",
                "federation_child_repository_fingerprint": "android-fp",
                "federation_child_local_root": "/android",
                "federation_child_remote_root": "/work/android",
                "federation_execution_environment": "ssh",
                "federation_execution_provider": "ssh-native",
                "federation_parent_run_id": "parent-run",
                "federation_parent_node_id": "federation:release:android",
                "federation_child_run_id": "child-run",
                "federation_artifact_return_root": "/android/.devtool/federation-returns/android/child-run",
                "federation_path_mapping": "ssh-workspace",
                "operational_ownership": {
                    "references": [{"kind": "artifact-registry", "identifier": "child-run"}],
                },
            },
        }],
        "diagnostics": [], "artifacts": [], "processes": [], "metrics": {},
        "counts": {}, "accounting": {}, "started_at": "", "finished_at": "",
        "exit_code": 0, "duration_seconds": 0.0, "last_sequence": 1,
        "last_event_id": "e1", "graph_sources": [], "graph_sealed": True,
        "graph_sealed_at": "", "graph_seal_reason": "",
    }


def test_static_observability_unifies_boundaries_workflows_and_public_contracts(tmp_path: Path) -> None:
    parent, child = _fixture(tmp_path)
    payload = build_federation_observability(_load(parent), execution_snapshots=[]).as_dict()
    assert payload["schema_version"] == 1
    assert payload["healthy"] is True
    assert payload["summary"]["repositories"] == 1
    assert payload["summary"]["workflows"] == 1
    repo = payload["repositories"][0]
    assert repo["root"] == str(child.resolve())
    assert repo["public_contracts"]["wrapper_commands"] == ["build"]
    assert repo["public_contracts"]["target_local_jobs"] == ["app:job:lint"]
    assert payload["workflows"][0]["status"] == "ok"
    assert payload["workflows"][0]["node_count"] >= 1
    assert payload["gates"]["gate1"]["ok"] is True
    assert payload["gates"]["gate2"]["ok"] is True


def test_runtime_projection_is_repo_aware_and_redaction_safe() -> None:
    payload = runtime_federation_projection(_execution())
    assert payload["relationship"] == "coordinator"
    assert payload["workflow"] == "release"
    assert payload["summary"]["children"] == 1
    child = payload["children"][0]
    assert child["repository"] == "android"
    assert child["child_run_id"] == "child-run"
    assert child["transport"]["child_remote_root"] == "/work/android"
    assert child["transport"]["path_mapping"] == "ssh-workspace"
    assert child["operational_reference_kinds"] == ["artifact-registry"]
    assert "argv" not in json.dumps(payload)


def test_machine_and_desktop_semantic_views_share_federation_projection() -> None:
    raw = {
        "run_id": "parent-run", "status": "running", "command": "federation run release",
        "execution": _execution(),
    }
    machine = machine_state_from_runtime(raw).as_dict()
    assert machine["schema_version"] == 2
    assert machine["federation"]["relationship"] == "coordinator"
    assert machine["federation"]["children"][0]["repository"] == "android"

    ui = runtime_state_to_ui_snapshot(raw, surface="desktop").as_dict()
    desktop = build_desktop_semantic_view(ui).as_dict()
    assert desktop["federation"]["workflow"] == "release"
    assert desktop["federation"]["children"][0]["child_run_id"] == "child-run"


def test_tui_dashboard_adds_one_compact_federation_panel_not_a_new_destination() -> None:
    snapshot = {
        "repo": "parent", "root": "/parent", "branch": "main", "commit": "abc123",
        "dirty": False, "dirty_count": 0, "artifacts": [], "history": [], "live": {}, "core_events": [],
        "federation": {
            "enabled": True,
            "summary": {"healthy": True, "repositories": 2, "available_repositories": 2, "workflows": 1},
            "repositories": [{"name": "android"}, {"name": "backend"}],
        },
    }
    console = Console(record=True, width=80, force_terminal=False)
    console.print(_dashboard(snapshot, TerminalProfile(80, 24, "narrow", False)))
    text = console.export_text()
    assert "Federation" in text
    assert "2/2 available" in text
    assert "android, backend" in text


def test_completion_state_and_zsh_expose_federation_names(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    config = _load(parent)
    state = build_completion_state(config)
    assert state.federation_repositories == ["child"]
    assert state.federation_groups == ["product"]
    assert state.federation_workflows == ["release"]
    assert completion_values(config, "federation-repositories") == ["child"]
    assert completion_values(config, "federation-workflows") == ["release"]
    assert "_devtool_federation_repositories()" in ZSH_COMPLETION
    assert "federation|fed)" in ZSH_COMPLETION
    assert "status summary plan run exec" in ZSH_COMPLETION


def test_federation_status_json_and_top_level_status_share_observability(tmp_path: Path, capsys) -> None:
    parent, _ = _fixture(tmp_path)
    env = {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}
    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(parent), "federation", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    assert payload["healthy"] is True
    assert payload["repositories"][0]["public_contracts"]["wrapper_commands"] == ["build"]

    config = _load(parent)
    top = cli._status_payload(config)
    assert top["federation"]["schema_version"] == 1
    assert top["federation"]["workflows"][0]["name"] == "release"


def test_run_observability_marks_orphan_children_without_guessing_parent_state(tmp_path: Path) -> None:
    parent, _ = _fixture(tmp_path)
    child_execution = _execution()
    child_execution["run_id"] = "orphan-child"
    child_execution["nodes"] = []
    child_execution["run_lineage"] = {
        "kind": "federation", "relationship": "child", "parent_run_id": "missing-parent",
        "child_run_id": "orphan-child", "federation_workflow": "release", "repository": "child",
    }
    payload = build_federation_observability(_load(parent), execution_snapshots=[child_execution]).as_dict()
    assert payload["summary"]["orphan_child_runs"] == 1
    assert payload["runs"][0]["orphan"] is True
    assert payload["runs"][0]["parent_run_id"] == "missing-parent"


def test_doctor_exposes_federation_observability_contracts(tmp_path: Path) -> None:
    from devtool.doctor import collect_doctor_checks
    parent, _ = _fixture(tmp_path)
    checks = [item for item in collect_doctor_checks(_load(parent)) if item.section == "federation"]
    by_name = {item.name: item for item in checks}
    assert by_name["observability"].status == "ok"
    assert by_name["canonical-roots"].status == "ok"
    assert by_name["workflow-plans"].status == "ok"
    assert by_name["public-contracts"].status == "ok"
    assert by_name["remote-mappings"].status in {"ok", "optional"}
