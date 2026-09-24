from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from devtool import cli
from devtool.completion import ZSH_COMPLETION
from devtool.config import load_project_config
from devtool.doctor import collect_doctor_checks
from devtool.federation_final_seal import (
    FEDERATION_FINAL_SEAL_AREAS,
    FEDERATION_FINAL_SEAL_SAFETY_CASES,
    build_federation_final_seal_report,
    federation_final_seal_as_dict,
)


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _write_repo(root: Path, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text(text, encoding="utf-8")


def _healthy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    _write_repo(parent, '''[project]
runner="command"
[workspace]
default_target="app"
[targets.app]
runner="command"
root="."
[wrapper.commands.build]
devtool="build"
target="app"
[federation]
schema=1
[federation.repositories.child]
path="../child"
[federation.groups.product]
repositories=["child"]
[federation.workflows.release]
depends_on=["child::release"]
''')
    _write_repo(child, '''[project]
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
''')
    return parent, child


def test_final_seal_standalone_has_exact_roadmap_inventory(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "standalone"
    _write_repo(repo, '[project]\nrunner="command"\n')
    report = build_federation_final_seal_report(_load(repo))
    payload = federation_final_seal_as_dict(report)
    assert report.ok is True
    assert report.mode == "standalone"
    assert tuple(item.area for item in report.checks) == FEDERATION_FINAL_SEAL_AREAS
    assert payload["required_areas"] == list(FEDERATION_FINAL_SEAL_AREAS)
    assert len(payload["required_safety_cases"]) == len(FEDERATION_FINAL_SEAL_SAFETY_CASES) == 20
    assert payload["summary"]["errors"] == 0

    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(repo / "missing-global.toml")}, clear=False):
        assert cli.main(["--repo", str(repo), "federation", "final-seal", "--json"]) == 0
    raw = json.loads(capsys.readouterr().out)
    machine = raw.get("data", raw)
    assert machine["gate"] == "FRX-12"
    assert machine["ok"] is True


def test_healthy_federation_qualifies_all_fourteen_areas(tmp_path: Path) -> None:
    parent, _ = _healthy_fixture(tmp_path)
    report = build_federation_final_seal_report(_load(parent))
    assert report.ok is True
    assert report.mode == "federated"
    assert len(report.checks) == 14
    assert all(item.status == "ok" for item in report.checks)
    by_area = {item.area: item for item in report.checks}
    assert by_area["graph-planning"].data["workflows"] == 1
    assert by_area["wrapper-bridge"].status == "ok"
    assert by_area["target-local-job-bridge"].status == "ok"
    assert by_area["local-remote-identity"].status == "ok"
    assert by_area["operational-ownership"].status == "ok"


def test_deep_explicit_registration_cycle_a_b_c_a_fails_final_seal(tmp_path: Path) -> None:
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    _write_repo(a, '''[project]
runner="command"
[federation]
schema=1
[federation.repositories.b]
path="../b"
''')
    _write_repo(b, '''[project]
runner="command"
[federation]
schema=1
[federation.repositories.c]
path="../c"
''')
    _write_repo(c, '''[project]
runner="command"
[federation]
schema=1
[federation.repositories.a]
path="../a"
''')
    report = build_federation_final_seal_report(_load(a))
    by_area = {item.area: item for item in report.checks}
    assert report.ok is False
    assert by_area["cycle-rejection"].status == "error"
    cycles = by_area["cycle-rejection"].data["cycles"]
    assert cycles and any("@" in token for token in cycles[0])


def test_non_git_child_is_allowed_and_unenrolled_nested_git_is_not_discovered(tmp_path: Path) -> None:
    parent, child = _healthy_fixture(tmp_path)
    nested = parent / "tools" / "not-enrolled"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    # The enrolled child deliberately has no .git directory.
    assert not (child / ".git").exists()
    report = build_federation_final_seal_report(_load(parent))
    assert report.ok is True
    discovery = {item.area: item for item in report.checks}["discovery"]
    roots = list(discovery.data.get("repository_roots") or [])
    assert str(child.resolve()) in roots
    assert str(nested.resolve()) not in roots


def test_same_target_and_wrapper_names_remain_repository_scoped(tmp_path: Path) -> None:
    parent, child = _healthy_fixture(tmp_path)
    # Fixture intentionally declares target=app and wrapper=build in both repos.
    parent_cfg = _load(parent)
    child_cfg = _load(child)
    assert parent_cfg.selected_target == child_cfg.selected_target == "app"
    assert "build" in parent_cfg.wrapper.commands and "build" in child_cfg.wrapper.commands
    report = build_federation_final_seal_report(parent_cfg)
    assert report.ok is True


def test_launch_from_child_with_parent_repo_flag_resolves_parent_federation(tmp_path: Path, monkeypatch, capsys) -> None:
    parent, child = _healthy_fixture(tmp_path)
    monkeypatch.chdir(child)
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}, clear=False):
        assert cli.main(["--repo", str(parent), "federation", "seal", "--json"]) == 0
    raw = json.loads(capsys.readouterr().out)
    payload = raw.get("data", raw)
    assert payload["repository"] == str(parent.resolve())
    assert payload["mode"] == "federated"


def test_doctor_and_completion_expose_final_seal(tmp_path: Path) -> None:
    parent, _ = _healthy_fixture(tmp_path)
    checks = [item for item in collect_doctor_checks(_load(parent)) if item.section == "federation"]
    by_name = {item.name: item for item in checks}
    assert by_name["final-seal"].status == "ok"
    assert "final-seal seal status summary plan run exec" in ZSH_COMPLETION
    assert "final-seal|seal)" in ZSH_COMPLETION or "execution-preflight|final-seal|seal)" in ZSH_COMPLETION

def test_cancellation_during_child_environment_transition_restores_parent_environment(tmp_path: Path, monkeypatch) -> None:
    from devtool.federation_execution import run_federated_workflow

    parent, _ = _healthy_fixture(tmp_path)
    config = _load(parent)
    requested: list[str] = []
    observed: list[tuple[str, str]] = []
    monkeypatch.setenv("DEVTOOL_RUN_ID", "outer-run")
    monkeypatch.delenv("DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT", raising=False)

    def interrupt_during_transition(_argv: list[str]) -> int:
        child_run_id = os.environ["DEVTOOL_RUN_ID"]
        observed.append((child_run_id, os.environ.get("DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT", "")))
        # Simulate the child provider moving from auto selection into an SSH
        # transport immediately before user cancellation arrives.
        os.environ["DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT"] = "ssh"
        raise KeyboardInterrupt

    report = run_federated_workflow(
        config,
        "release",
        reenter=interrupt_during_transition,
        parent_run_id="frx12-cancel-parent",
        request_child_cancel=lambda run_id: requested.append(run_id),
    )
    assert report.status == "cancelled" and report.exit_code == 130
    assert observed and observed[0][1] == "auto"
    assert requested == [observed[0][0]]
    assert os.environ["DEVTOOL_RUN_ID"] == "outer-run"
    assert "DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT" not in os.environ

