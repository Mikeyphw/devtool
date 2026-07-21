from __future__ import annotations

import os
from pathlib import Path

import pytest


HUD_TEST_FILES = {
    "test_android_live_hud.py",
    "test_hud.py",
    "test_qt_control_center.py",
    "test_tasker.py",
}


@pytest.fixture(autouse=True)
def isolate_devtool_runtime_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """Keep ordinary tests from reusing a developer's live HUD/log state."""

    for name in (
        "DEVTOOL_ANDROID_HUD_STATE_DIR",
        "DEVTOOL_HUD_BRIDGE_DIR",
        "DEVTOOL_TASKER_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEVTOOL_PHONE_LOG_MIRROR_DIR", str(tmp_path / "phone-log-mirror"))

    path = Path(str(request.node.fspath))
    if path.name not in HUD_TEST_FILES:
        monkeypatch.setenv("DEVTOOL_NO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_NO_AUTO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_HUD_SINGLETON", "off")
