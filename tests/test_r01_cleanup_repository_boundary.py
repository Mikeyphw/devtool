"""R01: project cleanup authority must not escape its repository boundary."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from devtool.cleanup_lifecycle import (
    CleanupScope,
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
)


def _view(repo: Path, candidate: Path):
    descriptor = GeneratedOutputDescriptor.create(
        candidate, owner="injected", provider="test-provider",
        category="scratch", producer_id="forged-managed",
        lifecycle="disposable", rebuild_cost="low", scope=CleanupScope.PROJECT,
        managed=True, published=False,
    )
    return build_cleanup_ownership_view(repo, [descriptor], policy_generation="r01-safety")


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_external_managed_candidate_is_protected_at_plan_and_apply(tmp_path: Path, kind: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "not-repo"
    outside.mkdir()
    candidate = outside / "private"
    if kind == "directory":
        candidate.mkdir()
        (candidate / "secret.txt").write_text("retain")
    else:
        candidate.write_text("retain")

    view = _view(repo, candidate)
    plan = compile_cleanup_plan(view)
    assert len(plan.candidates) == 1
    assert not plan.candidates[0].selected
    assert "out-of-repository" in plan.candidates[0].protection_reason
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert candidate.exists()
    if kind == "directory":
        assert (candidate / "secret.txt").read_text() == "retain"
    else:
        assert candidate.read_text() == "retain"


def test_project_root_cannot_be_selected_for_deletion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "source.py").write_text("keep")
    view = _view(repo, repo)
    plan = compile_cleanup_plan(view)
    assert not plan.candidates[0].selected
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert (repo / "source.py").exists()


def test_symlink_outside_cannot_be_deleted_as_project_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("keep")
    (repo / "redirect").symlink_to(outside, target_is_directory=True)
    view = _view(repo, repo / "redirect" / "secret.txt")
    plan = compile_cleanup_plan(view)
    assert not plan.candidates[0].selected
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert secret.read_text() == "keep"


def test_internal_managed_output_remains_deletable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    generated = repo / "scratch"
    generated.write_text("remove")
    view = _view(repo, generated)
    plan = compile_cleanup_plan(view)
    assert plan.candidates[0].selected
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "applied"
    assert not generated.exists()


def test_forged_selected_candidate_cannot_override_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret")
    view = _view(repo, outside)
    plan = compile_cleanup_plan(view)
    forged = replace(plan.candidates[0], selected=True)
    plan = replace(plan, candidates=(forged,))
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert outside.read_text() == "secret"


def test_forged_candidate_identity_does_not_redirect_local_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "build.bin"
    target.write_text("generated")
    outside = tmp_path / "outside"
    outside.write_text("private")
    view = _view(repo, target)
    plan = compile_cleanup_plan(view)
    orig = plan.candidates[0]
    forged = replace(orig, path=str(outside), source_snapshot={
        "exists": True, "kind": "file", "size": 7,
        "mtime_ns": outside.stat().st_mtime_ns, "sha256": "", "tree_sha256": "",
    })
    receipt = apply_cleanup_plan(replace(plan, candidates=(forged,)), ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert outside.read_text() == "private"
    assert target.read_text() == "generated"


def test_parent_symlink_retarget_between_plan_and_apply_is_protected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    working = repo / "generated"
    working.mkdir()
    local = working / "result.bin"
    local.write_text("in-repo")
    external = tmp_path / "elsewhere"
    external.mkdir()
    (external / "result.bin").write_text("do-not-touch")
    view = _view(repo, local)
    plan = compile_cleanup_plan(view)
    assert plan.candidates[0].selected
    working.rename(repo / "old-generated")
    working.symlink_to(external, target_is_directory=True)
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert (external / "result.bin").read_text() == "do-not-touch"


def _proot_descriptor(root: Path, run_id: str = "run-1") -> GeneratedOutputDescriptor:
    return GeneratedOutputDescriptor.create(
        root, owner="DIST", provider="proot-distro", category="provider-staging",
        producer_id=run_id, lifecycle="transient", rebuild_cost="low",
        scope=CleanupScope.PROJECT, managed=True, published=False,
        provenance={
            "run_id": run_id, "session": "proot-stage:test", "provider": "proot-distro",
            "authority": "RUN/LIFE", "normal_ingress": "proot-distro",
        },
    )


def test_forged_proot_descriptor_without_registry_is_protected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "secret").write_text("keep")
    descriptor = _proot_descriptor(stage)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="r01-proot")
    plan = compile_cleanup_plan(view)
    assert not plan.candidates[0].selected
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.results[0].status == "protected"
    assert (stage / "secret").read_text() == "keep"


def test_proot_provider_stage_requires_release_before_external_cleanup(tmp_path: Path) -> None:
    from devtool.provider_resource_registry import register_provider_resources, release_provider_resources
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = tmp_path / "proot-stage"
    stage.mkdir()
    (stage / "scratch").write_text("remove on release")
    descriptor = _proot_descriptor(stage)
    register_provider_resources(repo, [descriptor])
    active_view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="r01-proot")
    active_plan = compile_cleanup_plan(active_view)
    assert not active_plan.candidates[0].selected
    assert (stage / "scratch").exists()

    released = release_provider_resources(repo, run_id="run-1", session="proot-stage:test")
    assert descriptor.resource_id in released
    released_view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="r01-proot")
    released_plan = compile_cleanup_plan(released_view)
    assert any(row.resource_id == descriptor.resource_id and row.selected for row in released_plan.candidates)
    receipt = apply_cleanup_plan(released_plan, ownership=released_view, confirm=True)
    assert any(row.status == "applied" for row in receipt.results)
    assert not stage.exists()
