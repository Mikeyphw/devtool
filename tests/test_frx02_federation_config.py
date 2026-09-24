from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from devtool.config import global_config_doctor_entries, load_project_config, resolve_config_dict
from devtool.federation_config import (
    FEDERATION_SCHEMA_VERSION,
    FederationConfigError,
    federation_as_dict,
    load_federation_config,
)


def _base_project() -> str:
    return """
[project]
runner = "command"
""".strip() + "\n"


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_no_federation_section_is_backward_compatible(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(_base_project(), encoding="utf-8")
    config = _load(tmp_path)
    assert config.federation.schema == FEDERATION_SCHEMA_VERSION
    assert config.federation.enabled is False
    assert config.federation.repositories == {}
    assert config.federation.groups == {}
    assert resolve_config_dict(config)["federation"] == {
        "schema": 1,
        "enabled": False,
        "repositories": {},
        "groups": {},
    }


def test_repository_paths_are_relative_to_config_owner_not_launch_cwd(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    project = owner / "subproject"
    child = tmp_path / "child"
    project.mkdir(parents=True)
    child.mkdir()
    (owner / ".devtool.toml").write_text(
        _base_project()
        + """
[federation]
schema = 1

[federation.repositories.child]
path = "../child"
""",
        encoding="utf-8",
    )
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}, clear=False):
        config = load_project_config(str(project), None, allow_ambiguous_target=True)
    repo = config.federation.repositories["child"]
    assert repo.root == str(child.resolve())
    assert repo.declared_path == "../child"
    assert repo.available is True
    assert config.repository_identity is not None
    assert config.repository_identity.root == str(owner.resolve())


def test_sibling_and_nested_repositories_are_valid(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    sibling = tmp_path / "sibling"
    nested = owner / "tools" / "nested"
    owner.mkdir()
    sibling.mkdir()
    nested.mkdir(parents=True)
    section = {
        "schema": 1,
        "repositories": {
            "sibling": {"path": "../sibling"},
            "nested": {"path": "tools/nested"},
        },
        "groups": {"all": {"repositories": ["sibling", "nested"]}},
    }
    config = load_federation_config(section, owner_root=owner)
    assert config.enabled is True
    assert config.repositories["sibling"].root == str(sibling.resolve())
    assert config.repositories["nested"].root == str(nested.resolve())
    assert config.groups["all"].repositories == ("sibling", "nested")


def test_required_missing_repository_fails_closed_and_optional_missing_is_retained(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    with pytest.raises(FederationConfigError, match="required federation repository 'required'.*does not exist"):
        load_federation_config(
            {"schema": 1, "repositories": {"required": {"path": "../missing"}}},
            owner_root=owner,
        )

    config = load_federation_config(
        {"schema": 1, "repositories": {"optional": {"path": "../missing", "required": False}}},
        owner_root=owner,
    )
    repo = config.repositories["optional"]
    assert repo.required is False
    assert repo.available is False
    assert repo.root == str((tmp_path / "missing").resolve())


def test_self_reference_is_rejected_for_direct_relative_absolute_and_symlink_forms(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    alias = tmp_path / "owner-alias"
    alias.symlink_to(owner, target_is_directory=True)
    for path in (".", str(owner), str(alias)):
        with pytest.raises(FederationConfigError, match="self federation is not allowed"):
            load_federation_config(
                {"schema": 1, "repositories": {"self": {"path": path}}},
                owner_root=owner,
            )


def test_duplicate_canonical_child_roots_are_rejected_including_symlink_alias(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    child = tmp_path / "child"
    alias = tmp_path / "child-alias"
    owner.mkdir()
    child.mkdir()
    alias.symlink_to(child, target_is_directory=True)
    with pytest.raises(FederationConfigError, match="resolve to the same canonical root"):
        load_federation_config(
            {
                "schema": 1,
                "repositories": {
                    "one": {"path": "../child"},
                    "two": {"path": "../child-alias"},
                },
            },
            owner_root=owner,
        )




def test_optional_repository_that_exists_as_file_is_invalid(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    bad = tmp_path / "child-file"
    bad.write_text("not a repo", encoding="utf-8")
    with pytest.raises(FederationConfigError, match="exists but is not a directory"):
        load_federation_config(
            {"schema": 1, "repositories": {"bad": {"path": "../child-file", "required": False}}},
            owner_root=owner,
        )


def test_obvious_direct_parent_child_cycle_is_rejected(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    child = tmp_path / "child"
    owner.mkdir()
    child.mkdir()
    (child / ".devtool.toml").write_text(
        "[project]\nrunner='command'\n"
        "[federation]\nschema=1\n"
        "[federation.repositories.parent]\npath='../owner'\n",
        encoding="utf-8",
    )
    with pytest.raises(FederationConfigError, match="direct federation cycle detected"):
        load_federation_config(
            {"schema": 1, "repositories": {"child": {"path": "../child"}}},
            owner_root=owner,
        )


def test_schema_is_required_and_only_v1_is_accepted(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(FederationConfigError, match="schema must be integer 1"):
        load_federation_config({"repositories": {"child": {"path": "child"}}}, owner_root=tmp_path)
    with pytest.raises(FederationConfigError, match="Unsupported .*schema=2"):
        load_federation_config({"schema": 2, "repositories": {"child": {"path": "child"}}}, owner_root=tmp_path)
    with pytest.raises(FederationConfigError, match="schema must be integer 1"):
        load_federation_config({"schema": True}, owner_root=tmp_path)


def test_repository_and_group_names_are_strict_and_namespaces_do_not_collide(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(FederationConfigError, match="Invalid federation repository name"):
        load_federation_config(
            {"schema": 1, "repositories": {"bad/name": {"path": "child"}}}, owner_root=tmp_path
        )
    with pytest.raises(FederationConfigError, match="conflicts with a repository of the same name"):
        load_federation_config(
            {
                "schema": 1,
                "repositories": {"child": {"path": "child"}},
                "groups": {"child": {"repositories": ["child"]}},
            },
            owner_root=tmp_path,
        )


def test_groups_reject_unknown_duplicate_or_empty_members(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    base = {"schema": 1, "repositories": {"child": {"path": "child"}}}
    with pytest.raises(FederationConfigError, match="unknown repository"):
        load_federation_config(
            {**base, "groups": {"product": {"repositories": ["child", "missing"]}}}, owner_root=tmp_path
        )
    with pytest.raises(FederationConfigError, match="duplicate repository 'child'"):
        load_federation_config(
            {**base, "groups": {"product": {"repositories": ["child", "child"]}}}, owner_root=tmp_path
        )
    with pytest.raises(FederationConfigError, match="cannot be empty"):
        load_federation_config(
            {**base, "groups": {"product": {"repositories": []}}}, owner_root=tmp_path
        )


def test_unknown_fields_and_bad_types_fail_closed(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    cases = [
        ({"schema": 1, "mystery": True}, "unknown field"),
        ({"schema": 1, "repositories": []}, "repositories.*TOML table"),
        ({"schema": 1, "repositories": {"child": {"path": "child", "extra": 1}}}, "unknown field"),
        ({"schema": 1, "repositories": {"child": {"path": "child", "required": "yes"}}}, "true or false"),
        ({"schema": 1, "groups": {"x": {"repositories": "child"}}}, "TOML array"),
    ]
    for section, pattern in cases:
        with pytest.raises(FederationConfigError, match=pattern):
            load_federation_config(section, owner_root=tmp_path)


def test_machine_serialization_is_deterministic_and_explicit(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    zeta = tmp_path / "zeta"
    alpha = tmp_path / "alpha"
    owner.mkdir()
    zeta.mkdir()
    alpha.mkdir()
    config = load_federation_config(
        {
            "schema": 1,
            "repositories": {
                "zeta": {"path": "../zeta"},
                "alpha": {"path": "../alpha"},
            },
            "groups": {"product": {"repositories": ["zeta", "alpha"]}},
        },
        owner_root=owner,
    )
    payload = federation_as_dict(config)
    assert list(payload["repositories"]) == ["alpha", "zeta"]
    assert payload["groups"]["product"]["repositories"] == ["zeta", "alpha"]
    assert payload["repositories"]["alpha"]["root"] == str(alpha.resolve())


def test_federation_is_repository_scoped_and_global_config_cannot_inject_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".devtool.toml").write_text(_base_project(), encoding="utf-8")
    global_config = tmp_path / "global.toml"
    global_config.write_text(
        "[federation]\nschema = 1\n[federation.repositories.evil]\npath = '/tmp'\n",
        encoding="utf-8",
    )
    assert dict(global_config_doctor_entries({"federation": {"schema": 1}}))["federation"] == (
        "repo-scoped; keep this in .devtool.toml"
    )
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(global_config)}, clear=False):
        config = load_project_config(str(root), None, allow_ambiguous_target=True)
    assert config.federation.enabled is False


def test_resolve_config_exposes_normalized_federation_contract(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    (root / ".devtool.toml").write_text(
        _base_project()
        + """
[federation]
schema = 1

[federation.repositories.child]
path = "../child"
required = true

[federation.groups.product]
repositories = ["child"]
""",
        encoding="utf-8",
    )
    config = _load(root)
    payload = resolve_config_dict(config)["federation"]
    assert payload["enabled"] is True
    assert payload["repositories"]["child"]["root"] == str(child.resolve())
    assert payload["groups"]["product"]["repositories"] == ["child"]


def test_nested_git_repo_is_not_implicitly_enrolled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "vendor" / "nested"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    (root / ".devtool.toml").write_text(_base_project(), encoding="utf-8")
    config = _load(root)
    assert config.federation.repositories == {}
