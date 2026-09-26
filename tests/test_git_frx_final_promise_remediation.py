from __future__ import annotations

import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from devtool.cli import build_parser, main
from devtool.config import load_project_config
from devtool.federation_qol import (
    federation_artifacts_payload, federation_diff_payload, federation_failures_payload,
    federation_graph_payload, federation_logs_payload,
)
from devtool.federation_resume import checkpoint_path
from devtool.git_campaign import (
    checkpoint_campaign, load_campaign, range_diff_campaign, rollback_campaign,
    start_campaign, supersede_campaign,
)
from devtool.git_doctor import federation_doctor
from devtool.git_federation_integration import (
    recover_federation_checkpoint, rollback_federation_checkpoint,
)
from devtool.reservations import repo_key
from devtool.transactions import create_apply_transaction
from devtool.xdg import state_dir


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return (proc.stdout or "").strip()


def init_repo(path: Path, text: str = "base\n") -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Devtool Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "file.txt").write_text(text, encoding="utf-8")
    git(path, "add", "."); git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def federation_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    parent = tmp_path / "parent"; child = tmp_path / "child"
    init_repo(parent, "parent\n"); init_repo(child, "child\n")
    (child / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
        '[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print(1)"]\nmetadata={read_only=true}\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n', encoding="utf-8")
    git(child, "add", ".devtool.toml"); git(child, "commit", "-qm", "config")
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[git]\nrerere="validated-auto"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
        '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\nrequired=true\n[federation.repositories.child.exports]\ncapabilities=["lib/v1"]\n'
        '[federation.groups.shared]\nrepositories=["child"]\n'
        '[federation.workflows.validate]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n', encoding="utf-8")
    git(parent, "add", ".devtool.toml"); git(parent, "commit", "-qm", "config")
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    return parent, child, config


def test_campaign_named_checkpoint_rollback_supersede_and_range_diff_classification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    started = start_campaign(config, "camp", workflow="validate")
    base_head = git(child, "rev-parse", "HEAD")
    (child / "file.txt").write_text("v1\n", encoding="utf-8"); git(child, "commit", "-am", "v1", "-q")
    gen1 = checkpoint_campaign(config, "camp", step_id="phase-one")
    v1 = git(child, "rev-parse", "HEAD")
    (child / "file.txt").write_text("v2\n", encoding="utf-8"); git(child, "commit", "-am", "v2", "-q")
    gen2 = checkpoint_campaign(config, "camp", step_id="phase-two")
    v2 = git(child, "rev-parse", "HEAD")
    rolled = rollback_campaign("camp", "phase-one")
    assert rolled["to_generation"] == 1
    assert git(child, "rev-parse", "HEAD") == v2, "campaign rollback must never rewrite user branches"
    tip_ref = gen1["repositories"][0]["tip_ref"]
    assert git(child, "rev-parse", tip_ref) == v1
    superseded = supersede_campaign("camp", 1, 2)
    assert superseded["new_generation"] == 2
    assert git(child, "rev-parse", tip_ref) == v2
    state = load_campaign("camp")
    assert state["step_generations"] == {"base": 0, "phase-one": 1, "phase-two": 2}
    assert state["rollbacks"] and state["supersessions"]
    delta = range_diff_campaign("camp", old_generation=1, new_generation=2)
    row = delta["repositories"][0]
    assert row["change_kind"] in {"forward", "superseded"}
    assert "validation_evidence_changed" in row
    assert started["generations"][0]["step_id"] == "base" and gen1["step_id"] == "phase-one" and gen2["step_id"] == "phase-two"
    assert base_head != v1 != v2


def test_top_level_campaign_compatibility_surface_and_preserve_dirty_flag_are_declared() -> None:
    parser = build_parser()
    args = parser.parse_args(["campaign", "rollback", "camp", "phase-one"])
    assert args.command == "campaign" and args.campaign_command == "rollback"
    apply_args = parser.parse_args(["apply", "overlay", "x.zip", "--preserve-dirty"])
    assert apply_args.preserve_dirty is True


def test_explicit_preserve_dirty_request_is_durable_transaction_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"; init_repo(root)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("DEVTOOL_PRESERVE_DIRTY_REQUESTED", "1")
    artifact = tmp_path / "noop.zip"
    with zipfile.ZipFile(artifact, "w"):
        pass
    tx = create_apply_transaction(root, artifact, artifact_type="overlay", preview={"changes": [], "counts": {}})
    assert tx["git_state"]["preserve_dirty_requested"] is True
    assert tx["git_state"]["dirty_preservation_policy"] == "explicit"


def _write_checkpoint(config, run_id: str, *, status: str, evidence: str, artifact: str = "") -> None:
    path = checkpoint_path(config, run_id); path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "step_id": "child", "repository": "child", "target": "app", "workflow": "validate",
        "run_id": f"{run_id}-child", "status": status, "exit_code": 0 if status == "succeeded" else 1,
        "evidence_digest": evidence, "predicates": {"source": "ok"}, "placement": {"provider": "local"},
        "artifact_inputs": ([{"name": "input", "digest": artifact}] if artifact else []),
        "artifact_identity": ({"digest": artifact} if artifact else {}), "result_identity": {"digest": evidence},
        "run_lineage": {"relationship": "child", "parent_run_id": run_id},
        "git_lineage": {},
    }
    payload = {"schema_version": 1, "parent_run_id": run_id, "workflow": "validate", "group": "", "status": status, "plan_digest": evidence, "source_lock_digest": "lock", "children": [row]}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_federation_qol_surfaces_project_authoritative_checkpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    _write_checkpoint(config, "run-a", status="failed", evidence="a", artifact="111")
    _write_checkpoint(config, "run-b", status="succeeded", evidence="b", artifact="222")
    graph = federation_graph_payload(config, group="shared")
    assert graph["group"] == "shared" and graph["members"] == ["child"]
    diff = federation_diff_payload(config, "run-a", "run-b")
    assert diff["changed_steps"] == 1 and "status" in diff["steps"][0]["changed_fields"]
    assert federation_artifacts_payload(config, "run-b")["artifacts"][0]["artifact_identity"]["digest"] == "222"
    assert federation_failures_payload(config, "run-a")["failures"][0]["status"] == "failed"
    logs = federation_logs_payload(config, "run-b")
    assert logs["child_run_ids"] == ["run-b-child"]


def test_recover_and_explicit_rollback_use_persisted_integration_evidence_without_touching_unrelated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    start = git(child, "rev-parse", "HEAD")
    (child / "file.txt").write_text("integrated\n", encoding="utf-8"); git(child, "commit", "-am", "result", "-q")
    result = git(child, "rev-parse", "HEAD")
    branch = git(child, "symbolic-ref", "HEAD")
    _write_checkpoint(config, "run-int", status="succeeded", evidence="ok")
    path = state_dir() / "federation-integrations" / repo_key(Path(config.repo_root)) / "run-int.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1, "kind": "devtool.federation.git-integration", "parent_run_id": "run-int", "workflow": "validate", "group": "", "policy": "per-repo", "status": "succeeded",
        "pointers": [{"repository": "child", "root": str(child), "step_ids": ["child"], "ref": branch, "start_commit": start, "result_commit": result, "status": "integrated", "applied": True, "rolled_back": False, "checked_out_worktree": str(child), "reason": ""}],
        "groups": [{"id": "repo:child", "repositories": ["child"], "status": "succeeded"}],
    }), encoding="utf-8")
    recovery = recover_federation_checkpoint(config, "run-int")
    assert recovery["repositories"][0]["rollback_available"] is True
    preview = rollback_federation_checkpoint(config, "run-int", repository="child", dry_run=True)
    assert preview["applied_repositories"] == [] and git(child, "rev-parse", "HEAD") == result
    applied = rollback_federation_checkpoint(config, "run-int", repository="child", dry_run=False)
    assert applied["applied_repositories"] == ["child"] and git(child, "rev-parse", "HEAD") == start


def test_rerere_config_and_cli_operate_capture_record_and_validated_auto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    root = tmp_path / "repo"; init_repo(root)
    (root / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[git]\nrerere="validated-auto"\n[[test]]\nid="resolution-check"\nname="resolution check"\nrunner="command"\ncommand=["python","-c","from pathlib import Path; raise SystemExit(0 if Path(\\"file.txt\\").read_text()==\\"resolved\\\\n\\" else 1)"]\n',
        encoding="utf-8")
    git(root, "add", ".devtool.toml"); git(root, "commit", "-qm", "config")
    git(root, "checkout", "-qb", "side"); (root / "file.txt").write_text("side\n", encoding="utf-8"); git(root, "commit", "-am", "side", "-q")
    git(root, "checkout", "-q", "master"); (root / "file.txt").write_text("main\n", encoding="utf-8"); git(root, "commit", "-am", "main", "-q")
    assert git(root, "merge", "side", check=False) == "" or True
    assert main(["-r", str(root), "git", "rerere", "capture", "--campaign", "camp", "--json"]) == 0; capsys.readouterr()
    (root / "file.txt").write_text("resolved\n", encoding="utf-8")
    assert main(["-r", str(root), "git", "rerere", "record", "--campaign", "camp", "--path", "file.txt", "--test", "resolution-check", "--json"]) == 0; capsys.readouterr()
    git(root, "merge", "--abort")
    proc = subprocess.run(["git", "-C", str(root), "merge", "side"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode != 0
    assert main(["-r", str(root), "git", "rerere", "apply", "--campaign", "camp", "--test", "resolution-check", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)["data"]
    assert output["mode"] == "validated-auto" and output["applied"] is True and output["validation_rerun"] is True
    assert (root / "file.txt").read_text(encoding="utf-8") == "resolved\n"


def test_federation_doctor_projects_platform_authorities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    payload = federation_doctor(config)
    names = {row["name"] for row in payload["platform_checks"]}
    assert {"capabilities", "interfaces", "frozen-lock", "resume-checkpoints", "artifact-provenance", "provider-placement", "hierarchy-lineage", "bootstrap-sync"} <= names
    sync = state_dir() / "federation-sync" / repo_key(Path(config.repo_root)) / "op" / "child.json"
    sync.parent.mkdir(parents=True); sync.write_text("{}", encoding="utf-8")
    broken = federation_doctor(config)
    row = next(item for item in broken["platform_checks"] if item["name"] == "bootstrap-sync")
    assert row["status"] == "error" and broken["ok"] is False


def test_cli_parser_exposes_federation_recovery_and_qol_surfaces() -> None:
    parser = build_parser()
    assert parser.parse_args(["federation", "recover", "run-a"]).federation_command == "recover"
    rollback = parser.parse_args(["federation", "rollback", "run-a", "--repo", "child"])
    assert rollback.rollback_repository == "child"
    assert parser.parse_args(["federation", "diff", "run-a", "run-b"]).federation_command == "diff"
    assert parser.parse_args(["federation", "graph", "--group", "shared"]).group == "shared"
    for action in ("artifacts", "logs", "failures"):
        assert parser.parse_args(["federation", action, "run-a"]).federation_command == action



def test_campaign_compatibility_cli_dispatches_named_checkpoint_and_private_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    start_campaign(config, "cli-camp", workflow="validate")
    (child / "file.txt").write_text("cli-v1\n", encoding="utf-8")
    git(child, "commit", "-am", "cli-v1", "-q")
    assert main(["-r", str(parent), "campaign", "checkpoint", "cli-camp", "phase-one", "--json"]) == 0
    checkpoint = json.loads(capsys.readouterr().out)["data"]
    assert checkpoint["step_id"] == "phase-one"
    user_head = git(child, "rev-parse", "HEAD")
    assert main(["-r", str(parent), "campaign", "rollback", "cli-camp", "base", "--json"]) == 0
    rollback = json.loads(capsys.readouterr().out)["data"]
    assert rollback["to_generation"] == 0 and rollback["user_branch_mutation"] is False
    assert git(child, "rev-parse", "HEAD") == user_head


def test_federation_qol_cli_dispatches_authoritative_checkpoint_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    _write_checkpoint(config, "old", status="failed", evidence="old", artifact="aaa")
    _write_checkpoint(config, "new", status="succeeded", evidence="new", artifact="bbb")
    for argv, expected_kind in (
        (["federation", "graph", "--group", "shared", "--json"], "devtool.federation-graph"),
        (["federation", "diff", "old", "new", "--json"], "devtool.federation-diff"),
        (["federation", "artifacts", "new", "--json"], "devtool.federation-artifacts"),
        (["federation", "logs", "new", "--json"], "devtool.federation-logs"),
        (["federation", "failures", "old", "--json"], "devtool.federation-failures"),
    ):
        assert main(["-r", str(parent), *argv]) == 0
        payload = json.loads(capsys.readouterr().out)["data"]
        assert payload["kind"] == expected_kind


def test_checked_in_zsh_completion_matches_runtime_generator() -> None:
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["python", "-m", "devtool", "completion", "zsh"], cwd=root,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.encode("utf-8") == (root / "completions" / "_devtool").read_bytes()



def test_federation_recover_and_rollback_cli_are_operational_not_parser_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parent, child, config = federation_fixture(tmp_path, monkeypatch)
    start = git(child, "rev-parse", "HEAD")
    (child / "file.txt").write_text("cli-integrated\n", encoding="utf-8")
    git(child, "commit", "-am", "cli result", "-q")
    result = git(child, "rev-parse", "HEAD")
    branch = git(child, "symbolic-ref", "HEAD")
    _write_checkpoint(config, "run-cli", status="succeeded", evidence="ok")
    path = state_dir() / "federation-integrations" / repo_key(Path(config.repo_root)) / "run-cli.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1, "kind": "devtool.federation.git-integration", "parent_run_id": "run-cli", "workflow": "validate", "group": "", "policy": "per-repo", "status": "succeeded",
        "pointers": [{"repository": "child", "root": str(child), "step_ids": ["child"], "ref": branch, "start_commit": start, "result_commit": result, "status": "integrated", "applied": True, "rolled_back": False, "checked_out_worktree": str(child), "reason": ""}],
        "groups": [{"id": "repo:child", "repositories": ["child"], "status": "succeeded"}],
    }), encoding="utf-8")

    assert main(["-r", str(parent), "federation", "recover", "run-cli", "--json"]) == 0
    recovered = json.loads(capsys.readouterr().out)["data"]
    assert recovered["repositories"][0]["rollback_available"] is True

    assert main(["-r", str(parent), "federation", "rollback", "run-cli", "--repo", "child", "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)["data"]
    assert preview["dry_run"] is True and git(child, "rev-parse", "HEAD") == result

    assert main(["--yes", "-r", str(parent), "federation", "rollback", "run-cli", "--repo", "child", "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)["data"]
    assert applied["applied_repositories"] == ["child"]
    assert git(child, "rev-parse", "HEAD") == start
