from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from devtool.artifact_registry import register_paths
from devtool.cleanup_lifecycle import (
    CleanupReference,
    CleanupSafetyClass,
    CleanupScope,
    GeneratedOutputDescriptor,
    annotate_cleanup_specs,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
    descriptor_from_path,
    persist_cleanup_receipt,
    plan_repository_cache_relocation,
)
from devtool.core_runtime import private_run_root
from devtool.models import CommandSpec
from devtool.python_support import clean_python_with_receipt, plan_python_cleanup
from devtool.repository_cleanup import discover_backup_candidates
from devtool.run_store import AttachmentDescriptor, RunCatalog


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Devtool Tests")
    _git(root, "config", "user.email", "devtool@example.invalid")
    (root / ".gitignore").write_text("ignored-build/\n*.bak\n", encoding="utf-8")
    (root / "source.txt").write_text("source\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "source.txt")
    _git(root, "commit", "-qm", "base")
    return root


def test_unowned_heuristic_name_is_never_deletion_authority(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "build"
    path.mkdir()
    (path / "user.txt").write_text("keep", encoding="utf-8")
    descriptor = descriptor_from_path(
        path, owner="python-runner", provider="python", category="artifact",
        producer_id="python:build", repo_root=repo, heuristic=True,
    )
    assert not descriptor.managed
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(view)
    assert not plan.candidates[0].selected
    assert plan.candidates[0].safety_class is CleanupSafetyClass.PROTECTED_UNKNOWN


def test_tracked_resource_is_protected_even_with_managed_descriptor(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "source.txt"
    descriptor = GeneratedOutputDescriptor.create(
        path, owner="fixture", provider="fixture", category="generated",
        producer_id="fixture", lifecycle="rebuildable", rebuild_cost="low",
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    candidate = compile_cleanup_plan(view).candidates[0]
    assert candidate.git_state == "tracked"
    assert not candidate.selected
    assert "tracked source" in candidate.protection_reason


def test_ignored_heuristic_output_is_protected_and_filesystem_accounted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "ignored-build"
    path.mkdir()
    (path / "x.bin").write_bytes(b"x" * 4096)
    descriptor = descriptor_from_path(
        path, owner="python-runner", provider="python", category="artifact",
        producer_id="python:build", repo_root=repo, heuristic=True,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    candidate = compile_cleanup_plan(view).candidates[0]
    assert not candidate.selected
    assert candidate.safety_class is CleanupSafetyClass.PROTECTED_UNKNOWN
    assert candidate.reclaimable_bytes >= 4096
    assert candidate.filesystem_id.startswith("dev:") or candidate.filesystem_id == "unknown"


def test_reference_root_protects_rebuildable_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "ignored-build"
    path.mkdir()
    descriptor = descriptor_from_path(
        path, owner="runner", provider="python", category="artifact",
        producer_id="producer", repo_root=repo, heuristic=True,
    )
    ref = CleanupReference("rer:1", "RER", path=str(path), reason="reusable output is referenced")
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1", references=[ref])
    candidate = compile_cleanup_plan(view).candidates[0]
    assert not candidate.selected
    assert candidate.retention_state == "protected"
    assert candidate.reference_ids == ("rer:1",)


def test_plan_drift_revalidation_prevents_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "ignored-build"
    path.mkdir()
    file = path / "x.txt"
    file.write_text("before", encoding="utf-8")
    descriptor = descriptor_from_path(
        path, owner="runner", provider="python", category="artifact",
        producer_id="producer", repo_root=repo, heuristic=False,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(view)
    file.write_text("after-change", encoding="utf-8")
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert path.exists()
    assert receipt.status == "partial"
    assert receipt.results[0].status == "protected"
    assert "changed" in receipt.results[0].reason


def test_policy_generation_drift_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "ignored-build"
    path.mkdir()
    descriptor = descriptor_from_path(path, owner="runner", provider="python", category="cache", producer_id="p", repo_root=repo, heuristic=False)
    old = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(old)
    new = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p2")
    with pytest.raises(RuntimeError, match="policy generation drifted"):
        apply_cleanup_plan(plan, ownership=new, confirm=True)


def test_global_scope_requires_explicit_authorization(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    global_path = tmp_path / "global-cache"
    global_path.mkdir()
    descriptor = GeneratedOutputDescriptor.create(
        global_path, owner="go", provider="go", category="build-cache", producer_id="go-cache",
        lifecycle="disposable", rebuild_cost="low", scope=CleanupScope.USER_GLOBAL,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    protected = compile_cleanup_plan(view, scope=CleanupScope.USER_GLOBAL)
    assert not protected.candidates[0].selected
    explicit = compile_cleanup_plan(view, scope=CleanupScope.USER_GLOBAL, include_user_global=True)
    assert explicit.candidates[0].selected
    assert explicit.confirmation_required


def test_remote_cleanup_cannot_execute_on_controller_without_provider(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = tmp_path / "remote-staging"
    path.mkdir()
    descriptor = GeneratedOutputDescriptor.create(
        path, owner="ssh", provider="ssh", category="staging", producer_id="ssh:run",
        lifecycle="disposable", rebuild_cost="low", scope=CleanupScope.REMOTE, destination="tablet",
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(view, scope=CleanupScope.REMOTE, destination="tablet")
    with pytest.raises(RuntimeError, match="destination-local provider executor"):
        apply_cleanup_plan(plan, ownership=view, confirm=True)


def test_run_attachment_is_consumed_as_protection_authority(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "ignored-build"
    output.mkdir()
    attachment_file = output / "evidence.json"
    attachment_file.write_text("{}", encoding="utf-8")
    catalog = RunCatalog.open(private_run_root(repo))
    catalog.add_run({"run_id": "r1", "status": "succeeded"})
    catalog.add_attachment(AttachmentDescriptor.from_file(run_id="r1", path=attachment_file, kind="evidence", privacy="private"))
    catalog.save()
    descriptor = descriptor_from_path(output, owner="runner", provider="python", category="artifact", producer_id="p", repo_root=repo, heuristic=True)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    candidate = next(item for item in compile_cleanup_plan(view).candidates if item.resource_id == descriptor.resource_id)
    assert not candidate.selected
    assert any(ref.startswith("run:r1:attachment:") for ref in candidate.reference_ids)
    assert "RUN/RER" in view.authority_sources


def test_produced_artifact_registry_is_protected_from_ordinary_cleanup(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    artifact = repo / "ignored-build" / "app.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"zip-ish")
    register_paths(repo, [artifact], metadata={"runner": "fixture", "run_id": "r1"})
    descriptor = descriptor_from_path(artifact, owner="runner", provider="fixture", category="artifact", producer_id="build", repo_root=repo, heuristic=False)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    candidate = next(item for item in compile_cleanup_plan(view).candidates if item.resource_id == descriptor.resource_id)
    assert not candidate.selected
    assert "artifact-registry" in view.authority_sources


def test_python_cleanup_preserves_unowned_build_and_removes_owned_cache(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    unowned = repo / "build"
    unowned.mkdir()
    (unowned / "user.txt").write_text("keep", encoding="utf-8")
    ignored = repo / "ignored-build"
    ignored.mkdir()
    cache = repo / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"x")

    # Only Python-known names are selected by the provider planner; generic
    # unignored build remains protected even if explicitly requested below.
    plan, _view = plan_python_cleanup(repo, categories=("artifacts", "caches"))
    assert all(Path(item.path).name != "build" or not item.selected for item in plan.candidates)
    _plan, receipt = clean_python_with_receipt(repo, categories=("artifacts", "caches"))
    assert unowned.exists()
    assert not cache.exists()
    assert receipt.status in {"applied", "partial"}


def test_python_tracked_build_directory_survives(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    build = repo / "build"
    build.mkdir()
    (build / "tracked.txt").write_text("tracked", encoding="utf-8")
    _git(repo, "add", "-f", "build/tracked.txt")
    _git(repo, "commit", "-qm", "tracked build fixture")
    _plan, receipt = clean_python_with_receipt(repo, categories=("artifacts",))
    assert build.exists()
    assert not any(item.status == "applied" and Path(item.path) == build for item in receipt.results)


def test_legacy_backup_discovery_uses_ownership_not_suffix_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "tracked.bak"
    tracked.write_text("source", encoding="utf-8")
    _git(repo, "add", "-f", "tracked.bak")
    _git(repo, "commit", "-qm", "tracked backup-shaped source")
    ignored = repo / "scratch.bak"
    ignored.write_text("generated", encoding="utf-8")
    found = discover_backup_candidates(repo)
    assert ignored not in found
    assert tracked not in found


def test_provider_clean_is_typed_opaque_action_not_fake_file_preview(tmp_path: Path) -> None:
    spec = CommandSpec(
        "clean", ["npm", "run", "clean"], cwd=str(tmp_path), destructive=True,
        metadata={"runner": "node", "cleanup_scope": "project"},
    )
    actions = annotate_cleanup_specs([spec])
    assert len(actions) == 1
    assert actions[0].safety_class is CleanupSafetyClass.OPAQUE_EXTERNAL
    assert actions[0].confirmation_required
    assert spec.metadata["cleanup_semantics"] == "opaque-provider-action"
    assert "paths" not in spec.metadata["cleanup_action"]


def test_repository_cache_relocation_is_explicit_side_effect_free_plan(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    old = tmp_path / "old" / "repo"
    new = tmp_path / "new" / "repo"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    from devtool.cache_cleanup import repository_cache_key
    source = cache_root / "build" / repository_cache_key(old) / "native-termux"
    source.mkdir(parents=True)
    plan = plan_repository_cache_relocation(old, new, backend="native-termux", cache_root=cache_root)
    assert not plan.mutates
    assert str(source) in plan.source_paths
    assert plan.old_repository_key != plan.new_repository_key
    assert not (cache_root / "build" / plan.new_repository_key / "native-termux").exists()


def test_cleanup_receipt_tombstone_survives_payload_deletion_and_links_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    path = repo / "ignored-build"
    path.mkdir()
    (path / "x").write_text("x", encoding="utf-8")
    descriptor = descriptor_from_path(path, owner="runner", provider="python", category="cache", producer_id="p", repo_root=repo, heuristic=False)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(view)
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert not path.exists()
    assert receipt.tombstones and receipt.tombstones[0]["path"] == str(path)
    persisted = persist_cleanup_receipt(repo, receipt, run_id="cleanup-run")
    assert persisted.is_file()
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    assert payload["tombstones"][0]["path"] == str(path)
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    assert any(item.kind == "cleanup-receipt" and item.run_id == "cleanup-run" for item in catalog.attachments.values())


def test_provider_results_become_receipt_without_reexecution(tmp_path: Path) -> None:
    from devtool.cleanup_lifecycle import provider_cleanup_receipt_from_results
    from devtool.models import OperationResult
    repo = _repo(tmp_path)
    spec = CommandSpec("clean", ["npm", "run", "clean"], cwd=str(repo), destructive=True, metadata={"runner": "node", "cleanup_scope": "project"})
    actions = annotate_cleanup_specs([spec])
    view = build_cleanup_ownership_view(repo, (), policy_generation="life-x03-provider-clean-v1")
    plan = compile_cleanup_plan(view, opaque_actions=actions)
    receipt = provider_cleanup_receipt_from_results(
        plan, actions, [OperationResult("clean", spec.argv, 0, 0.1, "ok", [])]
    )
    assert receipt.status == "applied"
    assert receipt.results[0].postcondition == "provider-reported-success"
    assert receipt.reclaimed_bytes == 0


def test_run_stream_is_cleanup_protection_root(tmp_path: Path) -> None:
    from devtool.run_store import StreamDescriptor
    repo = _repo(tmp_path)
    output = repo / "ignored-build"
    output.mkdir()
    stream_path = output / "worker.stdout"
    stream_path.write_text("still active\n", encoding="utf-8")
    catalog = RunCatalog.open(private_run_root(repo))
    catalog.add_run({"run_id": "active-run", "status": "running"})
    catalog.add_stream(StreamDescriptor(
        schema_version=1,
        stream_id="stream-1",
        run_id="active-run",
        kind="stdout",
        path=str(stream_path),
        node_id="root",
        process_id="worker",
        byte_limit=4096,
        bytes_written=13,
        truncated=False,
        sha256="fixture",
    ))
    catalog.save()
    descriptor = descriptor_from_path(
        output, owner="runner", provider="python", category="cache", producer_id="p",
        repo_root=repo, heuristic=False,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    candidate = next(item for item in compile_cleanup_plan(view).candidates if item.resource_id == descriptor.resource_id)
    assert not candidate.selected
    assert "run:active-run:stream:stream-1" in candidate.reference_ids


def test_remote_staging_identity_is_destination_qualified(tmp_path: Path) -> None:
    from devtool.cleanup_lifecycle import remote_staging_descriptor
    repo = _repo(tmp_path)
    path = repo / "remote-stage-placeholder"
    tablet = remote_staging_descriptor(path, provider="ssh", destination="tablet", producer_id="run-1")
    phone = remote_staging_descriptor(path, provider="ssh", destination="phone", producer_id="run-1")
    assert tablet.resource_id != phone.resource_id
    assert tablet.scope is CleanupScope.REMOTE and phone.scope is CleanupScope.REMOTE
    view = build_cleanup_ownership_view(repo, [tablet, phone], policy_generation="p1")
    tablet_plan = compile_cleanup_plan(view, scope=CleanupScope.REMOTE, destination="tablet")
    assert [item.resource_id for item in tablet_plan.candidates] == [tablet.resource_id]
    with pytest.raises(ValueError, match="non-local destination"):
        remote_staging_descriptor(path, provider="ssh", destination="local", producer_id="run-1")


def test_cleanup_explain_projects_canonical_semantics(tmp_path: Path) -> None:
    from devtool.cleanup_lifecycle import explain_cleanup_candidate
    repo = _repo(tmp_path)
    path = repo / "build"
    path.mkdir()
    descriptor = descriptor_from_path(
        path, owner="python-runner", provider="python", category="artifact",
        producer_id="python:build", repo_root=repo, heuristic=True,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="p1")
    plan = compile_cleanup_plan(view)
    candidate = plan.candidates[0]
    projection = explain_cleanup_candidate(plan, candidate.candidate_id)
    assert projection["owner"] == candidate.owner
    assert projection["destination"] == "local"
    assert projection["selected"] is False
    assert projection["protection_reason"]
    assert projection["invalidation"]["requires_fresh_snapshot"] is True
    assert projection["recreation"]["rebuild_cost"] == candidate.rebuild_cost


def test_go_project_clean_never_mutates_global_cache(tmp_path: Path) -> None:
    from devtool.models import ProjectConfig
    from devtool.project_runners import GoRunner
    repo = _repo(tmp_path)
    (repo / "go.mod").write_text("module example.com/life-x03\ngo 1.23\n", encoding="utf-8")
    spec = GoRunner().build_specs(
        ProjectConfig(repo_root=repo, project_root=str(repo), kind="go"), "clean"
    )[0]
    assert spec.argv == ["go", "clean", "./..."]
    assert "-cache" not in spec.argv and "-testcache" not in spec.argv and "-modcache" not in spec.argv
    assert spec.metadata["cleanup_scope"] == "project"
    assert spec.metadata["global_cache_mutation"] is False
