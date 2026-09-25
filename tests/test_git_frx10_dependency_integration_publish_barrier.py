from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devtool.config import load_project_config
from devtool.federation_execution import run_federated_workflow
from devtool.federation_resume import checkpoint_path
from devtool.git_federation_integration import integrate_federation_checkpoint


def _git(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True, timeout=10)
    (root / "source.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True, timeout=10)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _child_config(root: Path) -> None:
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\nmetadata={read_only=true}\n[targets.app.workflows]\nvalidate=["job:check"]\npublish=["job:check"]\n''',
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, policy: str = "per-repo", barrier: bool = True):
    parent = tmp_path / "parent"; a = tmp_path / "a"; b = tmp_path / "b"
    parent.mkdir(); a.mkdir(); b.mkdir()
    for root in (a, b):
        _child_config(root)
        _git(root)
    (parent / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.repositories.b]\npath="../b"\n[federation.workflows.release]\nintegration_policy="{policy}"\npublish_barrier={str(barrier).lower()}\nsteps=[\n  {{id="library",ref="a::app::workflow:validate",stage="prepare"}},\n  {{id="consumer",ref="b::app::workflow:validate",stage="prepare",depends_on=["library"]}},\n  {{id="publish",ref="a::app::workflow:publish",stage="publish"}},\n]\n''',
        encoding="utf-8",
    )
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    return config, a, b


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: root)
    monkeypatch.setattr("devtool.git_federation_integration.state_dir", lambda: root)
    return root


def _branch(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "symbolic-ref", "HEAD"], text=True).strip()


def _make_result(root: Path) -> tuple[str, str, str]:
    start = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    ref = _branch(root)
    (root / "source.txt").write_text("result\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "validated result"], check=True, timeout=10)
    result = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(root), "reset", "--hard", "-q", start], check=True, timeout=10)
    return start, result, ref


def _write_checkpoint(config, rows: list[dict], run_id: str = "parent-10") -> None:
    path = checkpoint_path(config, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "devtool.federation.resume-checkpoint",
        "parent_run_id": run_id,
        "workflow": "release",
        "group": "",
        "status": "succeeded",
        "exit_code": 0,
        "children": rows,
    }), encoding="utf-8")


def _row(step: str, repository: str, start: str, result: str, ref: str, *, status: str = "succeeded") -> dict:
    return {
        "step_id": step, "repository": repository, "workflow": "validate", "target": "app",
        "run_id": f"child-{step}", "status": status, "exit_code": 0 if status == "succeeded" else 1,
        "git_lineage": {
            "start_commit": start, "result_commit": result, "planned_head_ref": ref,
            "result_ref": f"refs/devtool/results/{repository}",
        },
    }


def test_config_injects_prepare_dependencies_into_publish_barrier(tmp_path: Path) -> None:
    config, _a, _b = _fixture(tmp_path)
    workflow = config.federation.workflows["release"]
    assert workflow.publish_barrier is True
    assert workflow.integration_policy == "per-repo"
    publish = next(step for step in workflow.steps if step.step_id == "publish")
    assert publish.stage == "publish"
    assert set(publish.depends_on) == {"library", "consumer"}


def test_failed_prepare_keeps_publish_barrier_closed_without_rollback(tmp_path: Path) -> None:
    config, _a, _b = _fixture(tmp_path)
    calls: list[str] = []
    def reenter(argv: list[str]) -> int:
        # library succeeds, consumer fails; publish must never be entered.
        calls.append(" ".join(argv))
        return 1 if len(calls) == 2 else 0
    report = run_federated_workflow(config, "release", reenter=reenter)
    assert report.status == "failed"
    by_step = {child.step_id: child for child in report.children}
    assert by_step["library"].status == "succeeded"
    assert by_step["consumer"].status == "failed"
    assert by_step["publish"].status == "blocked"
    barrier = report.lifecycle["publish_barrier"]
    assert barrier["state"] == "closed"
    assert barrier["qualified_prepare_steps"] == ["library"]
    assert barrier["blocking_prepare_steps"] == ["consumer"]
    assert barrier["child_publish_owned"] is True
    assert barrier["federation_rollback_published_release"] is False
    assert len(calls) == 2


def test_per_repo_integration_moves_validated_checked_out_branch_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, a, _b = _fixture(tmp_path, barrier=False)
    start, result, ref = _make_result(a)
    _write_checkpoint(config, [_row("library", "a", start, result, ref)])
    dry = integrate_federation_checkpoint(config, "parent-10", dry_run=True)
    assert dry.status == "planned"
    assert dry.pointers[0].status == "planned"
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == start
    applied = integrate_federation_checkpoint(config, "parent-10", dry_run=False)
    assert applied.status == "succeeded"
    assert applied.pointers[0].status == "integrated"
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == result
    assert applied.automatic_push is False
    assert applied.published_release_rollback_owned is False


def test_all_or_nothing_withholds_every_pointer_when_one_repository_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, a, b = _fixture(tmp_path, policy="all-or-nothing-pointer-update", barrier=False)
    a_start, a_result, a_ref = _make_result(a)
    b_start, b_result, b_ref = _make_result(b)
    _write_checkpoint(config, [
        _row("library", "a", a_start, a_result, a_ref),
        _row("consumer", "b", b_start, b_result, b_ref, status="failed"),
    ])
    report = integrate_federation_checkpoint(config, "parent-10", dry_run=False)
    assert report.status == "blocked"
    by_repo = {item.repository: item for item in report.pointers}
    assert by_repo["a"].status == "withheld"
    assert by_repo["b"].status == "blocked"
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == a_start
    assert subprocess.check_output(["git", "-C", str(b), "rev-parse", "HEAD"], text=True).strip() == b_start


def test_cli_and_completion_expose_explicit_integration_apply_surface() -> None:
    from devtool.cli import build_parser
    from devtool.completion import ZSH_COMPLETION
    parser = build_parser()
    args = parser.parse_args(["federation", "integrate", "parent-10", "--policy", "dependency-group", "--apply", "--json"])
    assert args.federation_command == "integrate"
    assert args.run_id == "parent-10"
    assert args.policy == "dependency-group"
    assert args.apply is True and args.json is True
    assert "status summary plan run exec resume retry lock integrate" in ZSH_COMPLETION
    assert "--apply[apply validated branch pointer integration]" in ZSH_COMPLETION


def _independent_fixture(tmp_path: Path, *, policy: str = "dependency-group"):
    parent = tmp_path / "independent-parent"; a = tmp_path / "independent-a"; b = tmp_path / "independent-b"
    parent.mkdir(); a.mkdir(); b.mkdir()
    for root in (a, b):
        _child_config(root)
        _git(root)
    (parent / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../independent-a"\n[federation.repositories.b]\npath="../independent-b"\n[federation.workflows.release]\nintegration_policy="{policy}"\nsteps=[\n  {{id="a-check",ref="a::app::workflow:validate"}},\n  {{id="b-check",ref="b::app::workflow:validate"}},\n]\n''',
        encoding="utf-8",
    )
    config = load_project_config(
        parent, parent / ".devtool.toml", target_name="",
        allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
    )
    return config, a, b


def test_dependency_group_failure_does_not_rollback_unrelated_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state(monkeypatch, tmp_path)
    config, a, b = _independent_fixture(tmp_path)
    a_start, a_result, a_ref = _make_result(a)
    b_start, b_result, b_ref = _make_result(b)
    _write_checkpoint(config, [
        _row("a-check", "a", a_start, a_result, a_ref),
        _row("b-check", "b", b_start, b_result, b_ref, status="failed"),
    ])
    report = integrate_federation_checkpoint(config, "parent-10", dry_run=False)
    by_repo = {item.repository: item for item in report.pointers}
    assert report.status == "partial"
    assert by_repo["a"].status == "integrated"
    assert by_repo["b"].status == "blocked"
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == a_result
    assert subprocess.check_output(["git", "-C", str(b), "rev-parse", "HEAD"], text=True).strip() == b_start
    assert {group["status"] for group in report.groups} == {"succeeded", "blocked"}


def test_group_pointer_failure_rolls_back_only_already_moved_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import devtool.git_federation_integration as integration

    _state(monkeypatch, tmp_path)
    config, a, b = _fixture(tmp_path, policy="all-or-nothing-pointer-update", barrier=False)
    a_start, a_result, a_ref = _make_result(a)
    b_start, b_result, b_ref = _make_result(b)
    _write_checkpoint(config, [
        _row("library", "a", a_start, a_result, a_ref),
        _row("consumer", "b", b_start, b_result, b_ref),
    ])
    original_move = integration._move_pointer

    def fail_second(item, *, forward: bool) -> None:
        if forward and item.repository == "b":
            raise integration.FederationIntegrationError("simulated pointer update failure")
        original_move(item, forward=forward)

    monkeypatch.setattr(integration, "_move_pointer", fail_second)
    report = integrate_federation_checkpoint(config, "parent-10", dry_run=False)
    by_repo = {item.repository: item for item in report.pointers}
    assert report.status == "blocked"
    assert by_repo["a"].status == "rolled-back"
    assert by_repo["a"].rolled_back is True
    assert by_repo["b"].status == "blocked"
    assert "simulated pointer update failure" in by_repo["b"].reason
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == a_start
    assert subprocess.check_output(["git", "-C", str(b), "rev-parse", "HEAD"], text=True).strip() == b_start


def test_dirty_checked_out_target_branch_blocks_pointer_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state(monkeypatch, tmp_path)
    config, a, _b = _fixture(tmp_path, barrier=False)
    start, result, ref = _make_result(a)
    (a / "local-edit.txt").write_text("do not overwrite\n", encoding="utf-8")
    _write_checkpoint(config, [_row("library", "a", start, result, ref)])
    report = integrate_federation_checkpoint(config, "parent-10", dry_run=False)
    assert report.status == "blocked"
    assert report.pointers[0].status == "blocked"
    assert "dirty worktree" in report.pointers[0].reason
    assert subprocess.check_output(["git", "-C", str(a), "rev-parse", "HEAD"], text=True).strip() == start
    assert (a / "local-edit.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_publish_barrier_rejects_prepare_that_depends_on_publish(tmp_path: Path) -> None:
    parent = tmp_path / "cycle-parent"; a = tmp_path / "cycle-a"; b = tmp_path / "cycle-b"
    parent.mkdir(); a.mkdir(); b.mkdir()
    for root in (a, b):
        _child_config(root)
        _git(root)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../cycle-a"\n[federation.repositories.b]\npath="../cycle-b"\n[federation.workflows.release]\npublish_barrier=true\nsteps=[\n  {id="prepare",ref="a::app::workflow:validate",stage="prepare",depends_on=["publish"]},\n  {id="publish",ref="b::app::workflow:publish",stage="publish"},\n]\n''',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="publish_barrier would create a dependency cycle"):
        load_project_config(
            parent, parent / ".devtool.toml", target_name="",
            allow_ambiguous_target=True, allow_ambiguous_project_kind=True,
        )
