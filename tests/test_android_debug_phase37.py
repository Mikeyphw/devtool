from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase37_bridge_roundtrip_probe_has_real_end_to_end_markers() -> None:
    probe = read("BridgeRoundtripProbe.java")
    for symbol in (
        "BridgeRoundtripProbe",
        "OPERATION = \"roundtrip-probe\"",
        "DEVTOOL_BRIDGE_ROUNDTRIP_OK",
        "android-live-hud.token",
        "android-live-hud.package",
        "android-live-hud.component",
        "BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED",
        "TermuxCommandRunner.runBridgeOperation",
        "debugEventsForRun",
        "wrapper_started",
        "callback_received",
    ):
        assert symbol in probe
    assert "__devtool_roundtrip_token" in probe and "DEVTOOL_ANDROID_HUD_TOKEN" in probe
    assert "/system/bin/am broadcast" in probe


def test_phase37_termux_result_routes_probe_before_token_manager() -> None:
    service = read("TermuxResultService.java")
    assert "BridgeRoundtripProbe.OPERATION.equals(bridgeOperation)" in service
    assert "BridgeRoundtripProbe.handleResult" in service
    assert service.index("BridgeRoundtripProbe.OPERATION.equals(bridgeOperation)") < service.index("BridgeTokenManager.handleResult")
    assert "BRIDGE_ROUNDTRIP_RESULT_RECEIVED" in service


def test_phase37_trace_model_has_roundtrip_checkpoints() -> None:
    trace = read("RunCommandTrace.java")
    for checkpoint in (
        "BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED",
        "BRIDGE_ROUNDTRIP_RESULT_RECEIVED",
        "BRIDGE_ROUNDTRIP_FAILED",
    ):
        assert checkpoint in trace
    assert "case BRIDGE_ROUNDTRIP_CALLBACK_RECEIVED" in trace
    assert "case BRIDGE_ROUNDTRIP_RESULT_RECEIVED" in trace


def test_phase37_settings_ui_exposes_bridge_roundtrip_probe() -> None:
    main = read("MainActivity.java")
    assert "BridgeRoundtripProbe.ACTION_UPDATED" in main
    assert "Bridge roundtrip probe" in main
    assert "bridgeRoundtripAction" in main
    assert "BridgeRoundtripProbe.start" in main
    assert "requestTermuxPermission" in main


def test_phase37_support_bundle_includes_roundtrip_state() -> None:
    support = read("SupportBundle.java")
    assert 'root.put("bridge_roundtrip", bridgeRoundtrip(app));' in support
    assert "BridgeRoundtripProbe.supportSummary" in support
    classifier = read("DebugIssueClassifier.java")
    for issue in (
        "bridge_roundtrip_ok",
        "bridge_roundtrip_callback_missing",
        "bridge_roundtrip_marker_missing",
        "bridge_roundtrip_wrapper_missing",
    ):
        assert issue in classifier
