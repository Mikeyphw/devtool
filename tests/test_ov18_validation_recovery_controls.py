from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from devtool.cli import (
    _apply_artifact_validation_defaults,
    _rehydrate_apply_validation_context,
    _validation_apply_context,
    _validation_session_options,
    _validation_specs_for_args,
    build_parser,
    main,
)
from devtool.models import ProjectConfig
from devtool.transactions import (
    create_apply_transaction,
    mark_transaction_pending_validation,
    read_transaction,
    resolve_active_transaction,
    rollback_transaction,
)


def _gradle_config(root: Path) -> ProjectConfig:
    (root / "settings.gradle.kts").write_text('rootProject.name = "Demo"\ninclude(":app")\n', encoding="utf-8")
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "gradlew").chmod(0o755)
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="gradle",
        selected_target="gradle",
        runner_options={"validation": {"resumable": True}},
    )


def test_manifest_gradle_tasks_remain_exclusive_after_context_rehydrate(tmp_path: Path) -> None:
    config = _gradle_config(tmp_path)
    parser = build_parser()
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "validation": {"required": True, "tasks": [":app:lintDebug"]},
    }
    apply_args = parser.parse_args(["--repo", str(tmp_path), "apply-overlay", "--defer-validate", "artifact.zip"])
    apply_args._devtool_cli_test_task_explicit = False
    _apply_artifact_validation_defaults(config, apply_args, manifest)
    context = _validation_apply_context(apply_args, transaction_path="/tmp/tx.json", manifest=manifest)

    resume_args = parser.parse_args(["--repo", str(tmp_path), "validate", "continue"])
    resume_args._devtool_cli_test_task_explicit = False
    _rehydrate_apply_validation_context(config, resume_args, context)
    specs = _validation_specs_for_args(config, resume_args, parser)

    assert resume_args.test_task == [":app:lintDebug"]
    assert resume_args._devtool_validation_tasks_declared is True
    assert len(specs) == 1
    assert specs[0].metadata["validation_scope"] == "explicit-tasks"
    assert specs[0].metadata["gradle_tasks"] == [":app:lintDebug"]
    assert specs[0].metadata["validation_components"] == ["explicit-tasks"]


def test_validation_session_options_persist_task_contract_provenance() -> None:
    args = argparse.Namespace(
        test_task=[":app:testDebugUnitTest"],
        _devtool_validation_tasks_declared=True,
        _devtool_cli_test_task_explicit=False,
        _devtool_validation_task_source="artifact-manifest",
    )
    options = _validation_session_options(args)
    assert options["test_task"] == [":app:testDebugUnitTest"]
    assert options["validation_tasks_declared"] is True
    assert options["validation_task_source"] == "artifact-manifest"


def test_rehydrate_rejects_changed_deferred_task_contract(tmp_path: Path) -> None:
    config = _gradle_config(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["--repo", str(tmp_path), "validate", "continue"])
    args._devtool_cli_test_task_explicit = False
    context = {
        "validation_tasks_declared": True,
        "validation_task_source": "artifact-manifest",
        "exclusive_validation_tasks": [":app:lintDebug"],
        "manifest": {"validation": {"tasks": [":app:testDebugUnitTest"]}},
    }
    from devtool.validation_sessions import ValidationSessionError
    try:
        _rehydrate_apply_validation_context(config, args, context)
    except ValidationSessionError as exc:
        assert "task contract changed" in str(exc)
    else:
        raise AssertionError("task drift must not fall back to default validation")


def test_failed_transaction_can_be_resolved_and_resumed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "file.txt"
    source.write_text("old\n", encoding="utf-8")
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "new\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
    )
    tx_path = Path(tx["path"])
    source.write_text("new\n", encoding="utf-8")
    with patch("devtool.transactions._copy_path", side_effect=RuntimeError("simulated interruption")):
        failed = rollback_transaction(tx_path, reason="test")
    assert failed.returncode == 1
    assert read_transaction(tx_path)["status"] == "rollback-failed"
    assert resolve_active_transaction(repo, failed_only=True) == tx_path
    resumed = rollback_transaction(tx_path, reason="manual resume")
    assert resumed.returncode == 0
    assert source.read_text(encoding="utf-8") == "old\n"


def test_allow_pending_validation_preserves_transaction_and_runs_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".devtool.toml").write_text(
        '[project]\nkind="command"\nrestore_command="true"\n',
        encoding="utf-8",
    )
    artifact = tmp_path / "overlay.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr("file.txt", "new\n")
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "file.txt"}]},
    )
    mark_transaction_pending_validation(tx["path"], session_id="session-1", next_phase="test")

    with patch("devtool.cli.render_summary"):
        rc = main([
            "--repo", str(repo), "--no-clipboard", "--allow-pending-validation", "restore"
        ])
    assert rc == 0
    assert Path(tx["path"]).exists()
    assert read_transaction(tx["path"])["status"] == "applied-pending-validation"


def test_rollback_parser_exposes_transaction_recovery_controls() -> None:
    args = build_parser().parse_args(["rollback", "--failed"])
    assert args.failed is True
    args = build_parser().parse_args(["rollback", "--transaction", "abc"])
    assert args.transaction == "abc"
