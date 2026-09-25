from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.federation_execution import FederationExecutionError, run_federated_workflow
from devtool.federation_lock import (
    FederationSourceLock,
    FederationSourceLockError,
    build_federation_source_lock,
    compare_federation_source_locks,
    read_federation_source_lock,
    resolve_execution_source_lock,
    source_lock_path,
    update_federation_source_lock_repository,
    write_federation_source_lock,
)
from devtool.federation_plan import build_federated_plan


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(
            root,
            root / ".devtool.toml",
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)


def _child_config(root: Path, *, marker: str = "one") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tracked.txt").write_text(f"{marker}\n", encoding="utf-8")
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, two: bool = False, git: bool = True):
    parent = tmp_path / "parent"
    child1 = tmp_path / "child1"
    parent.mkdir(parents=True)
    _child_config(child1, marker="one")
    if git:
        _git_init(child1)
    repos = '[federation.repositories.child1]\npath="../child1"\n'
    steps = '[{id="one",ref="child1::app::workflow:validate"}]'
    child2 = None
    if two:
        child2 = tmp_path / "child2"
        _child_config(child2, marker="two")
        if git:
            _git_init(child2)
        repos += '[federation.repositories.child2]\npath="../child2"\n'
        steps = '[{id="one",ref="child1::app::workflow:validate"},{id="two",ref="child2::app::workflow:validate",depends_on=["one"]}]'
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
        '[federation]\nschema=1\n' + repos + '[federation.workflows.release]\nsteps=' + steps + '\n',
        encoding="utf-8",
    )
    return _load(parent), child1, child2


def _commit(root: Path, message: str = "change") -> str:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def test_plan_freezes_git_source_and_node_dependencies(tmp_path: Path) -> None:
    config, child, _ = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    lock = FederationSourceLock.from_dict(plan.source_lock)
    assert lock.kind == "federation-source-lock"
    assert lock.subject_kind == "workflow"
    assert lock.workflow == "release"
    assert len(lock.lock_digest) == 64
    assert len(lock.local_digest) == 64
    assert len(lock.equivalence_digest) == 64
    entry = lock.repositories[0]
    assert entry.repository == "child1"
    assert entry.source_kind == "git"
    assert entry.git_available is True
    assert entry.start_commit
    assert entry.head_tree
    assert entry.index_tree
    assert entry.config_digest
    assert entry.commit_policy == "none"
    assert "{federation_run_id}" in entry.expected_result_ref
    assert entry.node_dependencies and entry.node_dependencies[0]["node"].startswith("child1::")
    assert entry.repository_root == str(child.resolve())


def test_portable_lock_digest_is_path_independent_while_local_digest_is_not(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    seed.mkdir(); _child_config(seed); _git_init(seed)
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    clones = []
    locks = []
    for name in ("a", "b"):
        child = tmp_path / f"child-{name}"
        subprocess.run(["git", "clone", "-q", str(bare), str(child)], check=True)
        parent = tmp_path / f"parent-{name}"
        parent.mkdir()
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            f'[federation]\nschema=1\n[federation.repositories.child1]\npath="../child-{name}"\n'
            '[federation.workflows.release]\nsteps=[{id="one",ref="child1::app::workflow:validate"}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        locks.append(FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock))
        clones.append(child)
    assert locks[0].lock_digest == locks[1].lock_digest
    assert locks[0].equivalence_digest == locks[1].equivalence_digest
    assert locks[0].local_digest != locks[1].local_digest


def test_persisted_lock_lives_outside_checkout_and_does_not_dirty_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    lock = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    before = subprocess.check_output(["git", "-C", str(child), "status", "--porcelain"], text=True)
    path = write_federation_source_lock(config, lock)
    after = subprocess.check_output(["git", "-C", str(child), "status", "--porcelain"], text=True)
    assert path == source_lock_path(config)
    assert not str(path).startswith(str(config.repo_root))
    assert before == after == ""
    assert read_federation_source_lock(config).lock_digest == lock.lock_digest


def test_ephemeral_plan_fails_closed_if_source_moves_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    planned = build_federated_plan(config, "release")
    (child / "tracked.txt").write_text("moved-after-plan\n", encoding="utf-8")
    _commit(child)
    with pytest.raises(FederationSourceLockError, match="changed after planning"):
        resolve_execution_source_lock(config, planned, drift_policy="fail")


def test_default_fail_policy_rejects_commit_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    plan = build_federated_plan(config, "release")
    write_federation_source_lock(config, FederationSourceLock.from_dict(plan.source_lock))
    (child / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _commit(child)
    current = build_federated_plan(config, "release")
    with pytest.raises(FederationSourceLockError, match="source drift"):
        resolve_execution_source_lock(config, current, drift_policy="fail")


def test_allow_if_equivalent_accepts_new_commit_with_same_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    subprocess.run(["git", "-C", str(child), "commit", "--allow-empty", "-qm", "metadata-only"], check=True)
    current = build_federated_plan(config, "release")
    current_lock = FederationSourceLock.from_dict(current.source_lock)
    assert frozen.lock_digest != current_lock.lock_digest
    assert frozen.equivalence_digest == current_lock.equivalence_digest
    resolved, verification = resolve_execution_source_lock(config, current, drift_policy="allow-if-equivalent")
    assert verification["matched"] is True
    assert verification["mode"] == "equivalence"
    assert resolved.lock_digest == frozen.lock_digest


def test_allow_if_equivalent_rejects_tree_or_config_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    (child / "tracked.txt").write_text("different\n", encoding="utf-8")
    _commit(child)
    with pytest.raises(FederationSourceLockError, match="source drift"):
        resolve_execution_source_lock(config, build_federated_plan(config, "release"), drift_policy="allow-if-equivalent")


def test_dirty_worktree_state_is_part_of_frozen_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    (child / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    current = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    assert current.repositories[0].dirty is True
    assert current.repositories[0].dirty_identity["digest"] != frozen.repositories[0].dirty_identity["digest"]
    with pytest.raises(FederationSourceLockError):
        resolve_execution_source_lock(config, build_federated_plan(config, "release"), drift_policy="fail")


def test_replan_policy_explicitly_replaces_persisted_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    old = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, old)
    (child / "tracked.txt").write_text("new\n", encoding="utf-8")
    _commit(child)
    current_plan = build_federated_plan(config, "release")
    resolved, verification = resolve_execution_source_lock(config, current_plan, drift_policy="replan")
    assert verification["matched"] is True
    assert verification["source"] == "replanned"
    assert resolved.lock_digest != old.lock_digest
    assert read_federation_source_lock(config).lock_digest == resolved.lock_digest


def test_lock_update_refreshes_only_named_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child1, child2 = _fixture(tmp_path / "fixture", two=True)
    assert child2 is not None
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    before = {item.repository: item.source_digest for item in frozen.repositories}
    (child1 / "tracked.txt").write_text("updated\n", encoding="utf-8")
    _commit(child1)
    updated = update_federation_source_lock_repository(config, build_federated_plan(config, "release"), "child1")
    after = {item.repository: item.source_digest for item in updated.repositories}
    assert after["child1"] != before["child1"]
    assert after["child2"] == before["child2"]


def test_non_git_repository_uses_explicit_filesystem_snapshot_without_fake_git_identity(tmp_path: Path) -> None:
    config, child, _ = _fixture(tmp_path, git=False)
    lock = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    entry = lock.repositories[0]
    assert entry.source_kind == "filesystem"
    assert entry.git_available is False
    assert entry.repository_id == ""
    assert entry.start_commit == ""
    assert entry.filesystem_tree_digest
    (child / "tracked.txt").write_text("changed\n", encoding="utf-8")
    current = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    assert current.repositories[0].filesystem_tree_digest != entry.filesystem_tree_digest


def test_graph_dependency_change_invalidates_lock_even_when_source_is_unchanged(tmp_path: Path) -> None:
    config, _, _ = _fixture(tmp_path, two=True)
    first = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    parent_config = config.repo_root / ".devtool.toml"
    text = parent_config.read_text(encoding="utf-8")
    text = text.replace(',depends_on=["one"]', '')
    parent_config.write_text(text, encoding="utf-8")
    second_config = _load(config.repo_root)
    second = FederationSourceLock.from_dict(build_federated_plan(second_config, "release").source_lock)
    assert first.graph_digest != second.graph_digest
    assert compare_federation_source_locks(first, second, policy="allow-if-equivalent")["matched"] is False


def test_run_fails_before_child_execution_when_persisted_source_drifted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    write_federation_source_lock(config, FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock))
    (child / "tracked.txt").write_text("moved\n", encoding="utf-8")
    _commit(child)
    called = []
    with pytest.raises(FederationExecutionError, match="source drift"):
        run_federated_workflow(config, "release", reenter=lambda argv: called.append(argv) or 0)
    assert called == []


def test_run_allows_explicit_equivalent_commit_and_carries_frozen_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, _ = _fixture(tmp_path / "fixture")
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
    write_federation_source_lock(config, frozen)
    subprocess.run(["git", "-C", str(child), "commit", "--allow-empty", "-qm", "equivalent"], check=True)
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda argv: 0,
        drift_policy="allow-if-equivalent",
    )
    assert report.ok
    assert report.plan.source_lock["lock_digest"] == frozen.lock_digest
    assert report.children[0].run_lineage["frozen_source_lock_digest"] == frozen.lock_digest


def test_building_lock_never_moves_git_refs_or_mutates_index(tmp_path: Path) -> None:
    config, child, _ = _fixture(tmp_path)
    before_head = subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"], text=True).strip()
    before_status = subprocess.check_output(["git", "-C", str(child), "status", "--porcelain=v1"], text=True)
    plan = build_federated_plan(config, "release")
    build_federation_source_lock(config, plan)
    after_head = subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"], text=True).strip()
    after_status = subprocess.check_output(["git", "-C", str(child), "status", "--porcelain=v1"], text=True)
    assert after_head == before_head
    assert after_status == before_status


def test_cli_plan_freeze_and_lock_are_first_class_machine_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from devtool import cli

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, _, _ = _fixture(tmp_path / "fixture")
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(tmp_path / "missing-global.toml"))
    assert cli.main(["--repo", str(config.repo_root), "federation", "plan", "release", "--freeze", "--json"]) == 0
    planned = json.loads(capsys.readouterr().out)
    planned = planned.get("data", planned)
    assert planned["source_lock"]["kind"] == "federation-source-lock"
    assert planned["frozen_lock_path"] == str(source_lock_path(config))

    assert cli.main(["--repo", str(config.repo_root), "federation", "lock", "--json"]) == 0
    locked = json.loads(capsys.readouterr().out)
    locked = locked.get("data", locked)
    assert locked["verification"]["matched"] is True
    assert locked["lock"]["lock_digest"] == planned["source_lock"]["lock_digest"]


def test_cli_run_exposes_explicit_drift_policy_choices() -> None:
    from devtool.cli import build_parser

    args = build_parser().parse_args(["federation", "run", "release", "--drift-policy", "allow-if-equivalent"])
    assert args.drift_policy == "allow-if-equivalent"
    args = build_parser().parse_args(["federation", "plan", "release", "--freeze", "--drift-policy", "replan"])
    assert args.freeze is True
    assert args.drift_policy == "replan"
