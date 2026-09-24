from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config, resolve_config_dict
from devtool.core.events import EventBus, RunStarted
from devtool.repository_identity import (
    RepositoryIdentity,
    RepositoryIdentityError,
    assert_repository_identity_matches,
    build_repository_identity,
    same_repository_identity,
)
from devtool.workflow_runtime import build_workspace_specs, plan_workspace_targets


def _write_repo_config(root: Path) -> None:
    (root / ".devtool.toml").write_text(
        """
[project]
runner = "command"

[workspace]
default_target = "app"

[targets.app]
runner = "command"

[jobs.echo]
runner = "command"
command = ["echo", "ok"]

[targets.app.workflows]
build = ["job:echo"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_repository_identity_round_trip_and_path_canonicalization(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo_config(root)
    link = tmp_path / "repo-link"
    link.symlink_to(root, target_is_directory=True)

    direct = build_repository_identity(root, root / ".devtool.toml")
    via_link = build_repository_identity(link, link / ".devtool.toml")
    restored = RepositoryIdentity.from_dict(direct.as_dict())

    assert direct.root == str(root.resolve())
    assert direct.config_path == str((root / ".devtool.toml").resolve())
    assert same_repository_identity(direct, via_link)
    assert restored == direct
    assert len(direct.fingerprint) == 64


def test_repository_fingerprint_is_not_selected_config_filename(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo_config(root)
    alternate = root / "devtool-alt.toml"
    alternate.write_text((root / ".devtool.toml").read_text(encoding="utf-8"), encoding="utf-8")

    default_identity = build_repository_identity(root, root / ".devtool.toml")
    alternate_identity = build_repository_identity(root, alternate)

    assert same_repository_identity(default_identity, alternate_identity)
    assert default_identity.config_path != alternate_identity.config_path


def test_nested_git_repositories_never_collapse_identity(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(parent)], check=True)
    subprocess.run(["git", "init", "-q", str(child)], check=True)
    _write_repo_config(parent)
    _write_repo_config(child)

    parent_identity = build_repository_identity(parent, parent / ".devtool.toml")
    child_identity = build_repository_identity(child, child / ".devtool.toml")

    assert parent_identity.git_root == str(parent.resolve())
    assert child_identity.git_root == str(child.resolve())
    assert not same_repository_identity(parent_identity, child_identity)
    with pytest.raises(RepositoryIdentityError, match="identity mismatch"):
        assert_repository_identity_matches(parent_identity, child_identity, context="nested child")


def test_config_ownership_allows_ancestor_owner_and_rejects_descendant_or_unrelated(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    unrelated = tmp_path / "unrelated"
    child.mkdir(parents=True)
    unrelated.mkdir()
    _write_repo_config(parent)
    _write_repo_config(child)
    _write_repo_config(unrelated)

    ancestor_owned = build_repository_identity(child, parent / ".devtool.toml")
    assert ancestor_owned.root == str(parent.resolve())
    assert ancestor_owned.requested_root == str(child.resolve())

    with pytest.raises(RepositoryIdentityError, match="ownership mismatch"):
        build_repository_identity(parent, child / ".devtool.toml")
    with pytest.raises(RepositoryIdentityError, match="ownership mismatch"):
        build_repository_identity(child, unrelated / ".devtool.toml")


def test_parent_config_governing_deeper_project_keeps_owner_identity(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "subdir"
    child.mkdir(parents=True)
    _write_repo_config(parent)

    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}, clear=False):
        config = load_project_config(str(child), None, allow_ambiguous_target=True)
    assert config.repo_root == child.resolve()
    assert config.repository_identity is not None
    assert config.repository_identity.root == str(parent.resolve())
    assert config.repository_identity.requested_root == str(child.resolve())


def test_loaded_config_and_machine_resolution_expose_repository_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo_config(root)
    config = _load(root)

    assert config.repository_identity is not None
    payload = resolve_config_dict(config)
    assert payload["repository_identity"] == config.repository_identity.as_dict()
    assert payload["repository_identity"]["root"] == str(root.resolve())


def test_workflow_plan_and_specs_carry_repository_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo_config(root)
    config = _load(root)
    expected = config.repository_identity.as_dict()

    graph = plan_workspace_targets(config, ["app"], "build")
    assert graph.nodes
    assert all(dict(node.metadata).get("repository_identity") == expected for node in graph.nodes)

    specs = build_workspace_specs(config, ["app"], "build")
    assert specs
    assert all(spec.metadata.get("repository_identity") == expected for spec in specs)


def test_execution_store_persists_first_class_repository_identity(tmp_path: Path) -> None:
    identity = build_repository_identity(tmp_path)
    bus = EventBus()
    bus.publish(
        RunStarted(
            run_id="frx01-repo",
            command="build",
            project=str(tmp_path),
            metadata={"repository_identity": identity.as_dict()},
        )
    )
    snapshot = bus.execution_store.snapshot("frx01-repo")
    assert snapshot is not None
    assert snapshot.schema_version == 7
    assert snapshot.repository_identity == identity.as_dict()
    assert snapshot.to_dict()["repository_identity"] == identity.as_dict()


def test_cli_run_started_emits_repository_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_repo_config(tmp_path)
    events = []
    monkeypatch.setattr(cli, "publish_event", events.append)
    monkeypatch.setattr(cli, "finalize_execution_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_main_after_parse", lambda *args, **kwargs: 0)

    code = cli.main(["--repo", str(tmp_path), "build"])
    assert code == 0
    assert events
    payload = events[0].metadata.get("repository_identity")
    assert isinstance(payload, dict)
    assert payload["root"] == str(tmp_path.resolve())
    assert len(payload["fingerprint"]) == 64


def test_identity_json_is_secret_neutral(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo_config(root)
    identity = build_repository_identity(root, root / ".devtool.toml")
    encoded = json.dumps(identity.as_dict(), sort_keys=True)
    # Git remote contents, if any, are represented only by a digest.
    assert "remote.origin.url" not in encoded
    assert "git_remote_fingerprint" in encoded
