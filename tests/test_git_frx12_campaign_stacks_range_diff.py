from __future__ import annotations

import subprocess
from pathlib import Path

from devtool.config import load_project_config
from devtool.git_campaign import checkpoint_campaign, load_campaign, range_diff_campaign, start_campaign


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=10)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _child(root: Path, label: str) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\nmetadata={read_only=true}\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )
    (root / "stack.txt").write_text(f"{label}-base\n", encoding="utf-8")
    _git(root, "add", "."); _git(root, "commit", "-qm", "base")
    return _git(root, "rev-parse", "HEAD")


def _fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    parent = tmp_path / "parent"; a = tmp_path / "a"; b = tmp_path / "b"
    parent.mkdir(); a_base = _child(a, "a"); b_base = _child(b, "b")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.repositories.a.exports]\ncapabilities=["lib/v1"]\n[federation.repositories.b]\npath="../b"\n[federation.repositories.b.requires]\ncapabilities=["lib/v1"]\n[federation.workflows.release]\nsteps=[\n{id="a",ref="a::app::validate"},\n{id="b",ref="b::app::validate",depends_on=["a"]},\n]\n''', encoding="utf-8")
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    return config, a, b, a_base, b_base


def _commit(root: Path, text: str, message: str) -> str:
    (root / "stack.txt").write_text(text + "\n", encoding="utf-8")
    _git(root, "add", "stack.txt"); _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def test_campaign_generations_freeze_lock_and_y_contract_identity(tmp_path: Path, monkeypatch) -> None:
    config, a, b, _ab, _bb = _fixture(tmp_path, monkeypatch)
    started = start_campaign(config, "release-42", workflow="release")
    assert started["generations"][0]["generation"] == 0
    assert started["contract_identity"]["digest"]
    assert started["contract_identity"]["steps"][1]["depends_on"] == ["a"]
    _commit(a, "a-v1", "a v1"); _commit(b, "b-v1", "b v1")
    gen1 = checkpoint_campaign(config, "release-42")
    assert gen1["generation"] == 1
    state = load_campaign("release-42")
    assert len(state["generations"]) == 2
    assert all(row["generation_ref"].startswith("refs/devtool/campaigns/release-42/") for row in gen1["repositories"])
    assert state["automatic_push"] is False


def test_multi_repo_range_diff_compares_revised_stack_generations(tmp_path: Path, monkeypatch) -> None:
    config, a, b, a_base, b_base = _fixture(tmp_path, monkeypatch)
    start_campaign(config, "release-43", workflow="release")
    a_old = _commit(a, "a-old", "a old"); b_old = _commit(b, "b-old", "b old")
    checkpoint_campaign(config, "release-43")
    _git(a, "reset", "--hard", "-q", a_base); _git(b, "reset", "--hard", "-q", b_base)
    a_new = _commit(a, "a-new", "a revised"); b_new = _commit(b, "b-new", "b revised")
    checkpoint_campaign(config, "release-43")
    report = range_diff_campaign("release-43", old_generation=1, new_generation=2)
    assert report["kind"] == "devtool.git-campaign-range-diff"
    assert report["contract_digest"]
    rows = {row["repository"]: row for row in report["repositories"]}
    assert rows["a"]["old_commit"] == a_old and rows["a"]["new_commit"] == a_new
    assert rows["b"]["old_commit"] == b_old and rows["b"]["new_commit"] == b_new
    assert all(len(row["range_diff_sha256"]) == 64 for row in rows.values())
    assert len(report["federation_lock_generations"]) == 2


def test_campaign_rejects_contract_drift_between_generations(tmp_path: Path, monkeypatch) -> None:
    from devtool.git_campaign import GitCampaignError
    import pytest
    config, _a, _b, _ab, _bb = _fixture(tmp_path, monkeypatch)
    start_campaign(config, "release-44", workflow="release")
    parent = tmp_path / "parent"
    text = (parent / ".devtool.toml").read_text(encoding="utf-8").replace('depends_on=["a"]', 'depends_on=[]')
    (parent / ".devtool.toml").write_text(text, encoding="utf-8")
    changed = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    with pytest.raises(GitCampaignError, match="contract identity drifted"):
        checkpoint_campaign(changed, "release-44")


def test_campaign_dirty_restore_rebases_nonoverlapping_local_state_after_head_change(tmp_path: Path, monkeypatch) -> None:
    from devtool.git_campaign import restore_campaign_dirty_tree
    config, a, _b, _ab, _bb = _fixture(tmp_path, monkeypatch)
    (a / "local.txt").write_text("local-only\n", encoding="utf-8")
    start_campaign(config, "release-dirty", workflow="release")
    _git(a, "clean", "-fdq")
    _commit(a, "a-v2", "a v2")
    restored = restore_campaign_dirty_tree("release-dirty", repository="a")
    assert restored["repositories"][0]["restore"]["index_restore_mode"] == "rebased-staged-entries"
    assert (a / "local.txt").read_text(encoding="utf-8") == "local-only\n"
