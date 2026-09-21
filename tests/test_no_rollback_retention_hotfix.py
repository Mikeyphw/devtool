from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devtool import cli, logstore
from devtool.models import OperationResult
from devtool.transactions import create_apply_transaction, checkpoint_transaction_applied, list_active_transaction_records


def test_no_rollback_retention_closes_transaction_without_restoring_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = repo / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"placeholder")

    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="snapshot",
        preview={"changes": [{"path": "tracked.txt", "kind": "modify"}]},
    )
    tx_path = Path(str(tx["path"]))
    tracked.write_text("after\n", encoding="utf-8")
    checkpoint_transaction_applied(tx_path)
    assert list_active_transaction_records(repo)

    result, raw = cli._run_transaction_keep_changes(
        tx_path,
        reason="validation failed and --no-rollback-on-failure retained changes",
    )

    assert result.returncode == 0
    assert result.name == "apply-retain"
    assert result.failure_kind == "changes_retained"
    assert "abandoned-kept-changes" in raw
    assert tracked.read_text(encoding="utf-8") == "after\n"
    assert list_active_transaction_records(repo) == []
    assert not tx_path.exists()


def test_terminal_log_distinguishes_previous_stale_recovery_from_current_rollback(tmp_path: Path) -> None:
    args = argparse.Namespace(command="apply-overlay", artifact="")
    path = tmp_path / "failed.log"
    logstore.write_log("--- devtool run start ---\nDevtool status: running", path)
    started = datetime.now(timezone.utc)
    finished = started + timedelta(seconds=2)
    results = [
        OperationResult(
            "apply-rollback",
            ["transaction", "rollback", "old"],
            0,
            0.1,
            "Recovered old transaction",
            [],
            failure_kind="stale_apply_rolled_back",
        ),
        OperationResult("test", ["go", "test", "./..."], 1, 1.0, "boom", [], failure_kind="test_failure"),
        OperationResult(
            "apply-retain",
            ["transaction", "keep-changes", "new"],
            0,
            0.1,
            "kept",
            [],
            failure_kind="changes_retained",
        ),
    ]

    cli._append_run_log_summary(
        path,
        args,
        None,
        {"warnings": 0, "errors": 1, "failed": 1, "skipped": 0},
        "Devtool failed.",
        started_at=started,
        finished_at=finished,
        exit_code=1,
        results=results,
    )
    saved = path.read_text(encoding="utf-8")
    assert "Devtool previous interrupted apply recovery: succeeded" in saved
    assert "Devtool rollback: succeeded" not in saved
    assert "Devtool failure kind: test_failure" in saved
    assert "Devtool changes retained: yes" in saved


def test_no_rollback_help_describes_durable_post_apply_retention() -> None:
    help_text = cli.build_parser().format_help()
    # Top-level help does not expand subcommands, so inspect the apply parser action text.
    parser = cli.build_parser()
    apply_action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    apply_parser = apply_action.choices["apply-overlay"]
    text = apply_parser.format_help()
    compact = " ".join(text.split())
    assert "Keep successfully applied changes" in compact
    assert "atomic extraction/preflight failures still roll back" in compact
