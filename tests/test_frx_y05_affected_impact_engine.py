from __future__ import annotations

from pathlib import Path
import subprocess
import io
import json
from contextlib import redirect_stdout

from devtool.config import load_project_config
from devtool.cli import _federation_command, build_parser
from devtool.federation_impact import (
    analyze_federation_impact,
    explain_federation_impact,
    project_affected_workflow,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _child(root: Path, *, content: str = "base") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src.txt").write_text(content, encoding="utf-8")
    (root / ".devtool.toml").write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")


def _config(tmp_path: Path):
    parent = tmp_path / "parent"; parent.mkdir()
    for name in ("foundation", "library", "app", "unrelated"):
        _child(tmp_path / name)
    (parent / ".devtool.toml").write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.foundation]\npath="../foundation"\n[federation.repositories.foundation.exports]\ncapabilities=["securefs/v1"]\n[federation.repositories.library]\npath="../library"\n[federation.repositories.library.exports]\ncapabilities=["library-api/v1"]\n[federation.repositories.library.requires]\ncapabilities=["securefs/v1"]\n[federation.repositories.app]\npath="../app"\n[federation.repositories.app.requires]\ncapabilities=["library-api/v1"]\n[federation.repositories.unrelated]\npath="../unrelated"\n[federation.workflows.release]\nsteps=[\n {id="foundation",ref="foundation::app::validate"},\n {id="library",ref="library::app::validate"},\n {id="app",ref="app::app::validate"},\n {id="unrelated",ref="unrelated::app::validate"},\n]\n[federation.groups.stack]\nmembers=["workflow:release"]\n''', encoding="utf-8")
    return load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_y05_explicit_provider_change_propagates_direct_and_transitive_consumers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config, repositories=["foundation"])
    assert report.direct_repositories == ("foundation",)
    assert report.affected_repositories == ("app", "foundation", "library")
    assert report.direct_capabilities == ("securefs/v1",)
    assert report.affected_capabilities == ("library-api/v1", "securefs/v1")
    assert set(report.workflows) == {"release"}
    assert set(report.groups) == {"stack"}
    assert report.reasons["library"][0]["provider"] == "foundation"
    assert report.reasons["app"][0]["provider"] == "library"


def test_y05_does_not_infer_dependency_from_source_text_mentions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "unrelated" / "src.txt").write_text("mentions securefs/v1 but has no declared requirement", encoding="utf-8")
    report = analyze_federation_impact(config, repositories=["foundation"])
    assert "unrelated" not in report.affected_repositories


def test_y05_dirty_worktree_detection_finds_direct_repository_without_selector(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "foundation" / "src.txt").write_text("changed", encoding="utf-8")
    report = analyze_federation_impact(config)
    assert report.direct_repositories == ("foundation",)
    assert "app" in report.affected_repositories


def test_y05_since_revision_detects_child_change_when_revision_resolves(tmp_path: Path) -> None:
    config = _config(tmp_path)
    foundation = tmp_path / "foundation"
    base = subprocess.check_output(["git", "-C", str(foundation), "rev-parse", "HEAD"], text=True).strip()
    (foundation / "src.txt").write_text("next", encoding="utf-8")
    _git(foundation, "add", "."); _git(foundation, "commit", "-qm", "next")
    report = analyze_federation_impact(config, repositories=["foundation"], since=base)
    assert report.direct_repositories == ("foundation",)


def test_y05_why_returns_reason_path_for_repository_workflow_and_group(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config, repositories=["foundation"])
    repo = explain_federation_impact(report, "app")
    assert repo["affected"] and repo["reasons"][0]["provider"] == "library"
    workflow = explain_federation_impact(report, "release")
    assert workflow["affected"] and set(workflow["repositories"]) == {"foundation", "library", "app"}
    group = explain_federation_impact(report, "stack")
    assert group["affected"] and "app" in group["repositories"]


def test_y05_affected_workflow_projection_runs_only_affected_repositories(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config, repositories=["library"])
    scoped = project_affected_workflow(config, "release", report.affected_repositories)
    assert scoped is not None
    steps = scoped.federation.workflows["release"].steps
    assert {step.repository for step in steps} == {"library", "app"}
    assert "foundation" not in {step.repository for step in steps}
    assert "unrelated" not in {step.repository for step in steps}


def test_y05_empty_change_set_projects_to_no_work(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config)
    assert report.affected_repositories == ()
    assert project_affected_workflow(config, "release", report.affected_repositories) is None


def test_y05_cli_affected_and_why_surfaces_emit_reason_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    affected_args = build_parser().parse_args([
        "-r", str(config.repo_root), "federation", "affected",
        "--repository", "foundation", "--json",
    ])
    out = io.StringIO()
    with redirect_stdout(out):
        assert _federation_command(affected_args) == 0
    payload = json.loads(out.getvalue())
    assert payload["affected_repositories"] == ["app", "foundation", "library"]

    why_args = build_parser().parse_args([
        "-r", str(config.repo_root), "federation", "why", "app",
        "--repository", "foundation", "--json",
    ])
    out = io.StringIO()
    with redirect_stdout(out):
        assert _federation_command(why_args) == 0
    why = json.loads(out.getvalue())
    assert why["affected"] is True
    assert why["reasons"][0]["provider"] == "library"


def test_y05_run_affected_no_change_is_successful_noop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    args = build_parser().parse_args([
        "-r", str(config.repo_root), "federation", "run", "release",
        "--affected", "--json",
    ])
    out = io.StringIO()
    with redirect_stdout(out):
        assert _federation_command(args) == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "succeeded"
    assert payload["children"] == []
    assert payload["reason"] == "no affected federation steps"


def test_y05_transitive_reason_contains_complete_causal_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config, repositories=["foundation"])
    assert report.reasons["foundation"][0]["path"] == ["foundation"]
    assert report.reasons["library"][0]["path"] == ["foundation", "library"]
    assert report.reasons["app"][0]["path"] == ["foundation", "library", "app"]


def test_y05_since_revision_counts_untracked_files_as_direct_change(tmp_path: Path) -> None:
    config = _config(tmp_path)
    foundation = tmp_path / "foundation"
    base = subprocess.check_output(["git", "-C", str(foundation), "rev-parse", "HEAD"], text=True).strip()
    (foundation / "new.txt").write_text("new", encoding="utf-8")
    report = analyze_federation_impact(config, repositories=["foundation"], since=base)
    assert report.direct_repositories == ("foundation",)


def test_y05_since_revision_fails_closed_when_candidate_repo_cannot_resolve_revision(tmp_path: Path) -> None:
    from devtool.federation_impact import FederationImpactError
    import pytest

    config = _config(tmp_path)
    try:
        analyze_federation_impact(config, since="definitely-not-a-revision")
    except FederationImpactError as exc:
        assert "cannot be resolved" in str(exc)
        assert "foundation" in str(exc)
    else:
        raise AssertionError("unresolved --since revision must fail closed")


def test_y05_affected_projection_keeps_explicit_prerequisite_closure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from dataclasses import replace
    from devtool.models import FederatedWorkflowConfig, FederationConfig

    workflow = config.federation.workflows["release"]
    rewritten = []
    for step in workflow.steps:
        if step.step_id == "app":
            rewritten.append(replace(step, depends_on=("unrelated",), depends_on_declared=True))
        else:
            rewritten.append(step)
    federation = replace(config.federation, workflows={**config.federation.workflows, "release": FederatedWorkflowConfig(name="release", steps=tuple(rewritten))})
    config = replace(config, federation=federation)

    report = analyze_federation_impact(config, repositories=["library"])
    assert report.affected_repositories == ("app", "library")
    scoped = project_affected_workflow(config, "release", report.affected_repositories)
    assert scoped is not None
    steps = scoped.federation.workflows["release"].steps
    assert {step.repository for step in steps} == {"library", "app", "unrelated"}
    app = next(step for step in steps if step.step_id == "app")
    assert app.depends_on == ("unrelated",)
