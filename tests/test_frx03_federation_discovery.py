from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.doctor import doctor_payload
from devtool.federation_discovery import (
    FederationDiscoveryError,
    federation_boundary_payload,
    resolve_federated_repository,
)


def _child_config(*, default: str = "child-default") -> str:
    return f'''\n[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "{default}"\n\n[targets.{default}]\nrunner = "command"\n\n[targets.other]\nrunner = "command"\n'''.lstrip()


def _parent_config(child_path: str, *, parent_default: str = "parent-only", extra: str = "") -> str:
    return f'''\n[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "{parent_default}"\n\n[targets.{parent_default}]\nrunner = "command"\n\n[federation]\nschema = 1\n\n[federation.repositories.child]\npath = "{child_path}"\n{extra}\n'''.lstrip()


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_child_uses_own_config_and_workspace_default_not_parent_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")

    config = _load(parent)
    resolution = resolve_federated_repository(config, "child")

    assert config.selected_target == "parent-only"
    assert resolution.selected_target == "child-default"
    assert resolution.config_path == str((child / ".devtool.toml").resolve())
    assert resolution.repository_identity is not None
    assert resolution.repository_identity.root == str(child.resolve())
    assert resolution.repository_identity.fingerprint != config.repository_identity.fingerprint


def test_nested_child_without_own_config_never_borrows_parent_config(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "tools" / "child"
    child.mkdir(parents=True)
    (parent / ".devtool.toml").write_text(_parent_config("tools/child"), encoding="utf-8")

    config = _load(parent)
    resolution = resolve_federated_repository(config, "child")

    assert resolution.relation == "nested"
    assert resolution.config_path == ""
    assert resolution.repository_identity is not None
    assert resolution.repository_identity.root == str(child.resolve())
    assert "detected defaults" in resolution.boundary_detail
    assert resolution.selected_target == ""


def test_find_config_boundary_option_preserves_legacy_parent_lookup_but_federation_disables_it(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")

    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}, clear=False):
        legacy = load_project_config(str(child), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
        isolated = load_project_config(
            str(child), None,
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
            allow_parent_config=False,
        )
    assert legacy.config_path == (parent / ".devtool.toml").resolve()
    assert legacy.repository_identity.root == str(parent.resolve())
    assert isolated.config_path is None
    assert isolated.repository_identity.root == str(child.resolve())


def test_symlinked_enrollment_resolves_to_canonical_child_boundary(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    alias = tmp_path / "child-link"
    parent.mkdir(); child.mkdir(); alias.symlink_to(child, target_is_directory=True)
    (parent / ".devtool.toml").write_text(_parent_config("../child-link"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")

    resolution = resolve_federated_repository(_load(parent), "child")
    assert resolution.root == str(child.resolve())
    assert resolution.repository_identity.root == str(child.resolve())
    assert resolution.relation == "external"


def test_nested_git_repo_not_enrolled_is_not_discovered_implicitly(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    enrolled = tmp_path / "enrolled"
    hidden = parent / "hidden-repo"
    parent.mkdir(); enrolled.mkdir(); hidden.mkdir()
    subprocess.run(["git", "init", "-q", str(hidden)], check=True)
    (parent / ".devtool.toml").write_text(_parent_config("../enrolled"), encoding="utf-8")
    (enrolled / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    (hidden / ".devtool.toml").write_text(_child_config(default="hidden"), encoding="utf-8")

    payload = federation_boundary_payload(_load(parent))
    assert [item["name"] for item in payload["repositories"]] == ["child"]
    assert all(item["root"] != str(hidden.resolve()) for item in payload["repositories"])


def test_optional_missing_child_is_visible_but_not_resolved_through_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".devtool.toml").write_text(
        _parent_config("../missing", extra="required = false"), encoding="utf-8"
    )
    resolution = resolve_federated_repository(_load(parent), "child")
    assert resolution.available is False
    assert resolution.boundary_status == "unavailable"
    assert resolution.repository_identity is None


def test_unknown_child_fails_with_available_names(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    with pytest.raises(FederationDiscoveryError, match="Unknown federation repository 'missing'.*child"):
        resolve_federated_repository(_load(parent), "missing")


def test_cli_list_and_inspect_have_machine_payloads_and_ignore_parent_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    env = {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}

    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(parent), "--target", "parent-only", "federation", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    listed_data = listed.get("data", listed)
    assert listed_data["repositories"][0]["selected_target"] == "child-default"

    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(parent), "federation", "inspect", "child", "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    inspected_data = inspected.get("data", inspected)
    assert inspected_data["repository"]["name"] == "child"
    assert inspected_data["repository"]["selected_target"] == "child-default"
    assert inspected_data["repository"]["config_path"] == str((child / ".devtool.toml").resolve())


def test_parent_can_be_selected_from_child_cwd_and_child_from_parent_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "parent"; child = parent / "child"
    child.mkdir(parents=True)
    (parent / ".devtool.toml").write_text(_parent_config("child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    env = {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}

    monkeypatch.chdir(child)
    with mock.patch.dict(os.environ, env, clear=False):
        config = cli._federation_parent_config(argparse.Namespace(repo=str(parent), config=None))
    assert config.repository_identity.root == str(parent.resolve())
    assert resolve_federated_repository(config, "child").repository_identity.root == str(child.resolve())

    monkeypatch.chdir(parent)
    with mock.patch.dict(os.environ, env, clear=False):
        config2 = cli._federation_parent_config(argparse.Namespace(repo=str(parent), config=None))
    assert resolve_federated_repository(config2, "child").selected_target == "child-default"


def test_status_and_doctor_machine_payloads_include_boundary_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    config = _load(parent)

    status = cli._status_payload(config)
    assert status["federation"]["healthy"] is True
    assert status["federation"]["repositories"][0]["boundary_status"] == "ok"
    assert status["repository_identity"]["root"] == str(parent.resolve())

    # Keep this test focused on payload shape instead of host Android/root probes.
    import devtool.doctor as doctor_module
    monkeypatch.setattr(doctor_module, "collect_doctor_checks", lambda _config: [])
    payload = doctor_payload(config)
    assert payload["federation"]["healthy"] is True
    assert payload["federation"]["repositories"][0]["repository_identity"]["root"] == str(child.resolve())


def test_boundary_payload_tolerates_corrupt_child_only_when_requested(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text("[project\n", encoding="utf-8")
    config = _load(parent)

    with pytest.raises(FederationDiscoveryError, match="Cannot resolve federation repository"):
        federation_boundary_payload(config)
    tolerated = federation_boundary_payload(config, tolerate_errors=True)
    assert tolerated["healthy"] is False
    assert tolerated["repositories"][0]["boundary_status"] == "error"
    assert tolerated["errors"][0]["name"] == "child"
