from __future__ import annotations

import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.federation_discovery import resolve_federation_repository_owner
from devtool.federation_plan import build_federated_plan
from devtool.git_ownership import (
    GitFederatedOwnershipError,
    federated_git_ownership_payload,
    resolve_federated_git_owner,
)
from devtool.git_state import collect_git_state


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(
            root,
            root / ".devtool.toml",
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _repo(root: Path, body: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text('[project]\nrunner="command"\n' + body, encoding="utf-8")


def _git_init(root: Path, *, commit: bool = True) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Test"], check=True)
    if commit:
        (root / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)


def test_git_owner_uses_longest_recursive_explicit_root(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    grand = child / "grand"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="child"\n')
    _repo(child, '[federation]\nschema=1\n[federation.repositories.grand]\npath="grand"\n')
    _repo(grand)
    _git_init(grand)

    config = _load(parent)
    owner = resolve_federated_git_owner(config, grand / "tracked.txt")
    assert owner is not None
    assert owner.repository == "child::grand"
    assert owner.root == str(grand.resolve())
    assert owner.git["available"] is True
    assert owner.git["root"] == str(grand.resolve())


def test_git_owner_is_symlink_safe(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n')
    _repo(child)
    _git_init(child)
    link = tmp_path / "child-link"
    link.symlink_to(child, target_is_directory=True)

    owner = resolve_federated_git_owner(_load(parent), link / "tracked.txt")
    assert owner is not None
    assert owner.repository == "child"
    assert owner.canonical_path == str((child / "tracked.txt").resolve())
    assert owner.root == str(child.resolve())


def test_git_owner_rejects_unenrolled_nested_git_instead_of_parent_inheritance(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    nested = child / "vendor-checkout"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n')
    _repo(child)
    _git_init(child)
    nested.mkdir()
    _git_init(nested)

    config = _load(parent)
    # The older FRX resolver intentionally identifies only explicit membership;
    # GIT-FRX-07 adds the negative Git-boundary exclusion without auto-enrolling.
    frx_owner = resolve_federation_repository_owner(config, nested / "tracked.txt")
    assert frx_owner is not None and frx_owner.repository == "child"
    with pytest.raises(GitFederatedOwnershipError, match="unenrolled nested Git repository"):
        resolve_federated_git_owner(config, nested / "tracked.txt")


def test_git_owner_symlink_escape_to_sibling_is_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    sibling = tmp_path / "sibling"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n')
    _repo(child)
    _repo(sibling)
    _git_init(child)
    _git_init(sibling)
    escape = child / "escape"
    escape.symlink_to(sibling, target_is_directory=True)

    config = _load(parent)
    with pytest.raises(GitFederatedOwnershipError, match="outside the owning repository"):
        resolve_federated_git_owner(config, escape / "tracked.txt")


def test_git_owner_rejects_outside_and_sibling_paths(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    sibling = tmp_path / "sibling"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n')
    _repo(child)
    _repo(sibling)
    _git_init(child)
    _git_init(sibling)

    config = _load(parent)
    with pytest.raises(GitFederatedOwnershipError, match="outside the owning repository"):
        resolve_federated_git_owner(config, sibling / "tracked.txt")
    assert resolve_federated_git_owner(config, sibling / "tracked.txt", require_owned=False) is None


def test_git_owner_linked_worktree_keeps_stable_repository_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _repo(source)
    _git_init(source)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(source), "worktree", "add", "-q", "-b", "linked-fixture", str(linked)], check=True)

    parent = tmp_path / "parent"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../linked"\n')
    config = _load(parent)
    owner = resolve_federated_git_owner(config, linked / "tracked.txt")
    assert owner is not None
    main_state = collect_git_state(source)
    linked_state = collect_git_state(linked)
    assert owner.git["repository_id"] == main_state.repository_id == linked_state.repository_id
    assert owner.git["worktree_id"] == linked_state.worktree["id"]
    assert owner.git["is_linked_worktree"] is True
    assert linked_state.worktree["id"] != main_state.worktree["id"]


def _plan_fixture(tmp_path: Path):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )
    _git_init(child)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.workflows.release]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n''',
        encoding="utf-8",
    )
    return _load(parent), child


def test_federated_plan_exposes_machine_readable_git_ownership(tmp_path: Path) -> None:
    config, child = _plan_fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    assert set(plan.repository_ownership) == {"child"}
    evidence = plan.repository_ownership["child"]
    assert evidence["repository"] == "child"
    assert evidence["root"] == str(child.resolve())
    assert evidence["git"]["available"] is True
    assert evidence["git"]["repository_id"]
    node = next(node for node in plan.nodes if not node.synthetic)
    assert node.metadata["git_ownership"] == evidence
    payload = plan.as_dict()
    assert payload["repository_ownership"]["child"]["git"]["repository_id"] == evidence["git"]["repository_id"]


def test_public_ownership_payload_is_versioned_and_stable(tmp_path: Path) -> None:
    config, child = _plan_fixture(tmp_path)
    payload = federated_git_ownership_payload(config, child / "tracked.txt")
    assert payload["schema_version"] == 1
    assert payload["owner"]["schema_version"] == 1
    assert payload["owner"]["boundary_status"] == "owned"
    assert payload["owner"]["git"]["repository_id"]


def test_plan_fails_closed_if_child_path_enters_unenrolled_nested_git(tmp_path: Path) -> None:
    # This protects future planners from accidentally using a deeper checkout as
    # the enrolled child source.  The plan must expose the explicit child root,
    # not a filesystem-discovered nested repository.
    config, child = _plan_fixture(tmp_path)
    nested = child / "nested"
    nested.mkdir()
    _git_init(nested)
    # Normal child plan remains valid because its owned root is the enrolled
    # child itself; only paths inside nested are excluded.
    plan = build_federated_plan(config, "release")
    assert plan.repository_ownership["child"]["root"] == str(child.resolve())
    with pytest.raises(GitFederatedOwnershipError, match="unenrolled nested Git repository"):
        resolve_federated_git_owner(config, nested / "tracked.txt")


def test_non_git_explicit_repository_remains_logically_owned_without_fake_git_identity(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    _repo(parent, '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n')
    _repo(child)
    owner = resolve_federated_git_owner(_load(parent), child / "file.txt")
    assert owner is not None and owner.repository == "child"
    assert owner.git["available"] is False
    assert owner.git["repository_id"] == ""
