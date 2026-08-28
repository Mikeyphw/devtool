from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from devtool.artifact_registry import (
    find_record,
    load_registry,
    prune_registry,
    register_paths,
    verify_record,
)
from devtool.cli import build_parser
from devtool.doctor import doctor_payload
from devtool.models import ProjectConfig
from devtool.validation_profiles import apply_profile, available_profiles


def _config(repo: Path, *, kind: str = "android") -> ProjectConfig:
    return ProjectConfig(repo_root=repo, kind=kind, runner_options={})


def test_builtin_validation_workflow_profiles_are_available(tmp_path: Path) -> None:
    assert {"minimal", "default", "android-full", "release", "termux"}.issubset(set(available_profiles(tmp_path)))


def test_termux_workflow_profile_applies_real_runner_options(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    args = argparse.Namespace(
        validation_workflow_profile="termux",
        validation_resource_profile="automatic",
        skip_restore=False,
        skip_build=False,
        skip_test=False,
    )
    assert apply_profile(cfg, args) == "termux"
    assert cfg.runner_options["validation"]["android_safe"] is True
    assert cfg.runner_options["validation"]["parallel"] is True
    assert cfg.runner_options["gradle"]["build_cache"] is True
    assert cfg.runner_options["android"]["instrumentation"] == "never"


def test_repo_validation_toml_can_extend_profiles(tmp_path: Path) -> None:
    path = tmp_path / ".devtool" / "validation.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "[profiles.ci]\n"
        "skip_restore = true\n"
        "[profiles.ci.validation]\n"
        "parallel = false\n",
        encoding="utf-8",
    )
    cfg = _config(tmp_path)
    args = argparse.Namespace(
        validation_workflow_profile="ci",
        validation_resource_profile="automatic",
        skip_restore=False,
        skip_build=False,
        skip_test=False,
    )
    apply_profile(cfg, args)
    assert args.skip_restore is True
    assert cfg.runner_options["validation"]["parallel"] is False


def test_profile_is_workflow_only_and_resource_profile_is_separate() -> None:
    parser = build_parser()
    args = parser.parse_args(["validate", "--profile", "standard"])
    assert args.validation_workflow_profile == "standard"
    assert args.validation_resource_profile == "automatic"

    resource_args = parser.parse_args(["validate", "--resource-profile", "standard"])
    assert resource_args.validation_workflow_profile == "default"
    assert resource_args.validation_resource_profile == "standard"




def test_validation_profile_legacy_resource_alias_is_removed() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["validate", "--validation-profile", "standard"])
    assert exc.value.code == 2


def test_default_workflow_profile_is_authoritative() -> None:
    parser = build_parser()
    args = parser.parse_args(["validate"])
    assert args.validation_workflow_profile == "default"
    cfg = _config(Path.cwd())
    assert apply_profile(cfg, args) == "default"
    assert cfg.runner_options["validation"]["workflow_profile"] == "default"


def test_doctor_payload_has_structured_sections(tmp_path: Path) -> None:
    cfg = _config(tmp_path, kind="command")
    payload = doctor_payload(cfg)
    assert payload["schema_version"] == 1
    assert isinstance(payload["healthy"], bool)
    sections = {item["section"] for item in payload["checks"]}
    assert {"runtime", "android", "device", "project"}.issubset(sections)

def test_artifact_registry_register_inspect_verify_and_prune(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    managed = tmp_path / ".devtool" / "build-artifacts"
    managed.mkdir(parents=True)
    artifact = managed / "app-debug.apk"
    artifact.write_bytes(b"artifact-v1")

    registered = register_paths(tmp_path, [artifact])
    assert len(registered) == 1
    record = find_record(tmp_path, registered[0]["id"])
    assert record is not None
    assert record["path"] == ".devtool/build-artifacts/app-debug.apk"
    assert verify_record(tmp_path, record["id"])["valid"] is True

    artifact.write_bytes(b"changed")
    assert verify_record(tmp_path, record["id"])["valid"] is False

    # Seed an old timestamp directly as a fixture. Public registry writes are
    # append-only and intentionally cannot rewrite an existing production event.
    data = load_registry(tmp_path)
    data["items"][0]["registered_at"] = "2000-01-01T00:00:00Z"
    registry_file = tmp_path / ".devtool" / "artifacts" / "registry.json"
    registry_file.write_text(json.dumps(data), encoding="utf-8")
    result = prune_registry(tmp_path, older_than_days=30, delete_files=True)
    assert len(result["removed"]) == 1
    assert not artifact.exists()


def test_artifact_cli_surface_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["artifact", "verify", "abc123", "--json"])
    assert args.command == "artifact"
    assert args.artifact_command == "verify"
    assert args.selector == "abc123"
    assert args.json is True
