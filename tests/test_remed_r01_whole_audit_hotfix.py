from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devtool.android.signing import AndroidSigningError, signing_profile
from devtool.artifacts import write_archive_entries
from devtool.cli import main
from devtool.core_runtime import private_run_root
from devtool.invocation_origin import (
    create_federation_lineage_handoff,
    federation_lineage_from_environment,
)
from devtool.models import ProjectConfig
from devtool.run_store import RunCatalog
from devtool.runners import apply_overlay


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text('[project]\nkind="python"\n', encoding="utf-8")
    return root


def _overlay(path: Path, *, repo_name: str, validation_required: bool = False) -> Path:
    payload = b"rem01-hotfix\n"
    manifest: dict[str, object] = {
        "schema_version": 2,
        "type": "overlay",
        "name": "rem01-hotfix-fixture",
        "assurance": "qualified",
        "repository": {"name": repo_name},
        "apply": {"atomic": True},
        "inventory": {
            "format": 1,
            "entries": [{"path": "rem01.txt", "action": "add", "sha256": _sha(payload), "size": len(payload)}],
        },
        "validation": {"required": validation_required},
    }
    write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), "rem01.txt": payload})
    return path


def test_rem01_default_zsh_completion_uses_canonical_generated_surface(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["completion", "zsh"]) == 0
    text = capsys.readouterr().out
    assert "# Generated from the CORE-X04 command registry." in text
    assert "Legacy desktop page order marker" not in text
    assert "apply-overlay" not in text
    assert "apply" in text


def test_rem01_android_devtool_signing_rejects_legacy_identity_synthesis(tmp_path: Path) -> None:
    config = ProjectConfig(
        repo_root=tmp_path,
        kind="android",
        runner="android",
        project_root=str(tmp_path),
        variant="release",
        runner_options={
            "android": {
                "signing": {
                    "release": {
                        "mode": "devtool",
                        "keystore": "/legacy/release.p12",
                        "alias": "legacy",
                        "expected_cert_sha256": "a" * 64,
                    }
                }
            }
        },
    )
    with pytest.raises(AndroidSigningError, match="requires a registered LIFE identity_id"):
        signing_profile(config, "release")


def test_rem01_ambient_federation_environment_is_not_lineage_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_FEDERATION_PARENT_RUN_ID", "forged-parent")
    monkeypatch.setenv("DEVTOOL_FEDERATION_REPOSITORY", "forged-repo")
    monkeypatch.setenv("DEVTOOL_RUN_ID", "forged-child")
    assert federation_lineage_from_environment() == {}


def test_rem01_federation_lineage_requires_private_parent_handoff(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    code = (
        "import json, os; "
        "from devtool.invocation_origin import federation_lineage_from_environment; "
        "print(json.dumps(federation_lineage_from_environment(), sort_keys=True))"
    )
    env = dict(os.environ)
    env.update({
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent-run",
        "DEVTOOL_FEDERATION_REPOSITORY": "repo-a",
        "DEVTOOL_RUN_ID": "child-run",
        "DEVTOOL_FEDERATION_LINEAGE_HANDOFF": create_federation_lineage_handoff({
            "kind": "federation",
            "parent_run_id": "parent-run",
            "repository": "repo-a",
            "child_run_id": "child-run",
        }),
    })
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["parent_run_id"] == "parent-run"
    assert payload["repository"] == "repo-a"
    assert payload["child_run_id"] == "child-run"


def test_rem01_artifact_execution_record_is_pending_until_validation_finalization(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    artifact = _overlay(tmp_path / "overlay.zip", repo_name=repo.name, validation_required=False)
    result = apply_overlay(repo, str(artifact), yes=True)
    assert result.returncode == 0, result.output
    assert result.metadata["artifact_execution_status"] == "applied-pending-validation"
    assert str(result.metadata["artifact_terminal_tree_identity"]).startswith(("git-tree:", "tree-sha256:"))
    run_id = result.metadata["run_id"]
    record_id = result.metadata["artifact_execution_record_id"]
    record_path = private_run_root(repo) / run_id / "artifacts" / (record_id.replace(":", "-") + ".json")
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["execution_status"] == "applied-pending-validation"
    assert payload["final_state_identity"]
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    assert catalog.runs[run_id]["artifact_execution_status"] == "applied-pending-validation"
    assert catalog.runs[run_id]["status"] == "applied-pending-validation"
