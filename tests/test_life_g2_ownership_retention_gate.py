from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.cleanup_lifecycle import (
    CleanupReference,
    CleanupScope,
    DestinationCleanupResult,
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
    descriptor_from_path,
    persist_cleanup_receipt,
    preserve_primary_lifecycle_failure,
    remote_staging_descriptor,
)
from devtool.core_runtime import private_run_root
from devtool.life_g2_gate import gate_contract, missing_promises
from devtool.models import OperationResult
from devtool.run_store import AttachmentDescriptor, RunCatalog


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def _managed(repo: Path, path: Path, *, category: str = "cache") -> GeneratedOutputDescriptor:
    return descriptor_from_path(
        path,
        owner="gate-fixture",
        provider="gate-fixture",
        category=category,
        producer_id=f"fixture:{path.name}",
        repo_root=repo,
        heuristic=False,
        lifecycle="disposable",
    )


def test_gate_contract_names_exact_milestone() -> None:
    contract = gate_contract()
    assert contract.roadmap_id == "LIFE-G2"
    assert contract.position == "44/70"
    assert contract.gate_window == "2/2"
    assert not missing_promises(contract.promises)
    assert "LIFE-P105" in contract.promises


def test_tracked_and_heuristic_only_names_survive_even_when_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "tracked.bak"
    tracked.write_text("user source", encoding="utf-8")
    _git(repo, "add", "-f", "tracked.bak")
    _git(repo, "commit", "-qm", "tracked backup-shaped source")
    ignored = repo / "ignored-build"
    ignored.mkdir()
    (ignored / "payload").write_text("user data", encoding="utf-8")
    heuristic = descriptor_from_path(
        ignored, owner="python", provider="python", category="artifact",
        producer_id="name-only", repo_root=repo, heuristic=True,
    )
    tracked_descriptor = GeneratedOutputDescriptor.create(
        tracked, owner="fixture", provider="fixture", category="generated",
        producer_id="fixture", lifecycle="disposable", rebuild_cost="low",
    )
    plan = compile_cleanup_plan(build_cleanup_ownership_view(repo, [heuristic, tracked_descriptor], policy_generation="g2"))
    by_path = {Path(item.path).name: item for item in plan.candidates}
    assert not by_path["ignored-build"].selected
    assert not by_path["tracked.bak"].selected


def test_global_scope_authorization_is_plan_bound_and_confirmation_required(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    global_path = tmp_path / "global-cache"
    global_path.mkdir()
    descriptor = GeneratedOutputDescriptor.create(
        global_path, owner="go", provider="go", category="build-cache",
        producer_id="go-cache", lifecycle="disposable", rebuild_cost="low",
        scope=CleanupScope.USER_GLOBAL,
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    unauthorized = compile_cleanup_plan(view, scope=CleanupScope.USER_GLOBAL)
    assert not unauthorized.global_scope_authorized
    with pytest.raises(RuntimeError, match="explicit global scope authorization"):
        apply_cleanup_plan(unauthorized, ownership=view, confirm=True)
    assert global_path.exists()

    authorized = compile_cleanup_plan(view, scope=CleanupScope.USER_GLOBAL, include_user_global=True)
    assert authorized.global_scope_authorized and authorized.confirmation_required
    with pytest.raises(RuntimeError, match="explicit confirmation"):
        apply_cleanup_plan(authorized, ownership=view, confirm=False)
    receipt = apply_cleanup_plan(authorized, ownership=view, confirm=True)
    assert receipt.status == "applied"
    assert not global_path.exists()


def test_new_run_attachment_after_plan_blocks_apply_race(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    evidence = output / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    descriptor = _managed(repo, output)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    plan = compile_cleanup_plan(view)
    assert plan.selected

    catalog = RunCatalog.open(private_run_root(repo))
    catalog.add_run({"run_id": "r1", "status": "running"})
    catalog.add_attachment(AttachmentDescriptor.from_file(run_id="r1", path=evidence, kind="evidence", privacy="private"))
    catalog.save()

    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert output.exists()
    assert receipt.status == "partial"
    assert receipt.results[0].status == "protected"
    assert "referenced" in receipt.results[0].reason.lower()


def test_new_active_transaction_after_plan_blocks_apply_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    (output / "x").write_text("x", encoding="utf-8")
    descriptor = _managed(repo, output)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    plan = compile_cleanup_plan(view)
    tx = tmp_path / "transaction.json"
    tx.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("devtool.transactions.find_active_transactions", lambda *_args, **_kwargs: [tx])
    monkeypatch.setattr("devtool.transactions.read_transaction", lambda *_args, **_kwargs: {"transaction_id": "tx1", "status": "active"})
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert output.exists()
    assert receipt.status == "partial"
    assert "active artifact transaction" in receipt.results[0].reason


def test_new_active_validation_after_plan_blocks_apply_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    (output / "x").write_text("x", encoding="utf-8")
    descriptor = _managed(repo, output)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    plan = compile_cleanup_plan(view)
    monkeypatch.setattr(
        "devtool.validation_sessions.load_active_session",
        lambda *_args, **_kwargs: SimpleNamespace(session_id="v1", status="running", transaction_path=""),
    )
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert output.exists()
    assert receipt.status == "partial"
    assert "validation" in receipt.results[0].reason.lower()


def test_rer_and_seal_refs_protect_managed_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    descriptor = _managed(repo, output)
    refs = [
        CleanupReference("rer:1", "RER", path=str(output), reason="reuse materialization is active"),
        CleanupReference("seal:1", "seal", path=str(output), reason="release seal pins output"),
    ]
    plan = compile_cleanup_plan(build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2", references=refs))
    assert not plan.candidates[0].selected
    assert plan.candidates[0].reference_ids == ("rer:1", "seal:1")


def test_content_policy_and_destination_drift_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    payload = output / "x"
    payload.write_text("before", encoding="utf-8")
    descriptor = _managed(repo, output)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2-a")
    plan = compile_cleanup_plan(view)
    payload.write_text("after", encoding="utf-8")
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.status == "partial" and output.exists()

    with pytest.raises(RuntimeError, match="policy generation drifted"):
        apply_cleanup_plan(plan, ownership=build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2-b"), confirm=True)

    remote = remote_staging_descriptor(repo / "remote-placeholder", provider="ssh", destination="tablet", producer_id="r1")
    remote_view = build_cleanup_ownership_view(repo, [remote], policy_generation="g2")
    remote_plan = compile_cleanup_plan(remote_view, scope=CleanupScope.REMOTE, destination="tablet")
    phone = remote_staging_descriptor(repo / "remote-placeholder", provider="ssh", destination="phone", producer_id="r1")
    changed = build_cleanup_ownership_view(repo, [phone], policy_generation="g2")
    called = False
    def executor(_candidate):
        nonlocal called
        called = True
        return DestinationCleanupResult("applied", True, postcondition="absent")
    drift = apply_cleanup_plan(remote_plan, ownership=changed, confirm=True, destination_executor=executor)
    assert drift.status == "partial"
    assert not called


def test_partial_mix_remains_partial_and_only_deleted_resource_gets_tombstone(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    selected_path = repo / "owned-output"
    selected_path.mkdir()
    (selected_path / "x").write_text("x", encoding="utf-8")
    protected_path = repo / "build"
    protected_path.mkdir()
    selected = _managed(repo, selected_path)
    protected = descriptor_from_path(
        protected_path, owner="unknown", provider="python", category="artifact",
        producer_id="name-only", repo_root=repo, heuristic=True,
    )
    view = build_cleanup_ownership_view(repo, [selected, protected], policy_generation="g2")
    plan = compile_cleanup_plan(view)
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True)
    assert receipt.status == "partial"
    assert not selected_path.exists() and protected_path.exists()
    assert len(receipt.tombstones) == 1
    assert receipt.tombstones[0]["path"] == str(selected_path)


def test_remote_cleanup_never_deletes_controller_placeholder(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    controller_placeholder = repo / "remote-stage-placeholder"
    controller_placeholder.write_text("controller metadata only", encoding="utf-8")
    descriptor = remote_staging_descriptor(
        controller_placeholder, provider="ssh", destination="tablet", producer_id="run-1"
    )
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    plan = compile_cleanup_plan(view, scope=CleanupScope.REMOTE, destination="tablet")
    seen = []
    def destination_executor(candidate):
        seen.append((candidate.destination, candidate.path))
        return DestinationCleanupResult("applied", True, reclaimed_bytes=123, postcondition="absent")
    receipt = apply_cleanup_plan(plan, ownership=view, confirm=True, destination_executor=destination_executor)
    assert seen == [("tablet", str(controller_placeholder))]
    assert controller_placeholder.exists(), "controller-side placeholder must never be deleted for remote cleanup"
    assert receipt.status == "applied" and receipt.reclaimed_bytes == 123
    assert receipt.tombstones[0]["destination"] == "tablet"


def test_remote_executor_must_attest_revalidation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    descriptor = remote_staging_descriptor(repo / "remote", provider="ssh", destination="tablet", producer_id="r1")
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    plan = compile_cleanup_plan(view, scope=CleanupScope.REMOTE, destination="tablet")
    receipt = apply_cleanup_plan(
        plan, ownership=view, confirm=True,
        destination_executor=lambda _candidate: DestinationCleanupResult("failed", False, detail="remote digest changed"),
    )
    assert receipt.status == "failed"
    assert "digest changed" in receipt.results[0].reason


def test_secondary_cleanup_failure_cannot_replace_primary_lifecycle_failure() -> None:
    primary = OperationResult("build", ["build"], 7, 0.1, "compile failed", [], failure_kind="compile_failure")
    cleanup = OperationResult("artifact-cleanup", ["delete"], 1, 0.1, "unlink failed", [], failure_kind="artifact_cleanup_failure")
    result = preserve_primary_lifecycle_failure([primary], cleanup)
    assert primary.returncode == 7 and primary.failure_kind == "compile_failure"
    assert result.returncode == 0 and result.skipped and result.failure_kind == ""
    assert result.metadata["secondary_original_failure_kind"] == "artifact_cleanup_failure"
    assert result.metadata["primary_failure_kind"] == "compile_failure"


def test_cleanup_receipt_remains_inspectable_after_payload_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "owned-output"
    output.mkdir()
    (output / "x").write_text("payload", encoding="utf-8")
    descriptor = _managed(repo, output)
    view = build_cleanup_ownership_view(repo, [descriptor], policy_generation="g2")
    receipt = apply_cleanup_plan(compile_cleanup_plan(view), ownership=view, confirm=True)
    assert not output.exists()
    persisted = persist_cleanup_receipt(repo, receipt, run_id="cleanup-g2")
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    assert payload["tombstones"][0]["owner"] == "gate-fixture"
    assert payload["tombstones"][0]["source_snapshot"]["exists"] is True
    assert payload["status"] == "applied"
