from __future__ import annotations

from pathlib import Path

from devtool.cli import _apply_transaction_path
from devtool.models import OperationResult


def test_closed_journal_cannot_be_selected_as_active_rollback(tmp_path: Path) -> None:
    path = tmp_path / "closed" / "transaction.json"
    result = OperationResult("apply-overlay", ["apply-overlay"], 0, 0.01, "", [],
                             metadata={"transaction_path": str(path)})
    assert _apply_transaction_path([result]) == ""


def test_closed_rolled_back_journal_cannot_be_reopened(tmp_path: Path) -> None:
    path = tmp_path / "closed" / "transaction.json"
    result = OperationResult("apply-overlay", ["apply-overlay"], 0, 0.01, "", [],
                             metadata={"transaction_path": str(path),
                                       "rolled_back_during_apply": True, "rollback_returncode": 0})
    assert _apply_transaction_path([result]) == ""
