"""Regression guards for the hosted Devtool run 38517845080.

Fail-closed requirements: an unstarted federation step can be checkpointed
without inventing a child run, but successful/failed executed children must
still have a real run ID.  CI must install the PTY test dependency.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from devtool.federation_execution import FederatedRunReport
from devtool.federation_plan import build_federated_plan
from devtool.federation_resume import FederationResumeError, checkpoint_report, build_resume_plan
from test_frx_y02_federation_resume_checkpoint import _fixture, _children, _state


def test_unstarted_blocked_step_checkpoint_is_not_reusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _, _ = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    children = list(_children(config, plan))
    blocked = replace(children[-1], run_id="", status="blocked", exit_code=1)
    children[-1] = blocked
    payload = checkpoint_report(config, FederatedRunReport("release", "parent-blocked", "failed", 1, tuple(children), plan))
    assert payload["children"][-1]["status"] == "blocked"
    assert payload["children"][-1]["run_id"] == ""
    decisions = build_resume_plan(config, payload, mode="resume").decisions
    assert decisions[-1].action != "reused"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_executed_child_cannot_hide_missing_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    _state(monkeypatch, tmp_path)
    config, _, _ = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    children = list(_children(config, plan))
    children[0] = replace(children[0], run_id="", status=status, exit_code=0 if status == "succeeded" else 1)
    with pytest.raises(FederationResumeError, match="missing child identity"):
        checkpoint_report(config, FederatedRunReport("release", "parent-missing", "failed", 1, tuple(children), plan))


def test_full_matrix_installs_interactive_test_dependency() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/devtool-full-repository-pytest.yml").read_text()
    assert "python -m pip install -e '.[textual]' pytest pexpect" in workflow
