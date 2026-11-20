"""S7 P01 current behavior: local audit, deterministic scaffold, no side effects."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from devtool.delivery_adoption import AdoptionError, audit_consumer, scaffold_overlay, main


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], text=True,
                          check=True, capture_output=True).stdout.strip()


@pytest.fixture
def consumer(tmp_path):
    root = tmp_path / "consumer"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "remote", "add", "origin", "git@github.com:example/consumer.git")
    (root / "README.md").write_text("before\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "launcher.sh").write_text("#!/bin/sh\ngh run watch 123\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "README.md").write_text("after\n")
    (payload / "docs").mkdir()
    (payload / "docs" / "new.txt").write_text("new file\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schema_version": 1, "changes": [
        {"path": "README.md", "action": "modify"},
        {"path": "docs/new.txt", "action": "add"}]}))
    return root, payload, plan, tmp_path / "out.zip"


def test_audit_is_read_only_and_bounded(consumer):
    root, *_ = consumer
    prior = git(root, "rev-parse", "HEAD")
    result = audit_consumer(root)
    assert result["source_sha"] == prior
    assert result["repository"] == "example/consumer"
    assert result["worktree_clean"] is True
    assert result["candidate_duplicate_launchers"] == ["scripts/launcher.sh"]
    assert result["ci_qualified"] is False and result["release_qualified"] is False
    assert git(root, "status", "--porcelain") == ""


def test_scaffold_exact_inventory_and_no_consumer_mutation(consumer):
    root, payload, plan, output = consumer
    sha = git(root, "rev-parse", "HEAD")
    result = scaffold_overlay(root, payload, plan, output, expected_head=sha)
    assert result["source_sha"] == sha and result["source_bound"] is True
    assert result["remote_mutations"] is False and result["applied"] is False
    assert output.exists() and (output.stat().st_mode & 0o777) == 0o600
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result["sha256"]
    assert git(root, "status", "--porcelain") == ""
    assert (root / "README.md").read_text() == "before\n"
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None
        assert z.read("README.md") == b"after\n"
        assert z.read("docs/new.txt") == b"new file\n"
        manifest = json.loads(z.read(".devtool-artifact.json"))
    assert manifest["schema_version"] == 2
    assert manifest["repository"]["branch"] == "main"
    assert manifest["repository"]["enforce_remote"] is True
    assert manifest["base_contract_mode"] == "exact-tree"
    assert manifest["base_commit"] == sha
    assert manifest["base_tree"] == git(root, "rev-parse", "HEAD^{tree}")
    assert set(manifest["touched_paths"]) == {"README.md", "docs/new.txt"}
    assert manifest["source_sha256"]["docs/new.txt"] is None
    assert manifest["source_sha256"]["README.md"] == hashlib.sha256(b"before\n").hexdigest()
    assert set(x["action"] for x in manifest["inventory"]["entries"]) == {"modify", "add"}
    assert manifest["inventory"]["entries"][0]["source_sha256"] == hashlib.sha256(b"before\n").hexdigest()
    assert not any(x.get("source_sha256") for x in manifest["inventory"]["entries"] if x["action"]=="add")


def test_stale_head_refused_before_writes(consumer):
    root, payload, plan, output = consumer
    with pytest.raises(AdoptionError, match="Expected source SHA"):
        scaffold_overlay(root, payload, plan, output, expected_head="f" * 40)
    assert not output.exists()


def test_dirty_source_refused(consumer):
    root, payload, plan, output = consumer
    (root / "README.md").write_text("dirty\n")
    with pytest.raises(AdoptionError, match="clean"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))
    assert not output.exists()


@pytest.mark.parametrize("unsafe", ["../escape", "/etc/passwd", "a/../b", "a//b", ".git/config", "a\\b", ".devtool-artifact.json"])
def test_inventory_path_traversal_refused(consumer, unsafe):
    root, payload, plan, output = consumer
    plan.write_text(json.dumps({"schema_version": 1, "changes": [{"path": unsafe, "action": "add"}]}))
    with pytest.raises(AdoptionError):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))
    assert not output.exists()


def test_misdeclared_add_and_modify_refused(consumer):
    root, payload, plan, output = consumer
    plan.write_text(json.dumps({"schema_version": 1, "changes": [{"path": "README.md", "action": "add"}]}))
    with pytest.raises(AdoptionError, match="already exists"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))
    plan.write_text(json.dumps({"schema_version": 1, "changes": [{"path": "docs/new.txt", "action": "modify"}]}))
    with pytest.raises(AdoptionError, match="tracked regular"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))


def test_duplicate_and_unknown_action_refused(consumer):
    root, payload, plan, output = consumer
    plan.write_text(json.dumps({"schema_version": 1, "changes": [
        {"path": "README.md", "action": "modify"},
        {"path": "README.md", "action": "modify"}]}))
    with pytest.raises(AdoptionError, match="Duplicate"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))
    plan.write_text(json.dumps({"schema_version": 1, "changes": [{"path": "README.md", "action": "delete"}]}))
    with pytest.raises(AdoptionError, match="unsupported"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))


def test_symlink_payload_blocked(consumer):
    root, payload, plan, output = consumer
    (payload / "README.md").unlink()
    (payload / "README.md").symlink_to(root / "README.md")
    with pytest.raises(AdoptionError, match="symlink"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))


def test_existing_output_never_overwritten(consumer):
    root, payload, plan, output = consumer
    output.write_bytes(b"user-content")
    with pytest.raises(AdoptionError, match="Output"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))
    assert output.read_bytes() == b"user-content"


def test_payload_root_inside_consumer_blocked(consumer):
    root, payload, plan, output = consumer
    with pytest.raises(AdoptionError, match="separate"):
        scaffold_overlay(root, root, plan, output, expected_head=git(root,"rev-parse","HEAD"))


def test_strict_plan_fields_refused(consumer):
    root, payload, plan, output = consumer
    plan.write_text(json.dumps({"schema_version":1, "changes": [], "shell_command":"rm -rf /"}))
    with pytest.raises(AdoptionError, match="Expected only"):
        scaffold_overlay(root, payload, plan, output, expected_head=git(root,"rev-parse","HEAD"))


def test_cli_audit_json(consumer, capsys):
    root, payload, plan, output = consumer
    assert main(["audit", "--repo", str(root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == "devtool.delivery-adoption-audit.v1"


def test_cli_scaffold_wrong_head_does_not_exit_success(consumer, capsys):
    root, payload, plan, output = consumer
    assert main(["scaffold", "--repo", str(root), "--payload", str(payload),
                 "--plan", str(plan), "--output", str(output),
                 "--expected-head", "a" * 40]) == 2
    assert "DEVTOOL_S7_REFUSED" in capsys.readouterr().err
    assert not output.exists()


def test_exact_tree_contract_rejects_new_clean_consumer_commit(consumer):
    from devtool.artifact_base_contract import validate_base_contract
    root, payload, plan, output = consumer
    old = git(root, "rev-parse", "HEAD")
    scaffold_overlay(root, payload, plan, output, expected_head=old)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read(".devtool-artifact.json"))
    (root / "unrelated.txt").write_text("new unrelated change\n")
    git(root, "add", "unrelated.txt")
    git(root, "commit", "-m", "new clean head")
    assert git(root, "status", "--porcelain") == ""
    verified = validate_base_contract(root, manifest, manifest["inventory"]["entries"])
    assert not verified.ok
    assert any("exact-tree base_commit mismatch" in issue for issue in verified.messages)


def test_noncanonical_origin_refused(consumer):
    root, payload, plan, output = consumer
    git(root, "remote", "set-url", "origin", "https://example.invalid/fake")
    with pytest.raises(AdoptionError, match="canonical GitHub"):
        audit_consumer(root)
    with pytest.raises(AdoptionError, match="canonical GitHub"):
        scaffold_overlay(root, payload, plan, output,
                         expected_head=git(root,"rev-parse","HEAD"))


def test_termux_without_os_link_still_writes_exclusive_verified_overlay(consumer, monkeypatch):
    """Android/Termux builds can omit os.link entirely."""
    root, payload, plan, output = consumer
    monkeypatch.delattr(os, "link", raising=False)
    receipt = scaffold_overlay(root, payload, plan, output,
                               expected_head=git(root, "rev-parse", "HEAD"))
    assert receipt["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert (output.stat().st_mode & 0o777) == 0o600
    assert git(root, "status", "--porcelain") == ""


def test_output_created_during_publish_is_never_overwritten(consumer, monkeypatch):
    """No TOCTOU overwrite if another process creates the output at the last moment."""
    root, payload, plan, output = consumer
    original_open = os.open
    def racing_open(path, flags, mode=0o777, **kwargs):
        if os.fspath(path) == os.fspath(output) and flags & os.O_EXCL:
            output.write_bytes(b"another-writer")
        return original_open(path, flags, mode, **kwargs)
    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(FileExistsError):
        scaffold_overlay(root, payload, plan, output,
                         expected_head=git(root, "rev-parse", "HEAD"))
    assert output.read_bytes() == b"another-writer"
    assert not list(output.parent.glob(".s7-scaffold-*.zip"))
    assert git(root, "status", "--porcelain") == ""


def test_exclusive_publish_failure_cleans_own_output(consumer, monkeypatch):
    """An I/O failure cannot leave an apparently accepted broken ZIP."""
    root, payload, plan, output = consumer
    original_fsync = os.fsync
    fsync_count = 0
    def fail_second_fsync(fd):
        nonlocal fsync_count
        fsync_count += 1
        if fsync_count == 2:
            raise OSError("simulated output fsync failure")
        return original_fsync(fd)
    monkeypatch.setattr(os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="simulated output fsync failure"):
        scaffold_overlay(root, payload, plan, output,
                         expected_head=git(root, "rev-parse", "HEAD"))
    assert not output.exists()
    assert not list(output.parent.glob(".s7-scaffold-*.zip"))
    assert git(root, "status", "--porcelain") == ""


# Current v3.2.1 native scaffold must share the source-bound launcher byte budget.
# Retain original symlink, traversal, exact-tree, no-clobber and I/O-failure tests.
def test_native_scaffold_capacity_matches_launcher_contract():
    from devtool import delivery_adoption as ad, launcher_contract_v3 as launcher
    assert ad._MAX_FILE == launcher.MAX_ONE == 5 * 1024 * 1024
    assert ad._MAX_TOTAL == launcher.MAX_TOTAL == 6 * 1024 * 1024


def _capacity_fixture(consumer, sizes):
    root, payload, plan, output = consumer
    inventory=[]
    for i, n in enumerate(sizes):
        path=f'capacity/file{i}.bin'
        target=payload/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'x'*n)
        inventory.append({'path':path,'action':'add'})
    plan.write_text(json.dumps({'schema_version':1,'changes':inventory}))
    return root,payload,plan,output


def test_scaffold_accepts_four_mib_source_bounded_file(consumer):
    root,payload,plan,output=_capacity_fixture(consumer,[4*1024*1024])
    sha=git(root,'rev-parse','HEAD')
    receipt=scaffold_overlay(root,payload,plan,output,expected_head=sha)
    assert receipt['source_sha']==sha
    with zipfile.ZipFile(output) as z:
        assert len(z.read('capacity/file0.bin')) == 4*1024*1024
        assert z.testzip() is None
    assert git(root,'status','--porcelain')==''


def test_scaffold_rejects_file_over_five_mib(consumer):
    root,payload,plan,output=_capacity_fixture(consumer,[5*1024*1024+1])
    with pytest.raises(AdoptionError,match='oversized'):
        scaffold_overlay(root,payload,plan,output,expected_head=git(root,'rev-parse','HEAD'))
    assert not output.exists()


def test_scaffold_rejects_total_over_six_mib(consumer):
    root,payload,plan,output=_capacity_fixture(consumer,[4*1024*1024,2*1024*1024+1])
    with pytest.raises(AdoptionError,match='byte budget'):
        scaffold_overlay(root,payload,plan,output,expected_head=git(root,'rev-parse','HEAD'))
    assert not output.exists()
