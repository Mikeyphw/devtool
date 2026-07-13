from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from devtool.android_live_hud import AndroidLiveHudBridge


def test_bridge_sends_explicit_token_protected_broadcast(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    bridge = AndroidLiveHudBridge(token_file=token_file)

    with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
        AndroidLiveHudBridge, "_package_installed", return_value=True
    ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
        assert bridge.available()
        assert bridge.send("update", {"run_id": "abc", "progress": 72, "indeterminate": False})

    argv = run.call_args.args[0]
    assert argv[:2] == ["am", "broadcast"]
    assert "io.github.mikeceead.devtoolhud/.BridgeReceiver" in argv
    assert "secret-token" in argv
    assert "--ei" in argv
    assert "--ez" in argv


def test_bridge_requires_token(tmp_path: Path) -> None:
    bridge = AndroidLiveHudBridge(token_file=tmp_path / "missing")
    with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
        AndroidLiveHudBridge, "_package_installed", return_value=True
    ):
        assert not bridge.available()


def test_bridge_sends_sequence_as_long_extra(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    bridge = AndroidLiveHudBridge(token_file=token_file)
    with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch(
        "devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")
    ) as run:
        assert bridge.send("update", {"run_id": "abc", "sequence": 1234567890123})
    argv = run.call_args.args[0]
    sequence_index = argv.index("sequence")
    assert argv[sequence_index - 1] == "--el"
