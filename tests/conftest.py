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

    # Tests must never inherit the developer's live Termux/chroot session,
    # global config, clipboard bridge, or active HUD run identity. Individual
    # environment-specific tests opt back in explicitly with patch.dict.
    for name in (
        "TERMUX_VERSION",
        "ANDROID_ROOT",
        "ANDROID_DATA",
        "DEVTOOL_EXECUTION_CONTEXT",
        "DEVTOOL_INSIDE_TERMUX_CHROOT",
        "DEVTOOL_CHROOT_CONTEXT",
        "DEVTOOL_AUTO_CHROOT_ACTIVE",
        "DEVTOOL_RUN_ID",
        "DEVTOOL_ANDROID_HUD_STATE_DIR",
        "DEVTOOL_HUD_BRIDGE_DIR",
        "DEVTOOL_TASKER_DIR",
        "DEVTOOL_CLIPBOARD_SOCKET",
        "DEVTOOL_CLIPBOARD_FIFO",
        "DEVTOOL_PHONE_LOG_MIRROR_DIR",
        "DEVTOOL_GLOBAL_CONFIG",
        "DEVTOOL_TERMUX_CHROOT",
        "DEVTOOL_TERMUX_HOME",
        "DEVTOOL_BUILD_ENVIRONMENT",
        "DEVTOOL_BUILD_CACHE_ROOT",
        "DEVTOOL_MAX_WORKERS",
        "DEVTOOL_VALIDATION_MAX_WORKERS",
        "DEVTOOL_VALIDATION_CPU_LIMIT",
        "DEVTOOL_VALIDATION_MEMORY_GUARD_MB",
        "DEVTOOL_VALIDATION_RESOURCE_PROFILE",
        "DEVTOOL_ANDROID_SAFE_VALIDATION",
    ):
        monkeypatch.delenv(name, raising=False)

    for name in tuple(os.environ):
        if name.startswith(("DEVTOOL_PROJECT_", "DEVTOOL_ENV_")):
            monkeypatch.delenv(name, raising=False)

    test_home = tmp_path / "home"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("PREFIX", "/usr")

    path = Path(str(request.node.fspath))
    if path.name not in HUD_TEST_FILES:
        monkeypatch.setenv("DEVTOOL_NO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_NO_AUTO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_HUD_SINGLETON", "off")
