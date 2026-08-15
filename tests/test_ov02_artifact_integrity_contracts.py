from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

import devtool.artifacts as artifacts_mod
import devtool.transactions as transactions_mod
from devtool.artifact_inspection import inspect_artifact
from devtool.artifact_validation import run_artifact_validation_script
from devtool.artifacts import (
    append_history,
    artifact_validation_task_declaration,
    create_overlay_bundle,
    load_history,
    overlay_bundle_conflicts,
    read_artifact_manifest,
    read_overlay_bundle_manifest,
    resolve_artifact_manifest,
    sha256_file,
    validate_artifact,
    validate_overlay_bundle,
    write_archive_entries,
)
from devtool.cli import (
    _apply_declared_validation_tasks,
    _bundle_entry_validation_tasks,
    _manifest_with_validation_tasks,
    _record_apply_history,
    _validation_expectation_result,
)
from devtool.models import CommandSpec, Issue, OperationResult, ProjectConfig
from devtool.project_runners import resolve_validation_plan
from devtool.repair_apply import apply_auto_source_repairs
from devtool.transactions import create_apply_transaction, mark_transaction_completed, register_transaction_paths, rollback_transaction


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay(path: Path, payloads: dict[str, bytes], manifest: dict) -> Path:
    entries = {".devtool-artifact.json": (json.dumps(manifest) + "\n").encode()}
    entries.update(payloads)
    return write_archive_entries(path, entries)


def _add_overlay(path: Path, rel: str, data: bytes, **manifest_extra: object) -> Path:
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {"format": 1, "entries": [{"path": rel, "action": "add", "sha256": _sha(data), "size": len(data)}]},
        **manifest_extra,
    }
    return _overlay(path, {rel: data}, manifest)


def _modify_overlay(path: Path, rel: str, before: bytes, after: bytes) -> Path:
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [{
                "path": rel,
                "action": "modify",
                "source_sha256": _sha(before),
                "sha256": _sha(after),
                "size": len(after),
            }],
        },
    }
    return _overlay(path, {rel: after}, manifest)


# MR-080 ---------------------------------------------------------------------

def test_source_integrity_rehashes_bytes_even_when_size_and_mtime_are_restored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "tracked.txt"
    source.write_bytes(b"AAAA")
    stat = source.stat()
    overlay = _modify_overlay(tmp_path / "change.zip", "tracked.txt", b"AAAA", b"CCCC")
    assert validate_artifact(repo, overlay).ok
    # Same path/inode/size and restore the old mtime: metadata-only hash caches would miss this.
    source.write_bytes(b"BBBB")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    result = validate_artifact(repo, overlay)
    assert not result.ok
    assert any("Source SHA-256 mismatch" in message for message in result.messages)


@pytest.mark.parametrize("inventory", [[], {"format": 1}, {"format": 1, "entries": {}}, {"format": 1, "entries": ["bad"]}])
def test_malformed_declared_inventory_fails_closed(tmp_path: Path, inventory: object) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / "bad.zip", {"x.txt": b"x"}, {"schema_version": 2, "type": "overlay", "inventory": inventory})
    assert not validate_artifact(repo, artifact).ok


# MR-081 ---------------------------------------------------------------------

def test_enforced_git_identity_fails_when_git_identity_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(
        tmp_path / "identity.zip",
        "x.txt",
        b"x",
        repository={"remote": "git@example/repo.git", "enforce_remote": True, "branch": "main", "enforce_branch": True},
    )
    monkeypatch.setattr(artifacts_mod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("could not be determined" in message for message in result.messages)


def test_created_bundle_pins_nested_overlay_bytes_and_detects_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = _add_overlay(tmp_path / "nested.zip", "x.txt", b"x")
    bundle = create_overlay_bundle(tmp_path / "bundle.zip", [nested])
    files = {m.name: m.data or b"" for m in artifacts_mod.list_archive_members(bundle, include_data=True) if not m.is_dir}
    nested_name = next(name for name in files if name.startswith("overlays/"))
    files[nested_name] = files[nested_name] + b"tamper"
    tampered = write_archive_entries(tmp_path / "tampered.zip", files)
    result = validate_overlay_bundle(repo, tampered)
    assert not result.ok
    assert any("member SHA-256 mismatch" in message or "member size mismatch" in message for message in result.messages)


def test_bundle_creator_rejects_nested_overlay_with_invalid_internal_checksum(tmp_path: Path) -> None:
    bad_payload = b"actual"
    nested = _overlay(
        tmp_path / "nested-bad.zip",
        {"x.txt": bad_payload},
        {
            "schema_version": 2,
            "type": "overlay",
            "inventory": {
                "format": 1,
                "entries": [{
                    "path": "x.txt",
                    "action": "add",
                    "sha256": _sha(b"not-the-payload"),
                    "size": len(bad_payload),
                }],
            },
        },
    )
    out = tmp_path / "bundle.zip"
    with pytest.raises(RuntimeError, match="self-validation"):
        create_overlay_bundle(out, [nested])
    assert not out.exists()


@pytest.mark.parametrize("version", [0, -1, "2", False])
def test_nonpositive_or_noninteger_schema_versions_reject(tmp_path: Path, version: object) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / f"schema-{str(version)}.zip", {"x.txt": b"x"}, {"schema_version": version, "type": "overlay"})
    assert not validate_artifact(repo, artifact).ok


def test_creator_rejects_invalid_validation_timeout_and_removes_output(tmp_path: Path) -> None:
    nested = _add_overlay(tmp_path / "nested.zip", "x.txt", b"x")
    validator = tmp_path / "validate.sh"
    validator.write_text("#!/bin/sh\nexit 0\n")
    out = tmp_path / "bundle.zip"
    with pytest.raises(RuntimeError, match="positive integer"):
        create_overlay_bundle(out, [nested], validation_script=validator, validation_timeout_seconds=0)
    assert not out.exists()


# MR-082 ---------------------------------------------------------------------

def test_explicit_overlay_with_archive_payload_is_not_reclassified_as_bundle(tmp_path: Path) -> None:
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("x", "x")
    manifest = {"schema_version": 2, "type": "overlay"}
    artifact = _overlay(tmp_path / "outer.zip", {"payload.zip": inner.read_bytes()}, manifest)
    assert not artifacts_mod.is_overlay_bundle(artifact)
    assert read_artifact_manifest(artifact)["type"] == "overlay"


def test_explicit_overlay_type_is_authoritative_even_with_bundle_shaped_fields(tmp_path: Path) -> None:
    artifact = _overlay(
        tmp_path / "explicit-overlay.zip",
        {"payload.zip": b"not-an-archive-and-that-does-not-matter"},
        {
            "schema_version": 2,
            "type": "overlay",
            "apply_order": [{"path": "payload.zip"}],
            "overlays": [{"path": "payload.zip"}],
        },
    )
    assert read_overlay_bundle_manifest(artifact) == {}
    assert not artifacts_mod.is_overlay_bundle(artifact)
    assert read_artifact_manifest(artifact)["type"] == "overlay"


def test_explicit_overlay_type_is_authoritative_even_in_legacy_bundle_manifest_filename(tmp_path: Path) -> None:
    artifact = write_archive_entries(
        tmp_path / "legacy-name.zip",
        {
            "devtool-bundle.json": json.dumps({"schema_version": 2, "type": "overlay"}).encode(),
            "x.txt": b"x",
        },
    )
    assert not artifacts_mod.is_overlay_bundle(artifact)
    assert read_artifact_manifest(artifact)["type"] == "overlay"


def test_implicit_bundle_preserves_archive_member_order(tmp_path: Path) -> None:
    a = _add_overlay(tmp_path / "a.zip", "a.txt", b"a")
    b = _add_overlay(tmp_path / "b.zip", "b.txt", b"b")
    wrapper = tmp_path / "wrapper.zip"
    with zipfile.ZipFile(wrapper, "w") as zf:
        zf.writestr("second.zip", b.read_bytes())
        zf.writestr("first.zip", a.read_bytes())
    manifest = artifacts_mod.read_overlay_bundle_manifest(wrapper)
    assert [item["path"] for item in manifest["apply_order"]] == ["second.zip", "first.zip"]


def test_sidecar_plus_embedded_or_duplicate_embedded_manifest_is_ambiguous(tmp_path: Path) -> None:
    embedded = _add_overlay(tmp_path / "ambiguous.zip", "x.txt", b"x")
    embedded.with_name(embedded.name + ".devtool.json").write_text(json.dumps({"schema_version": 2, "type": "overlay", "target": "sidecar"}))
    resolution = resolve_artifact_manifest(embedded)
    assert resolution.present and not resolution.valid
    assert not validate_artifact(tmp_path, embedded).ok

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as zf:
        zf.writestr(".devtool-artifact.json", json.dumps({"schema_version": 2, "type": "overlay", "target": "one"}))
        zf.writestr("artifact.devtool.json", json.dumps({"schema_version": 2, "type": "overlay", "target": "two"}))
        zf.writestr("x.txt", "x")
    assert not resolve_artifact_manifest(duplicate).valid


def test_inspection_and_execution_resolve_same_type_and_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "target.zip", "x.txt", b"x", target="devtool")
    inspected = inspect_artifact(artifact, repo_root=repo)
    manifest = read_artifact_manifest(artifact)
    assert inspected["kind"] == manifest["type"] == "overlay"
    assert inspected["target"] == artifacts_mod.artifact_target(manifest) == "devtool"


# MR-083 ---------------------------------------------------------------------

def test_declared_malformed_manifest_fails_validation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "malformed.zip"
    with zipfile.ZipFile(artifact, "w") as zf:
        zf.writestr(".devtool-artifact.json", "{bad-json")
        zf.writestr("x.txt", "x")
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("Invalid JSON" in message for message in result.messages)


def test_bundle_preserves_nested_source_sha_validation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.txt").write_bytes(b"actual")
    nested = _modify_overlay(tmp_path / "nested.zip", "x.txt", b"expected", b"new")
    bundle = create_overlay_bundle(tmp_path / "bundle.zip", [nested])
    result = validate_overlay_bundle(repo, bundle)
    assert not result.ok
    assert any("Source SHA-256 mismatch" in message for message in result.messages)


def test_clean_worktree_requirement_fails_when_git_status_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "clean.zip", "x.txt", b"x", apply={"atomic": True, "requires_clean_worktree": True})
    monkeypatch.setattr(artifacts_mod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("cleanliness could not be determined" in message for message in result.messages)


@pytest.mark.parametrize("field,value", [("solution", "../outside.sln"), ("solution", "/tmp/outside.sln"), ("workspace", {"root": "../outside"})])
def test_solution_and_workspace_paths_cannot_escape_repo(tmp_path: Path, field: str, value: object) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = {"schema_version": 2, "type": "overlay", field: value}
    artifact = _overlay(tmp_path / "escape.zip", {"x.txt": b"x"}, manifest)
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("Unsafe archive path" in message for message in result.messages)


# MR-084 ---------------------------------------------------------------------

def test_ordered_same_file_transforms_are_not_false_bundle_conflicts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.txt").write_bytes(b"one")
    first = _modify_overlay(tmp_path / "first.zip", "x.txt", b"one", b"two")
    second = _modify_overlay(tmp_path / "second.zip", "x.txt", b"two", b"three")
    bundle = create_overlay_bundle(tmp_path / "bundle.zip", [first, second])
    assert overlay_bundle_conflicts(bundle) == {}
    result = validate_overlay_bundle(repo, bundle, fail_on_conflict=True)
    assert result.ok, result.messages


def test_validation_expected_and_warning_limit_are_executable_contracts() -> None:
    passing = OperationResult("pytest-test", ["pytest"], 0, 0.1, "", [], metadata={"validation_phase_name": "tests", "validation_pipeline": True})
    contract = _validation_expectation_result({"validation": {"expected": {"tests": "pass"}, "maximum_warnings": 0}}, [passing])
    assert contract is not None and contract.returncode == 0
    warning = OperationResult(
        "pytest-test", ["pytest"], 0, 0.1, "",
        [Issue("warning", "pytest", "x.py", "W1", "warning")],
        metadata={"validation_phase_name": "tests", "validation_pipeline": True},
    )
    contract = _validation_expectation_result({"validation": {"expected": {"tests": "pass"}, "maximum_warnings": 0}}, [warning])
    assert contract is not None and contract.returncode == 1
    assert contract.metadata["validation_contract_satisfied"] is False


def test_validation_boolean_fields_reject_truthy_strings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "bool.zip", "x.txt", b"x", validation={"required": "false", "restore": "false"})
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("must be a boolean" in message for message in result.messages)


def test_commit_enabled_requires_real_boolean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(
        tmp_path / "commit-bool.zip",
        "x.txt",
        b"x",
        apply={"commit": {"enabled": "false", "message": "must not coerce"}},
    )
    result = validate_artifact(repo, artifact)
    assert not result.ok
    assert any("apply.commit.enabled must be a boolean" in message for message in result.messages)


def test_inspection_of_invalid_schema_reports_validation_failure_without_crashing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "bad-schema.zip", "x.txt", b"x", schema_version="banana")
    inspected = inspect_artifact(artifact, repo_root=repo)
    assert inspected["schema_version"] == "banana"
    assert inspected["validation"]["ok"] is False
    assert any("schema_version" in message for message in inspected["validation"]["messages"])


def test_direct_validation_script_runner_rejects_non_integer_timeout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b"#!/bin/sh\nexit 0\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {"format": 1, "entries": []},
        "validation": {
            "script": {
                "path": ".devtool-artifact/validate.sh",
                "runner": "bash",
                "sha256": _sha(script),
                "timeout_seconds": "5",
            }
        },
    }
    artifact = _overlay(tmp_path / "timeout.zip", {".devtool-artifact/validate.sh": script}, manifest)
    with pytest.raises(RuntimeError, match="positive integer"):
        run_artifact_validation_script(repo, artifact, manifest=manifest, dry_run=True)


# MR-006 ---------------------------------------------------------------------

def test_validation_task_resolution_preserves_explicit_empty_and_provenance() -> None:
    manifest = {"validation": {"tasks": []}}
    assert artifact_validation_task_declaration(manifest) == (True, [])
    args = argparse.Namespace(_devtool_cli_test_task_explicit=False, test_task=["old"])
    _apply_declared_validation_tasks(args, manifest, active=True)
    assert args.test_task == []
    assert args._devtool_validation_tasks_declared is True
    assert args._devtool_validation_task_source == "artifact-manifest"

    declared, tasks, source = _bundle_entry_validation_tasks(
        {"validation_tasks": [], "validation_tasks_declared": True},
        {"validation": {"tasks": ["nested"]}},
        {"validation": {"tasks": ["bundle"]}},
    )
    assert (declared, tasks, source) == (True, [], "bundle-entry")


def test_bundle_creator_preserves_absent_vs_explicit_empty_validation_tasks(tmp_path: Path) -> None:
    nested = _add_overlay(tmp_path / "nested.zip", "x.txt", b"x")
    absent = create_overlay_bundle(tmp_path / "absent.zip", [nested], validation_tasks=None)
    explicit_empty = create_overlay_bundle(tmp_path / "empty.zip", [nested], validation_tasks=[])
    absent_manifest = read_overlay_bundle_manifest(absent)
    empty_manifest = read_overlay_bundle_manifest(explicit_empty)
    assert "tasks" not in absent_manifest["validation"]
    assert empty_manifest["validation"]["tasks"] == []


def test_required_validation_can_be_satisfied_by_explicit_tasks_without_default_phases(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(
        tmp_path / "tasks.zip",
        "x.txt",
        b"x",
        validation={
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "tasks": ["custom-check"],
        },
    )
    result = validate_artifact(repo, artifact)
    assert result.ok, result.messages


def test_validation_plan_is_runner_aware_stably_deduped_and_does_not_mutate_manifest() -> None:
    config = ProjectConfig(Path("/tmp/repo"), "python")
    specs = [CommandSpec("restore", ["restore"]), CommandSpec("test", ["test"])]
    plan = resolve_validation_plan(config, specs, source="artifact-manifest", tasks_declared=True, declared_tasks=["a", "a", "b"], required=True)
    assert plan.runner == "python"
    assert plan.declared_tasks == ("a", "b")
    assert plan.planned_steps == ("restore", "test")
    original = {"validation": {"tasks": ["nested"]}}
    updated = _manifest_with_validation_tasks(original, [], declared=True)
    assert original["validation"]["tasks"] == ["nested"]
    assert updated["validation"]["tasks"] == []


# MR-005 ---------------------------------------------------------------------

def test_transaction_summary_is_archived_before_recovery_marker_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "overlay.zip", "x.txt", b"x")
    tx = create_apply_transaction(repo, artifact, artifact_type="overlay", preview={"changes": [{"kind": "add", "path": "x.txt"}], "counts": {}})
    order: list[str] = []
    real_archive = transactions_mod._archive_transaction_summary
    real_remove = transactions_mod._remove_active_marker

    def archive(path: Path, payload: dict) -> None:
        order.append("archive")
        real_archive(path, payload)

    def remove(repo_root: Path, path: Path, transaction_id: str = "") -> None:
        order.append("remove")
        real_remove(repo_root, path, transaction_id)

    monkeypatch.setattr(transactions_mod, "_archive_transaction_summary", archive)
    monkeypatch.setattr(transactions_mod, "_remove_active_marker", remove)
    mark_transaction_completed(tx["path"])
    assert order[:2] == ["archive", "remove"]


def test_transaction_summary_archive_is_fsynced_before_recovery_marker_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "overlay.zip", "x.txt", b"x")
    tx = create_apply_transaction(repo, artifact, artifact_type="overlay", preview={"changes": [{"kind": "add", "path": "x.txt"}], "counts": {}})
    events: list[str] = []
    real_fsync = transactions_mod.os.fsync
    real_remove = transactions_mod._remove_active_marker

    def fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def remove(repo_root: Path, path: Path, transaction_id: str = "") -> None:
        events.append("remove")
        real_remove(repo_root, path, transaction_id)

    monkeypatch.setattr(transactions_mod.os, "fsync", fsync)
    monkeypatch.setattr(transactions_mod, "_remove_active_marker", remove)
    mark_transaction_completed(tx["path"])
    assert "remove" in events
    remove_index = events.index("remove")
    # Completing the live transaction and archiving the durable summary each
    # fsync their file and containing directory before the recovery marker goes.
    assert events[:remove_index].count("fsync") >= 4


def test_artifact_history_record_is_durably_fsynced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    calls: list[int] = []
    real_fsync = artifacts_mod.os.fsync

    def fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(artifacts_mod.os, "fsync", fsync)
    append_history(repo, {"history_id": "durable", "schema_version": 3, "status": "ok"}, repo_local=False)
    # File data then directory metadata are both synchronized.
    assert len(calls) >= 2


def test_history_is_idempotent_schema3_and_records_final_child_rollback_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _add_overlay(tmp_path / "history.zip", "x.txt", b"x")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    config = ProjectConfig(repo, "python")
    args = argparse.Namespace(
        command="apply-overlay", validate=False, validate_only=False, preview=False,
        artifact=str(artifact), commit="", _devtool_artifact_type="overlay",
        _devtool_artifact_preview={"files": 1, "counts": {"add": 1}},
        _devtool_artifact_manifest=read_artifact_manifest(artifact),
        _devtool_artifact_sha=sha256_file(artifact), _devtool_artifact_size=artifact.stat().st_size, _devtool_run_id="run-1",
        _devtool_resolved_validation_plan={"validation_plan_source": "runner-default"},
    )
    results = [
        OperationResult("apply-overlay", ["apply"], 0, 0.1, "", [], metadata={"overlay_id": "child-1"}),
        OperationResult(
            "validation-contract", ["contract"], 0, 0.0, "", [],
            metadata={"validation_contract_satisfied": True, "validation_contract_evidence": {"tests": {"actual": "passed"}}},
        ),
        OperationResult("apply-rollback", ["rollback"], 0, 0.1, "", [], failure_kind="rollback"),
    ]
    _record_apply_history(config, args, results)
    _record_apply_history(config, args, results)
    history = load_history(repo)
    assert len(history) == 1
    entry = history[0]
    assert entry["schema_version"] == 3
    assert entry["status"] == "rolled-back"
    assert entry["rollback_status"] == "rolled-back"
    assert entry["content_identity"]["sha256"] == sha256_file(artifact)
    assert [item["name"] for item in entry["child_outcomes"]] == ["apply-overlay", "validation-contract", "apply-rollback"]
    assert entry["validation_contract"]["validation_contract_satisfied"] is True
    assert entry["validation_plan"]["validation_contract_satisfied"] is True
    assert entry["validation_plan"]["validation_contract_evidence"] == {"tests": {"actual": "passed"}}
    outcomes = entry["validation_plan"]["validation_step_outcomes"]
    assert [item["name"] for item in outcomes] == ["validation-contract"]
    assert outcomes[0]["outcome"] == "passed"


def test_history_preserves_cached_content_identity_after_artifact_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    artifact = _add_overlay(tmp_path / "ephemeral.zip", "x.txt", b"x")
    digest = sha256_file(artifact)
    size = artifact.stat().st_size
    manifest = read_artifact_manifest(artifact)
    artifact.unlink()
    args = argparse.Namespace(
        command="apply-overlay", validate=False, validate_only=False, preview=False,
        artifact=str(artifact), commit="", _devtool_artifact_type="overlay",
        _devtool_artifact_preview={"files": 1, "counts": {"add": 1}},
        _devtool_artifact_manifest=manifest,
        _devtool_artifact_sha=digest,
        _devtool_artifact_size=size,
        _devtool_run_id="run-cleanup",
        _devtool_resolved_validation_plan={},
    )
    _record_apply_history(repo_config := ProjectConfig(repo, "python"), args, [OperationResult("apply-overlay", ["apply"], 0, 0.1, "", [])])
    entry = load_history(repo_config.repo_root)[0]
    assert entry["content_identity"] == {"sha256": digest, "size": size}


def test_history_recovers_final_transaction_identity_after_live_journal_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    artifact = _add_overlay(tmp_path / "tx-history.zip", "x.txt", b"x")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "add", "path": "x.txt"}], "counts": {"add": 1}},
    )
    tx_path = Path(tx["path"])
    tx_id = str(tx["transaction_id"])
    mark_transaction_completed(tx_path)
    assert not tx_path.exists()
    args = argparse.Namespace(
        command="apply-overlay", validate=False, validate_only=False, preview=False,
        artifact=str(artifact), commit="", _devtool_artifact_type="overlay",
        _devtool_artifact_preview={"files": 1, "counts": {"add": 1}},
        _devtool_artifact_manifest=read_artifact_manifest(artifact),
        _devtool_artifact_sha=sha256_file(artifact),
        _devtool_artifact_size=artifact.stat().st_size,
        _devtool_run_id="run-tx-history",
        _devtool_resolved_validation_plan={},
    )
    result = OperationResult(
        "apply-overlay", ["apply"], 0, 0.1, "", [],
        metadata={"transaction_path": str(tx_path)},
    )
    _record_apply_history(ProjectConfig(repo, "python"), args, [result])
    entry = load_history(repo)[0]
    assert entry["transaction"] == {"id": tx_id, "status": "completed"}


def test_history_recovers_rolled_back_transaction_identity_after_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    target = repo / "x.txt"
    target.write_text("old\n")
    artifact = _modify_overlay(tmp_path / "rollback-history.zip", "x.txt", b"old\n", b"new\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modify", "path": "x.txt"}], "counts": {"modify": 1}},
    )
    tx_path = Path(tx["path"])
    tx_id = str(tx["transaction_id"])
    target.write_text("new\n")
    rollback = rollback_transaction(tx_path, reason="test failure")
    assert rollback.returncode == 0
    assert not tx_path.exists()
    args = argparse.Namespace(
        command="apply-overlay", validate=True, validate_only=False, preview=False,
        artifact=str(artifact), commit="", _devtool_artifact_type="overlay",
        _devtool_artifact_preview={"files": 1, "counts": {"modify": 1}},
        _devtool_artifact_manifest=read_artifact_manifest(artifact),
        _devtool_artifact_sha=sha256_file(artifact),
        _devtool_artifact_size=artifact.stat().st_size,
        _devtool_run_id="run-rb-history",
        _devtool_resolved_validation_plan={},
    )
    failed_validation = OperationResult(
        "pytest-test", ["pytest"], 1, 0.1, "failed", [],
        failure_kind="validation_failure",
        metadata={"transaction_path": str(tx_path), "validation_pipeline": True},
    )
    _record_apply_history(ProjectConfig(repo, "python"), args, [failed_validation, rollback])
    entry = load_history(repo)[0]
    assert entry["rollback_status"] == "rolled-back"
    assert entry["transaction"] == {"id": tx_id, "status": "rolled-back"}


def test_history_reader_remains_backward_compatible_with_legacy_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    registry = repo / ".devtool" / "artifacts.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"schema_version": 2, "artifacts": [{"id": "legacy", "status": "ok"}]}) + "\n")
    history = load_history(repo)
    assert any(item.get("id") == "legacy" and item.get("status") == "ok" for item in history)


# MR-086 ---------------------------------------------------------------------

def _repair_result(path: str) -> OperationResult:
    return OperationResult(
        "build", ["dotnet", "build"], 1, 0.0, "",
        [Issue("error", "build", path, "CS1061", "string does not contain a definition for GetFullPath", line=1)],
    )


def test_auto_repair_rejects_parent_path_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.cs"
    outside.write_text("var x = p.GetFullPath();\n")
    with pytest.raises(RuntimeError, match="escapes repository"):
        apply_auto_source_repairs(repo, [_repair_result("../outside.cs")])
    assert outside.read_text() == "var x = p.GetFullPath();\n"


def test_repair_mutations_join_apply_transaction_and_rollback_atomically(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    a = repo / "A.txt"
    b = repo / "B.cs"
    a.write_text("A-old\n")
    b.write_text("var x = p.GetFullPath();\n")
    overlay = _modify_overlay(tmp_path / "overlay.zip", "A.txt", b"A-old\n", b"A-new\n")
    tx = create_apply_transaction(repo, overlay, artifact_type="overlay", preview={"changes": [{"kind": "modify", "path": "A.txt"}], "counts": {"modify": 1}})
    a.write_text("A-new\n")
    applied, _backups = apply_auto_source_repairs(
        repo,
        [_repair_result("B.cs")],
        before_write=lambda path: register_transaction_paths(tx["path"], [path]),
    )
    assert applied and "System.IO.Path.GetFullPath" in b.read_text()
    result = rollback_transaction(tx["path"], reason="induced later failure")
    assert result.returncode == 0
    assert a.read_text() == "A-old\n"
    assert b.read_text() == "var x = p.GetFullPath();\n"
