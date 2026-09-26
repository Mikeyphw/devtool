from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from devtool.config import load_project_config
from devtool.git_audit import record_audit_pass
from devtool.git_campaign import checkpoint_campaign, start_campaign
from devtool.git_final_seal import GitFinalSealError, build_campaign_seal, verify_campaign_seal
from devtool.git_execution_notes import append_execution_note
from devtool.git_signing import GitSigningDecision, GitSigningError, final_tag_argv, normalize_signing_mode, resolve_signing_decision
from devtool.models import ProjectConfig
from devtool.runners import git_commit


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def init_repo(root: Path, *, filename: str = "state.txt") -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Devtool Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / filename).write_text("base\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")


def campaign_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    init_repo(parent, filename="control.txt")
    init_repo(child)
    (child / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
        'command=["python","-c","print(\'ok\')"]\nmetadata={read_only=true}\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n', encoding="utf-8"
    )
    git(child, "add", ".devtool.toml")
    git(child, "commit", "-qm", "config")
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[git]\nsigning="off"\n[workspace]\ndefault_target="root"\n'
        '[targets.root]\nrunner="command"\n[federation]\nschema=1\n'
        '[federation.repositories.child]\npath="../child"\n'
        '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::validate"}]\n', encoding="utf-8"
    )
    git(parent, "add", ".devtool.toml")
    git(parent, "commit", "-qm", "federation")
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    campaign = start_campaign(config, "release-campaign", workflow="release")
    initial = campaign["generations"][0]["repositories"][0]
    start_commit = str(initial["commit"])
    repository_id = str(initial["repository_id"])
    (child / "state.txt").write_text("result\n", encoding="utf-8")
    git(child, "add", "state.txt")
    git(child, "commit", "-qm", "result")
    result_commit = git(child, "rev-parse", "HEAD")
    result_ref = "refs/devtool/federations/fixture-parent/repos/child/result"
    rollback_ref = "refs/devtool/federations/fixture-parent/repos/child/rollback"
    git(child, "update-ref", result_ref, result_commit)
    git(child, "update-ref", rollback_ref, start_commit)
    lineage_path = tmp_path / "xdg" / "federation-git-lineage" / "fixture-parent" / "fixture-child.json"
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "federation-git-lineage",
        "state": "finalized",
        "parent_run_id": "fixture-parent",
        "child_run_id": "fixture-child",
        "repository": "child",
        "repository_id": repository_id,
        "start_commit": start_commit,
        "result_commit": result_commit,
        "result_ref": result_ref,
        "rollback_ref": rollback_ref,
        "commit_mode": "new",
        "overlay_digests": ["fixture-overlay-sha256"],
        "supersession_pairs": [],
        "execution_provider": "local",
        "terminal_status": "succeeded",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_execution_note(child, result_commit, {
        "parent_run_id": "fixture-parent",
        "child_run_id": "fixture-child",
        "repository": "child",
        "target": "app",
        "contract_ref": "child::app::workflow:validate",
        "lifecycle": "executed",
        "frozen_lock_identity": "fixture-lock-digest",
        "frozen_lock_equivalence_identity": "fixture-lock-equivalence",
        "frozen_repository_source_identity": "fixture-source-digest",
        "capabilities": {"exports": ["shared/platform-v1"], "requires": [], "digest": "cap-digest"},
        "interface": {"name": "shared-library", "required_workflows": ["validate"], "digest": "iface-digest"},
        "scheduler": {"wave": 1, "placement": {"provider_id": "local"}, "parallel_effective": True, "max_parallel": 2},
        "resume_reuse": {"mode": "run", "resumed_from": "", "child_lifecycle": "executed"},
        "artifact_inputs": [{"name": "contracts", "producer_step": "platform", "sha256": "input-digest"}],
        "artifact_outputs": [{"id": "contracts", "sha256": "output-digest", "type": "json"}],
    })
    checkpoint_campaign(config, "release-campaign")
    record_audit_pass(config, campaign_id="release-campaign", roadmap_range="GIT-FRX-01..18", gap_count=0, pass_id="final-zero-gap")
    return config, parent, child


def test_signing_modes_are_strict():
    assert normalize_signing_mode("off") == "off"
    assert normalize_signing_mode("if-configured") == "if-configured"
    assert normalize_signing_mode("required") == "required"
    with pytest.raises(GitSigningError):
        normalize_signing_mode("auto")


def test_required_ssh_signing_fails_closed_without_verifiable_config(tmp_path: Path):
    repo = tmp_path / "repo"
    init_repo(repo)
    with pytest.raises(GitSigningError, match="required"):
        resolve_signing_decision(repo, "required")


def test_if_configured_signing_stays_off_without_ssh_verification_config(tmp_path: Path):
    repo = tmp_path / "repo"
    init_repo(repo)
    decision = resolve_signing_decision(repo, "if-configured")
    assert decision.mode == "if-configured"
    assert not decision.enabled
    assert not decision.configured


def test_runner_adds_git_S_only_when_signing_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    init_repo(repo)
    captured = []

    def fake_run_specs(config, specs, **kwargs):
        captured.extend(specs)
        return [], ""

    monkeypatch.setattr("devtool.runners.run_specs", fake_run_specs)
    git_commit(repo, "signed", sign=True)
    assert captured[1].argv[:3] == ["git", "commit", "-S"]
    captured.clear()
    git_commit(repo, "plain", sign=False)
    assert captured[1].argv[:3] == ["git", "commit", "-m"]



def test_configured_ssh_signing_enables_commit_and_signed_tag_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    init_repo(repo)
    values = {
        "gpg.format": "ssh",
        "user.signingKey": "/tmp/key",
        "user.signingkey": "/tmp/key",
        "gpg.ssh.allowedSignersFile": "/tmp/allowed",
    }
    monkeypatch.setattr("devtool.git_signing._config", lambda root, key: values.get(key, ""))
    decision = resolve_signing_decision(repo, "if-configured")
    assert decision.enabled and decision.configured and decision.format == "ssh"
    argv = final_tag_argv(decision, "devtool/campaign/1", "deadbeef", "manifest-sha256: abc")
    assert argv[:2] == ["tag", "-s"]


def test_final_seal_fails_if_source_changes_during_packaging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    import devtool.git_final_seal as sealmod
    real = sealmod.verify_zero_gap_audit_freshness
    calls = {"n": 0}
    def changing(cfg, cid):
        calls["n"] += 1
        payload = dict(real(cfg, cid))
        if calls["n"] >= 2:
            payload["status_digest"] = "changed-during-package"
        return payload
    monkeypatch.setattr(sealmod, "verify_zero_gap_audit_freshness", changing)
    with pytest.raises(GitFinalSealError, match="changed while final seal"):
        build_campaign_seal(config, "release-campaign")

def test_final_manifest_contains_required_repository_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    seal = build_campaign_seal(config, "release-campaign")
    assert seal["status"] == "ok"
    assert len(seal["manifest"]["repositories"]) == 1
    row = seal["manifest"]["repositories"][0]
    for key in (
        "repo_id", "start_commit", "result_commit", "integration_target", "overlay_digests",
        "commit_policy", "amended_superseded_commits", "validation_run_ids", "remote_provider",
        "remote_attestation", "frozen_lock_identity", "frozen_repository_source_identity",
        "canonical_contract_identity", "capabilities", "interface_conformance",
        "scheduler_provider_placement", "resume_reuse_state", "artifact_producer_consumer_digests",
        "notes_evidence_digest", "signing_verification", "rollback_ref", "final_tree",
    ):
        assert key in row
    assert seal["manifest"]["zero_gap_audit_pass_id"] == "final-zero-gap"
    assert seal["manifest_sha256"]
    assert Path(seal["path"]).is_file()
    verification = verify_campaign_seal(config, "release-campaign")
    assert verification["ok"] is True


def test_final_seal_invalidates_after_participating_source_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    build_campaign_seal(config, "release-campaign")
    (child / "state.txt").write_text("drift\n", encoding="utf-8")
    git(child, "add", "state.txt")
    git(child, "commit", "-qm", "drift")
    verification = verify_campaign_seal(config, "release-campaign")
    assert verification["ok"] is False
    assert not verification["zero_gap_fresh"]
    with pytest.raises(GitFinalSealError, match="zero-gap"):
        build_campaign_seal(config, "release-campaign")



def test_final_manifest_projects_y12_platform_provenance_from_execution_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, _parent, _child = campaign_fixture(tmp_path, monkeypatch)
    row = build_campaign_seal(config, "release-campaign")["manifest"]["repositories"][0]
    assert row["frozen_lock_identity"] == "fixture-lock-digest"
    assert row["frozen_repository_source_identity"] == "fixture-source-digest"
    assert row["canonical_contract_identity"] == "child::app::workflow:validate"
    assert row["capabilities"]["exports"] == ["shared/platform-v1"]
    assert row["interface_conformance"]["name"] == "shared-library"
    assert row["scheduler_provider_placement"]["placement"]["provider_id"] == "local"
    assert row["resume_reuse_state"]["child_lifecycle"] == "executed"
    assert row["artifact_producer_consumer_digests"]["inputs"][0]["sha256"] == "input-digest"
    assert row["artifact_producer_consumer_digests"]["outputs"][0]["sha256"] == "output-digest"


def test_final_seal_verification_detects_execution_note_provenance_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, _parent, child = campaign_fixture(tmp_path, monkeypatch)
    build_campaign_seal(config, "release-campaign")
    result_commit = git(child, "rev-parse", "HEAD")
    append_execution_note(child, result_commit, {
        "parent_run_id": "fixture-parent-2", "child_run_id": "fixture-child-2",
        "repository": "child", "target": "app", "contract_ref": "child::app::workflow:validate",
        "lifecycle": "executed", "frozen_lock_identity": "different-lock",
    })
    verification = verify_campaign_seal(config, "release-campaign")
    assert verification["ok"] is False
    assert {row["reason"] for row in verification["drift"]} == {"notes-drift"}

def test_required_signing_policy_blocks_unsigned_campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    config.git_config["signing"] = "required"
    with pytest.raises(GitFinalSealError, match="Signing policy failed"):
        build_campaign_seal(config, "release-campaign")


def test_optional_final_tag_references_manifest_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    seal = build_campaign_seal(config, "release-campaign", create_tag=True)
    tag = seal["tag"]["name"]
    assert tag == "devtool/release-campaign/1"
    assert git(parent, "rev-list", "-n", "1", tag) == git(parent, "rev-parse", "HEAD")
    tag_text = git(parent, "cat-file", "-p", tag)
    assert seal["manifest_sha256"] in tag_text



def test_cli_campaign_seal_emits_machine_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    from devtool.cli import main
    rc = main(["-r", str(parent), "git", "campaign", "seal", "release-campaign", "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    payload = envelope.get("data", envelope)
    assert payload["kind"] == "devtool.git-federation-final-seal"
    assert payload["manifest"]["campaign_id"] == "release-campaign"
    assert payload["manifest_sha256"]


def test_final_seal_rejects_changed_repository_without_finalized_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    lineage_root = tmp_path / "xdg" / "federation-git-lineage"
    for path in lineage_root.glob("*/*.json"):
        path.unlink()
    with pytest.raises(GitFinalSealError, match="without finalized Devtool Git lineage"):
        build_campaign_seal(config, "release-campaign")


def test_verify_final_tag_rechecks_manifest_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    seal = build_campaign_seal(config, "release-campaign", create_tag=True)
    assert verify_campaign_seal(config, "release-campaign")["tag_valid"] is True
    tag = seal["tag"]["name"]
    git(parent, "tag", "-d", tag)
    git(parent, "tag", "-a", tag, "-m", "wrong manifest")
    verification = verify_campaign_seal(config, "release-campaign")
    assert verification["ok"] is False
    assert verification["tag_valid"] is False


def test_verify_final_seal_rejects_campaign_digest_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, parent, child = campaign_fixture(tmp_path, monkeypatch)
    build_campaign_seal(config, "release-campaign")
    from devtool.git_campaign import campaign_path
    path = campaign_path("release-campaign")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["campaign_digest"] = "0" * 64
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verification = verify_campaign_seal(config, "release-campaign")
    assert verification["ok"] is False
    assert verification["campaign_digest_valid"] is False


def test_commit_parser_defers_signing_default_to_project_policy():
    from devtool.cli import build_parser
    args = build_parser().parse_args(["commit", "message"])
    assert args.signing == ""

def test_config_rejects_unknown_git_signing_mode(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".devtool.toml").write_text('[project]\nrunner="command"\n[git]\nsigning="maybe"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid \\[git\\].signing"):
        load_project_config(root, root / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
