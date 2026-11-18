from __future__ import annotations

import os
from pathlib import Path

from devtool import logstore


def test_pytest_devtool_logs_are_isolated_to_tmp_path(tmp_path: Path) -> None:
    expected_state = tmp_path.parent / f".{tmp_path.name}-devtool-runtime" / "state"
    assert Path(os.environ["XDG_STATE_HOME"]) == expected_state
    path = logstore.new_log_path("nested-devtool-command")
    assert path.parent == expected_state / "devtool" / "logs"
    assert not path.is_relative_to(tmp_path)
    assert "/data/data/com.termux/files/home/.local/state/devtool/logs" not in str(path)
    assert "/sdcard/.logs/devtool" not in str(path)


def test_pytest_xdg_state_home_cannot_leak_user_log_root(tmp_path: Path) -> None:
    expected_state = tmp_path.parent / f".{tmp_path.name}-devtool-runtime" / "state"
    assert Path(os.environ["XDG_STATE_HOME"]) == expected_state
    assert logstore.state_dir() == expected_state / "devtool"
