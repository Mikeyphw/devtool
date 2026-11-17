"""Regression: ownership diagnostics use the canonical configuration authority.

The diagnostics may describe ignored historical metadata, but may not adopt
that metadata as executable target ownership or reparse TOML independently.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from devtool.affected_ownership_diagnostics import diagnose


def _config(repo: Path, text: str) -> Path:
    repo.mkdir()
    path = repo / ".devtool.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_diagnostics_report_ignored_metadata_without_changing_authority(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    path = _config(repo, '''
[project]
runner = "python"
[targets.phone]
runner = "python"
root = "."
inputs = ["src/**"]
[targets.phone.metadata]
affected_ownership = "inputs-only"
''')
    before = path.read_bytes()
    result = diagnose(repo)
    assert result["status"] == "NEEDS_MIGRATION"
    assert result["effective_modes"] == {"phone": "root-and-inputs"}
    assert result["legacy_metadata_count"] == 1
    assert {row["code"] for row in result["findings"]} == {
        "IGNORED_METADATA_AFFECTED_OWNERSHIP", "REPOSITORY_ROOT_WILDCARD",
    }
    assert result["mutated"] is False
    assert path.read_bytes() == before


def test_canonical_inputs_only_remains_effective_despite_stale_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _config(repo, '''
[project]
runner = "python"
[targets.phone]
runner = "python"
root = "."
affected_ownership = "inputs-only"
inputs = ["src/**"]
[targets.phone.metadata]
affected_ownership = "root-and-inputs"
''')
    result = diagnose(repo)
    assert result["effective_modes"]["phone"] == "inputs-only"
    assert result["legacy_metadata_count"] == 1
    assert all(row["code"] != "REPOSITORY_ROOT_WILDCARD" for row in result["findings"])


def test_diagnostics_refuse_configuration_outside_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _config(repo, '[project]\nrunner = "python"\n')
    outside = tmp_path / "external.toml"
    outside.write_text('[project]\nrunner = "python"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="inside the repository"):
        diagnose(repo, config_path=outside)


def test_subdirectory_root_is_not_misclassified_as_repository_wildcard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _config(repo, '''
[project]
runner = "python"
[targets.phone]
runner = "python"
root = "src"
inputs = ["src/**"]
''')
    result = diagnose(repo)
    assert result["status"] == "PASS"
    assert result["effective_modes"] == {"phone": "root-and-inputs"}
    assert all(row["code"] != "REPOSITORY_ROOT_WILDCARD" for row in result["findings"])
