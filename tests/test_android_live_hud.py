from __future__ import annotations

import os
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from devtool.android_live_hud import (
    DEBUG_PACKAGE,
    PACKAGE,
    PROTOCOL_VERSION,
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
    assert f"{DEBUG_PACKAGE}.UPDATE" in argv
    assert f"{PACKAGE}.UPDATE" not in argv
    protocol_index = argv.index("protocol_version")
    assert argv[protocol_index - 1] == "--ei"
    assert argv[protocol_index + 1] == str(PROTOCOL_VERSION)
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
    argv = run.call_args.args[0]
    assert f"{PACKAGE}/{RECEIVER_CLASS}" in argv
    assert f"{PACKAGE}.UPDATE" in argv


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
    argv = run.call_args.args[0]
    assert component in argv
    assert "example.variant.UPDATE" in argv


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
    argv = run.call_args.args[0]
    assert component in argv
    assert f"{PACKAGE}.UPDATE" in argv


def test_bridge_falls_back_to_installed_release_when_debug_is_absent(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", side_effect=lambda package=None: package == PACKAGE
        ), patch("devtool.android_live_hud.subprocess.run", return_value=CompletedProcess([], 0, "Broadcast completed")) as run:
            assert bridge.send("update", {"run_id": "release-fallback"})
    argv = run.call_args.args[0]
    assert f"{PACKAGE}/{RECEIVER_CLASS}" in argv
    assert f"{PACKAGE}.UPDATE" in argv



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


def test_bridge_errors_are_logged_but_not_printed(tmp_path: Path, capsys) -> None:
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=tmp_path / "missing-token")
        bridge._set_error(
            "Devtool Live HUD companion package was not found. Checked "
            "io.github.mikeceead.devtoolhud.debug, io.github.mikeceead.devtoolhud."
        )
    assert capsys.readouterr().err == ""
    log = (tmp_path / "bridge-error.log").read_text(encoding="utf-8")
    assert "Devtool Android HUD bridge:" in log
    assert "io.github.mikeceead.devtoolhud.debug" in log


def test_bridge_action_prefix_override_remains_authoritative(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    env = {
        **_isolated_bridge_environment(tmp_path),
        "DEVTOOL_ANDROID_HUD_COMPONENT": f"{DEBUG_PACKAGE}/{RECEIVER_CLASS}",
        "DEVTOOL_ANDROID_HUD_ACTION_PREFIX": "custom.bridge.namespace",
    }
    with patch.dict(os.environ, env, clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch(
            "devtool.android_live_hud.subprocess.run",
            return_value=CompletedProcess([], 0, 'Broadcast completed: result=100, data="accepted"'),
        ) as run:
            assert bridge.send("update", {"run_id": "override-prefix"})
    assert "custom.bridge.namespace.UPDATE" in run.call_args.args[0]


def test_bridge_rejects_unknown_action_before_broadcast(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch(
            "devtool.android_live_hud.subprocess.run"
        ) as run:
            assert not bridge.send("explode", {"run_id": "bad-action"})
    run.assert_not_called()
    assert "unsupported android hud action" in bridge.last_error.lower()


def test_bridge_accepts_current_companion_acknowledgement() -> None:
    ok, detail = AndroidLiveHudBridge._broadcast_outcome(
        CompletedProcess([], 0, 'Broadcast completed: result=100, data="accepted"')
    )
    assert ok
    assert detail == "accepted"


def test_bridge_treats_stale_acknowledgement_as_safe_delivery() -> None:
    ok, detail = AndroidLiveHudBridge._broadcast_outcome(
        CompletedProcess([], 0, 'Broadcast completed: result=110, data="stale-update"')
    )
    assert ok
    assert detail == "stale-update"


def test_bridge_surfaces_receiver_rejection() -> None:
    ok, detail = AndroidLiveHudBridge._broadcast_outcome(
        CompletedProcess([], 0, 'Broadcast completed: result=202, data="unknown-action"')
    )
    assert not ok
    assert detail == "companion rejected broadcast: unknown-action"


def test_bridge_keeps_legacy_companion_result_compatible() -> None:
    ok, detail = AndroidLiveHudBridge._broadcast_outcome(
        CompletedProcess([], 0, "Broadcast completed: result=0")
    )
    assert ok
    assert detail == ""


def test_bridge_helper_uses_pairing_markers_and_dynamic_action_prefix() -> None:
    helper = (
        Path(__file__).resolve().parents[1]
        / "android"
        / "devtool-live-hud"
        / "tools"
        / "devtool-live-hud"
    ).read_text(encoding="utf-8")
    assert "android-live-hud.package" in helper
    assert "android-live-hud.component" in helper
    assert 'ACTION_PREFIX="${DEVTOOL_ANDROID_HUD_ACTION_PREFIX:-}"' in helper
    assert '[[ -n "$ACTION_PREFIX" ]] || ACTION_PREFIX="$PACKAGE"' in helper
    assert 'COMPONENT="$PACKAGE/$RECEIVER_CLASS"' in helper
    assert '--ei protocol_version 1' in helper
    assert 'result=(201|202|203|204)' in helper
    assert '\nPACKAGE="io.github.mikeceead.devtoolhud"\n' not in helper


def test_android_receiver_and_manifest_share_variant_aware_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    java = root / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
    receiver = (java / "BridgeReceiver.java").read_text(encoding="utf-8")
    protocol = (java / "BridgeProtocol.java").read_text(encoding="utf-8")
    manifest = (
        root / "android" / "devtool-live-hud" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    wrapper = (java / "TermuxWrapperSentinel.java").read_text(encoding="utf-8")
    roundtrip = (java / "BridgeRoundtripProbe.java").read_text(encoding="utf-8")

    assert "BridgeProtocol.isExpectedAction(context.getPackageName(), action)" in receiver
    assert "RESULT_ACTION_REJECTED" in receiver
    assert "RESULT_PROTOCOL_REJECTED" in receiver
    assert "setResultCode(resultCode)" in receiver
    assert "setResultData(resultData)" in receiver
    assert '"UPDATE", "COMPLETE", "FAIL", "CANCEL", "TOAST"' in protocol
    for action in ("UPDATE", "COMPLETE", "FAIL", "CANCEL", "TOAST"):
        assert f'android:name="${{applicationId}}.{action}"' in manifest
    assert "--ei protocol_version" in wrapper
    assert "--ei protocol_version" in roundtrip


def test_bridge_protocol_has_executable_local_unit_tests() -> None:
    test_source = (
        Path(__file__).resolve().parents[1]
        / "android"
        / "devtool-live-hud"
        / "app"
        / "src"
        / "test"
        / "java"
        / "io"
        / "github"
        / "mikeceead"
        / "devtoolhud"
        / "BridgeProtocolTest.java"
    ).read_text(encoding="utf-8")
    assert "debugBuildUsesDebugActionNamespace" in test_source
    assert "releaseBuildUsesReleaseActionNamespace" in test_source
    assert "protocolResultCodesStayStable" in test_source


def test_bridge_helper_sends_debug_component_and_debug_action_from_markers(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    helper = root / "android" / "devtool-live-hud" / "tools" / "devtool-live-hud"
    config = tmp_path / ".config" / "devtool"
    config.mkdir(parents=True)
    (config / "android-live-hud.token").write_text("secret-token\n", encoding="utf-8")
    component = f"{DEBUG_PACKAGE}/{RECEIVER_CLASS}"
    (config / "android-live-hud.package").write_text(DEBUG_PACKAGE + "\n", encoding="utf-8")
    (config / "android-live-hud.component").write_text(component + "\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "am-argv"
    fake_am = bin_dir / "am"
    fake_am.write_text(
        "#!/bin/sh\nprintf '%s\n' \"$@\" > \"$DEVTOOL_TEST_AM_ARGV\"\n"
        "printf 'Broadcast completed: result=100, data=\"accepted\"\n'\n",
        encoding="utf-8",
    )
    fake_am.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "DEVTOOL_TEST_AM_ARGV": str(argv_file),
    }
    result = subprocess.run(
        ["bash", str(helper), "update", "--run-id", "debug-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert component in argv
    assert f"{DEBUG_PACKAGE}.UPDATE" in argv
    assert f"{PACKAGE}.UPDATE" not in argv
    version_index = argv.index("protocol_version")
    assert argv[version_index - 1] == "--ei"
    assert argv[version_index + 1] == "1"


def test_bridge_rejects_unknown_nonzero_companion_result() -> None:
    ok, detail = AndroidLiveHudBridge._broadcast_outcome(
        CompletedProcess([], 0, 'Broadcast completed: result=999, data="future-code"')
    )
    assert not ok
    assert detail == "companion returned unknown result code 999"


def test_bridge_send_does_not_report_receiver_rejection_as_success(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    with patch.dict(os.environ, _isolated_bridge_environment(tmp_path), clear=False):
        bridge = AndroidLiveHudBridge(token_file=token_file)
        with patch.object(AndroidLiveHudBridge, "_am_binary", return_value="am"), patch.object(
            AndroidLiveHudBridge, "_package_installed", return_value=True
        ), patch(
            "devtool.android_live_hud.subprocess.run",
            return_value=CompletedProcess(
                [], 0, 'Broadcast completed: result=202, data="unknown-action"'
            ),
        ):
            assert not bridge.send("update", {"run_id": "rejected"})
    assert "companion rejected broadcast: unknown-action" in bridge.last_error
