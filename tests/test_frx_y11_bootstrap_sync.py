from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.federation_lock import (
    FederationSourceLock,
    read_federation_source_lock,
    source_lock_path,
    write_federation_source_lock,
)
from devtool.federation_plan import build_federated_plan
from devtool.federation_sync import federation_source_management


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, stdout=subprocess.DEVNULL)


def _child_files(root: Path, marker: str = "one") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tracked.txt").write_text(marker + "\n", encoding="utf-8")
    (root / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n'
        '[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print(\'ok\')"]\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n',
        encoding="utf-8",
    )


def _load(parent: Path, *, allow_missing: bool = False):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}, clear=False):
        return load_project_config(
            parent,
            parent / ".devtool.toml",
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_missing_federation_repositories=allow_missing,
        )


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    child = tmp_path / "child"
    parent = tmp_path / "parent"
    _child_files(seed, "v1")
    subprocess.run(["git", "init", "-q", str(seed)], check=True)
    _run(seed, "config", "user.email", "test@example.invalid")
    _run(seed, "config", "user.name", "Devtool Test")
    _run(seed, "add", ".")
    _run(seed, "commit", "-qm", "v1")
    first = _git(seed, "rev-parse", "HEAD")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(child)], check=True)
    _run(child, "config", "user.email", "test@example.invalid")
    _run(child, "config", "user.name", "Devtool Test")
    parent.mkdir()
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
        '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
        '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n',
        encoding="utf-8",
    )
    config = _load(parent)
    return config, parent, child, seed, bare, first


def _freeze(config) -> FederationSourceLock:
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    return frozen


def test_lock_carries_sanitized_clone_locator_without_changing_core_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, child, *_ = _fixture(tmp_path, monkeypatch)
    frozen = _freeze(config)
    entry = frozen.repositories[0]
    assert entry.remote_locator == _git(child, "config", "--get", "remote.origin.url")
    assert entry.remote_locator_digest == hashlib.sha256(entry.remote_locator.encode()).hexdigest()
    old = replace(entry, remote_locator="", remote_locator_digest="")
    roundtrip = replace(frozen, repositories=(old,))
    # Safe source locators are operational bootstrap evidence, not part of the pre-Y11 portable lock identity.
    assert roundtrip.lock_digest == frozen.lock_digest
    assert roundtrip.local_digest == frozen.local_digest
    assert roundtrip.equivalence_digest == frozen.equivalence_digest


def test_bootstrap_preview_and_apply_materialize_missing_required_repo_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, parent, child, *_ = _fixture(tmp_path, monkeypatch)
    frozen = _freeze(config)
    lock_bytes = source_lock_path(config).read_bytes()
    shutil.rmtree(child)
    missing = _load(parent, allow_missing=True)
    preview = federation_source_management(missing, operation="bootstrap")
    assert preview.ok and preview.status == "planned"
    assert preview.actions[0].state == "planned"
    assert preview.actions[0].commands[0][:2] == ("git", "init")
    applied = federation_source_management(missing, operation="bootstrap", apply=True)
    assert applied.ok and applied.actions[0].state == "bootstrapped"
    assert applied.actions[0].verified is True
    assert _git(child, "rev-parse", "HEAD") == frozen.repositories[0].start_commit
    assert source_lock_path(missing).read_bytes() == lock_bytes
    assert applied.lock_updated is False


def test_normal_config_stays_fail_closed_for_missing_required_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, parent, child, *_ = _fixture(tmp_path, monkeypatch)
    _freeze(config)
    shutil.rmtree(child)
    with pytest.raises(SystemExit, match="required federation repository"):
        _load(parent, allow_missing=False)
    assert _load(parent, allow_missing=True).federation.repositories["child"].available is False


def test_sync_preview_and_apply_fast_forward_with_recovery_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, child, seed, bare, first = _fixture(tmp_path, monkeypatch)
    (seed / "tracked.txt").write_text("v2\n", encoding="utf-8")
    _run(seed, "add", ".")
    _run(seed, "commit", "-qm", "v2")
    second = _git(seed, "rev-parse", "HEAD")
    _run(seed, "remote", "add", "origin", str(bare))
    _run(seed, "push", "-q", "origin", "HEAD:master")
    _run(child, "fetch", "-q", "origin")
    _run(child, "merge", "--ff-only", second)
    config = _load(config.repo_root)
    frozen = _freeze(config)
    lock_bytes = source_lock_path(config).read_bytes()
    _run(child, "reset", "--hard", first)
    preview = federation_source_management(config, operation="sync")
    assert preview.ok and preview.actions[0].state == "planned"
    assert preview.actions[0].mutation == "fast-forward-to-lock"
    applied = federation_source_management(config, operation="sync", apply=True)
    action = applied.actions[0]
    assert applied.ok and action.state == "synced" and action.verified
    assert _git(child, "rev-parse", "HEAD") == second == frozen.repositories[0].start_commit
    assert action.recovery["verification"]["ok"] is True
    assert Path(action.recovery["path"]).is_file()
    assert source_lock_path(config).read_bytes() == lock_bytes


def test_sync_blocks_dirty_branch_and_remote_mismatch_without_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, child, *_ = _fixture(tmp_path, monkeypatch)
    frozen = _freeze(config)
    head = _git(child, "rev-parse", "HEAD")
    (child / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    dirty = federation_source_management(config, operation="sync", apply=True)
    assert dirty.actions[0].state == "blocked-dirty"
    assert _git(child, "rev-parse", "HEAD") == head
    _run(child, "checkout", "--", "tracked.txt")
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    _run(child, "remote", "set-url", "origin", str(other))
    mismatch = federation_source_management(config, operation="sync", apply=True)
    assert mismatch.actions[0].state == "blocked-remote-mismatch"
    assert _git(child, "rev-parse", "HEAD") == head == frozen.repositories[0].start_commit


def test_sync_blocks_non_fast_forward_without_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, child, *_ = _fixture(tmp_path, monkeypatch)
    frozen = _freeze(config)
    (child / "local.txt").write_text("ahead\n", encoding="utf-8")
    _run(child, "add", ".")
    _run(child, "commit", "-qm", "local-ahead")
    ahead = _git(child, "rev-parse", "HEAD")
    preview = federation_source_management(config, operation="sync")
    assert preview.actions[0].state == "blocked-non-fast-forward"
    applied = federation_source_management(config, operation="sync", apply=True)
    assert applied.actions[0].state == "blocked-non-fast-forward"
    assert _git(child, "rev-parse", "HEAD") == ahead
    assert ahead != frozen.repositories[0].start_commit


def test_bootstrap_old_lock_without_safe_locator_blocks_truthfully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, parent, child, *_ = _fixture(tmp_path, monkeypatch)
    frozen = _freeze(config)
    entry = replace(frozen.repositories[0], remote_locator="", remote_locator_digest="")
    write_federation_source_lock(config, replace(frozen, repositories=(entry,)))
    shutil.rmtree(child)
    missing = _load(parent, allow_missing=True)
    report = federation_source_management(missing, operation="bootstrap")
    assert not report.ok
    assert report.actions[0].state == "blocked-missing-source-locator"
    assert "refreeze" in report.actions[0].reason


def test_cli_bootstrap_is_preview_by_default_and_missing_repo_is_allowed_only_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from devtool import cli

    config, parent, child, *_ = _fixture(tmp_path, monkeypatch)
    _freeze(config)
    shutil.rmtree(child)
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    code = cli.main(["--repo", str(parent), "federation", "bootstrap", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    assert payload["operation"] == "bootstrap"
    assert payload["apply"] is False and payload["lock_updated"] is False
    assert payload["actions"][0]["state"] == "planned"
    assert cli.main(["--repo", str(parent), "federation", "plan", "release", "--json"]) == 1
    failed = json.loads(capsys.readouterr().out)
    assert "required federation repository" in str(failed.get("stderr") or failed.get("result", {}).get("stderr") or "")


def test_safe_locator_redacts_http_credentials_and_query() -> None:
    from devtool.federation_lock import sanitize_git_remote_locator
    assert sanitize_git_remote_locator("https://user:secret@example.com/org/repo.git?token=abc") == "https://example.com/org/repo.git"
    assert sanitize_git_remote_locator("ssh://git@example.com:2222/org/repo.git?x=secret") == "ssh://git@example.com:2222/org/repo.git"


def test_completion_exposes_source_management_without_breaking_legacy_sequence() -> None:
    from devtool.completion import ZSH_COMPLETION
    assert "status summary plan run exec resume retry lock integrate capabilities graph interfaces conformance interface-check affected why bootstrap sync" in ZSH_COMPLETION
    assert "--repo" in ZSH_COMPLETION


def test_ssh_runtime_does_not_own_repository_source_sync() -> None:
    import devtool
    root = Path(devtool.__file__).resolve().parent
    for path in sorted(root.glob("ssh_*.py")):
        text = path.read_text(encoding="utf-8")
        assert "federation_sync" not in text
        assert "federation_source_management" not in text


def test_sync_reports_partial_availability_across_multiple_locked_repositories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, parent, child, _seed, bare, _first = _fixture(tmp_path, monkeypatch)
    child2 = tmp_path / "child2"
    subprocess.run(["git", "clone", "-q", str(bare), str(child2)], check=True)
    _run(child2, "config", "user.email", "test@example.invalid")
    _run(child2, "config", "user.name", "Devtool Test")
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
        '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
        '[federation.repositories.child2]\npath="../child2"\n'
        '[federation.workflows.release]\nsteps=['
        '{id="child",ref="child::app::workflow:validate"},'
        '{id="child2",ref="child2::app::workflow:validate"}]\n',
        encoding="utf-8",
    )
    config = _load(parent)
    _freeze(config)
    (child2 / "local-untracked.txt").write_text("do not overwrite\n", encoding="utf-8")

    report = federation_source_management(config, operation="sync", apply=False)
    states = {item.repository: item.state for item in report.actions}
    assert report.ok is False
    assert report.partial is True
    assert report.status == "partial"
    assert states == {"child": "already-exact", "child2": "blocked-dirty"}
    assert (child2 / "local-untracked.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_cli_human_bootstrap_preview_uses_action_state_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from devtool import cli

    config, parent, child, *_ = _fixture(tmp_path, monkeypatch)
    _freeze(config)
    shutil.rmtree(child)
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    code = cli.main(["--repo", str(parent), "federation", "bootstrap"])
    output = capsys.readouterr().out
    assert code == 0
    assert "planned" in output
    assert "bootstrap" in output


def test_sync_apply_fetch_failure_is_truthful_and_preserves_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _parent, child, seed, bare, first = _fixture(tmp_path, monkeypatch)
    (seed / "tracked.txt").write_text("v2\n", encoding="utf-8")
    _run(seed, "add", ".")
    _run(seed, "commit", "-qm", "v2")
    second = _git(seed, "rev-parse", "HEAD")
    _run(seed, "remote", "add", "origin", str(bare))
    _run(seed, "push", "-q", "origin", "HEAD:master")
    _run(child, "fetch", "-q", "origin")
    _run(child, "merge", "--ff-only", second)
    config = _load(config.repo_root)
    _freeze(config)
    _run(child, "reset", "--hard", first)
    shutil.rmtree(bare)

    report = federation_source_management(config, operation="sync", apply=True)
    assert not report.ok
    assert report.status == "failed"
    assert report.actions[0].state == "failed"
    assert "fetch" in report.actions[0].reason.lower()
    assert _git(child, "rev-parse", "HEAD") == first
