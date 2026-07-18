from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from devtool.android_live_hud import (
    DEBUG_PACKAGE,
    PACKAGE,
    RECEIVER_CLASS,
    AndroidLiveHudBridge,
)


def _isolated_bridge_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "DEVTOOL_ANDROID_HUD_PACKAGE_FILE": str(tmp_path / "package-marker"),
        "DEVTOOL_ANDROID_HUD_COMPONENT_FILE": str(tmp_path / "component-marker"),
        "DEVTOOL_ANDROID_HUD_DIAGNOSTIC_FILE": str(tmp_path / "bridge-error.log"),
    }


def test_bridge_sends_explicit_token_protected_broadcast(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", return_value=True
        ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
            assert bridge.available()
            assert bridge.send("update", {"run_id": "abc", "progress": 72, "indeterminate": False})

    argv = run.call_args.args[0]
    assert argv[:2] == ["am", "broadcast"]
    assert "-f" in argv and "0x20" in argv
    assert f"{DEBUG_PACKAGE}/{RECEIVER_CLASS}" in argv
    assert f"{PACKAGE}.UPDATE" in argv
    assert "secret-token" in argv
    assert "--ei" in argv
    assert "--ez" in argv


def test_bridge_release_package_uses_fully_qualified_receiver(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    env = {
        **_isolated_bridge_environment(tmp_path),
        "DEVTOOL_ANDROID_HUD_PACKAGE": PACKAGE,
    }
    with patch.dict(os.environ, env, clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", return_value=True
        ), patch(
            "devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")
        ) as run:
            assert bridge.send("update", {"run_id": "release"})
    assert f"{PACKAGE}/{RECEIVER_CLASS}" in run.call_args.args[0]


def test_bridge_honors_explicit_component_override(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    component = f"example.variant/{RECEIVER_CLASS}"
    env = {
        **_isolated_bridge_environment(tmp_path),
        "DEVTOOL_ANDROID_HUD_COMPONENT": component,
    }
    with patch.dict(os.environ, env, clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch(
            "devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")
        ) as run:
            assert bridge.send("update", {"run_id": "override"})
    assert component in run.call_args.args[0]


def test_bridge_prefers_pairing_component_marker(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    component_file = tmp_path / "component-marker"
    component = f"{PACKAGE}/{RECEIVER_CLASS}"
    component_file.write_text(component + "\n", encoding="utf-8")
    env = {
        **_isolated_bridge_environment(tmp_path),
        "DEVTOOL_ANDROID_HUD_COMPONENT_FILE": str(component_file),
    }
    with patch.dict(os.environ, env, clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", side_effect=lambda package=None: package == PACKAGE
        ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
            assert bridge.send("update", {"run_id": "marker"})
    assert component in run.call_args.args[0]


def test_bridge_falls_back_to_installed_release_when_debug_is_absent(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", side_effect=lambda package=None: package == PACKAGE
        ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
            assert bridge.send("update", {"run_id": "release-fallback"})
    assert f"{PACKAGE}/{RECEIVER_CLASS}" in run.call_args.args[0]



def test_package_discovery_includes_root_fallback_when_su_exists(tmp_path: Path) -> None:
    env = {
        **_isolated_bridge_environment(tmp_path),
        "PATH": str(tmp_path),
    }
    su = tmp_path / "su"
    su.write_text("#!/bin/sh\n", encoding="utf-8")
    su.chmod(0o755)
    with patch.dict(os.environ, env, clear=False), patch(
        "devtool.android_live_hud.shutil.which",
        side_effect=lambda name: str(su) if name == "su" else None,
    ):
        commands = AndroidLiveHudBridge._package_commands(DEBUG_PACKAGE)
    assert [str(su), "-c", f"/system/bin/pm path {DEBUG_PACKAGE}"] in commands
    assert [str(su), "-c", f"/system/bin/cmd package path {DEBUG_PACKAGE}"] in commands


def test_package_discovery_can_disable_root_fallback(tmp_path: Path) -> None:
    env = {
        **_isolated_bridge_environment(tmp_path),
        "DEVTOOL_ANDROID_HUD_NO_ROOT_DISCOVERY": "1",
    }
    with patch.dict(os.environ, env, clear=False), patch(
        "devtool.android_live_hud.shutil.which", return_value="/system/bin/su"
    ):
        commands = AndroidLiveHudBridge._package_commands(DEBUG_PACKAGE)
    assert not any(command[:2] == ["/system/bin/su", "-c"] for command in commands)

def test_bridge_requires_token(tmp_path: Path) -> None:
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=tmp_path / "missing")
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", return_value=True
        ):
            assert not bridge.available()
            assert "token is missing" in bridge.last_error.lower()
    assert (tmp_path / "bridge-error.log").is_file()


def test_bridge_sends_sequence_as_long_extra(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", return_value=True
        ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
            assert bridge.send("update", {"run_id": "abc", "sequence": 1234567890123})
    argv = run.call_args.args[0]
    sequence_index = argv.index("sequence")
    assert argv[sequence_index - 1] == "--el"
