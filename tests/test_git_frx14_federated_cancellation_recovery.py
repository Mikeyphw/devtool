from __future__ import annotations

import os
from pathlib import Path
import time
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.federation_execution import run_federated_workflow
from devtool.federation_resume import build_resume_plan, load_checkpoint


def _child(root: Path) -> None:
    root.mkdir(parents=True)
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[jobs.one]\nrunner="command"\ncommand=["true"]\noutputs=["out/**"]\n[targets.app]\nrunner="command"\nroot="."\n[targets.app.workflows]\nwork=["job:one"]\n''', encoding="utf-8",
    )


def _config(tmp_path: Path):
    parent = tmp_path / "parent"; parent.mkdir()
    for name in ("alpha", "beta", "join"):
        _child(tmp_path / name)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace.scheduler]\nmax_parallel_nodes=2\n[federation]\nschema=1\n[federation.repositories.alpha]\npath="../alpha"\n[federation.repositories.beta]\npath="../beta"\n[federation.repositories.join]\npath="../join"\n[federation.workflows.release]\nsteps=[{id="alpha",ref="alpha::work"},{id="beta",ref="beta::work"},{id="join",ref="join::work",depends_on=["alpha","beta"]}]\n''', encoding="utf-8",
    )
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}, clear=False):
        return load_project_config(parent, parent / ".devtool.toml", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_cancellation_checkpoint_preserves_success_reuse_and_invalidates_cancelled_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    config = _config(tmp_path)

    def executor(argv: list[str], _env: dict[str, str]) -> int:
        repo = argv[argv.index("--repo") + 1]
        if repo.endswith("alpha"):
            time.sleep(0.01)
            return 0
        time.sleep(0.04)
        return 130

    report = run_federated_workflow(
        config, "release", reenter=lambda _argv: 99, snapshot_lookup=lambda _run_id: None,
        parent_run_id="git-frx14-cancel", parallel=True, max_parallel=2, child_executor=executor,
    )
    assert report.status == "cancelled" and report.exit_code == 130
    recovery = report.lifecycle["cancellation_recovery"]
    assert recovery["stopped_new_scheduling"] is True
    assert recovery["reusable_steps"] == ["alpha"]
    invalidated = {row["step_id"] for row in recovery["invalidated_steps"]}
    assert invalidated == {"beta", "join"}
    assert recovery["rollback_ownership"] == "child-repository-owned"
    assert recovery["automatic_git_rollback"] is False

    checkpoint = load_checkpoint(config, report.parent_run_id)
    assert checkpoint["lifecycle"]["cancellation_recovery"]["reusable_steps"] == ["alpha"]
    assert checkpoint["schedule"]["parallel_requested"] is True
    resume = build_resume_plan(config, checkpoint, mode="resume")
    actions = {row.step_id: row.action for row in resume.decisions}
    assert actions["alpha"] == "reused"
    assert actions["beta"] == "retried"
    assert actions["join"] == "retried"


def test_cancellation_recovery_never_claims_parent_owned_git_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    config = _config(tmp_path)
    report = run_federated_workflow(
        config, "release", reenter=lambda _argv: 99, snapshot_lookup=lambda _run_id: None,
        parent_run_id="git-frx14-owner", parallel=True, max_parallel=1,
        child_executor=lambda _argv, _env: 130,
    )
    rows = report.lifecycle["cancellation_recovery"]["child_recovery"]
    assert rows
    assert all(row["rollback_owner"] == "child-repository" for row in rows)
    assert all(row["automatic_git_rollback"] is False for row in rows)
