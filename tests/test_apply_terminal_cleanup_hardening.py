from __future__ import annotations

from tests._rmp_c02_durable_fixture import attach_durable_apply_evidence

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace

import devtool.artifact_cleanup_worker as cleanup_worker
import devtool.cli as cli
from devtool.models import OperationResult


def _human_nested_apply_args() -> argparse.Namespace:
    return argparse.Namespace(
        command="apply",
        normalized_command="apply-overlay",
        quiet=False,
        json=False,
        _devtool_json=False,
        machine="",
        format="",
        stdout_payload="auto",
        copy=True,
    )


def test_nested_apply_copy_keeps_visible_terminal_fallback_eligible() -> None:
    args = _human_nested_apply_args()
    assert cli._effective_apply_command(args) == "apply-overlay"
    assert cli._apply_terminal_stream_allowed(args) is True


def test_terminal_fallback_emits_immediately_to_stderr_console(monkeypatch) -> None:
    seen: list[str] = []

    class FakeConsole:
        def print(self, value: object) -> None:
            seen.append(str(value))

    monkeypatch.setattr(cli, "create_console", lambda **kwargs: FakeConsole())
    args = _human_nested_apply_args()
    args._devtool_apply_terminal_stream = True
    cli._emit_apply_terminal_status(args, "preparing artifact lifecycle")

    assert seen
    assert "preparing artifact lifecycle" in seen[0]


def test_external_apply_source_cleanup_does_not_rebuild_global_ownership(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Devtool Test"], check=True)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    artifact = tmp_path / "Download" / "overlay.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")

    def forbidden(*args, **kwargs):
        raise AssertionError("global ownership rebuild must not run for external consumed apply source")

    monkeypatch.setattr(cleanup_worker, "build_cleanup_ownership_view", forbidden)
    result = cleanup_worker.cleanup_apply_source_artifacts(
        repo,
        [artifact],
        reason="after successful apply",
        run_id="",
    )

    assert result["returncode"] == 0, result["output"]
    assert result["metadata"]["cleanup_mode"] == "bounded-external-apply-source"
    assert not artifact.exists()


def test_cleanup_subprocess_timeout_is_bounded_and_classified(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "Download" / "overlay.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")
    result = OperationResult(
        "apply-overlay",
        ["apply-overlay", str(artifact)],
        0,
        0.01,
        "applied",
        [],
        metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True},
    )

    attach_durable_apply_evidence(repo, result)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args", args[0] if args else []), timeout=0.01)

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    monkeypatch.setenv("DEVTOOL_ARTIFACT_CLEANUP_TIMEOUT_SECONDS", "5")
    cleanup = cli._delete_original_artifact_after_apply([result], repo_root=repo)

    assert cleanup is not None
    assert cleanup.returncode == 1
    assert cleanup.failure_kind == "artifact_cleanup_outcome_unknown"
    assert cleanup.metadata["cleanup_worker_timed_out"] is True
    assert "deadline" in cleanup.output


def test_cleanup_timeout_after_source_deleted_requires_receipt(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "Download" / "overlay.zip"
    artifact.parent.mkdir()
    artifact.write_bytes(b"overlay")
    result = OperationResult(
        "apply-overlay",
        ["apply-overlay", str(artifact)],
        0,
        0.01,
        "applied",
        [],
        metadata={"artifact_path": str(artifact), "delete_artifact_on_success": True},
    )

    attach_durable_apply_evidence(repo, result)
    def timeout_after_delete(*args, **kwargs):
        artifact.unlink()
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args", args[0] if args else []), timeout=0.01)

    monkeypatch.setattr(cli.subprocess, "run", timeout_after_delete)
    cleanup = cli._delete_original_artifact_after_apply([result], repo_root=repo)

    assert cleanup is not None
    assert cleanup.returncode == 1
    assert cleanup.failure_kind == "artifact_cleanup_outcome_unknown"
    assert cleanup.metadata["cleanup_source_removed"] is True
