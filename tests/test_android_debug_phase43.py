from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
MANIFEST = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "AndroidManifest.xml"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase43_debug_workbench_health_composes_existing_sensors_without_mutation() -> None:
    health = read("DebugWorkbenchHealth.java")
    for symbol in (
        "DebugWorkbenchHealth",
        "BUNDLE_KEY",
        "Snapshot assess(Context context)",
        "supportSummary(Context context)",
        "plainText(Context context)",
        "BridgeRoundtripProbe.state",
        "BridgeTokenManager.isReady",
        "RootBridgeManager.stateLabel",
        "RepositoryPathResolver.resolve",
        "RepoQueueRecovery.assess",
        "ValidationStore.latest",
        "DevtoolDatabase.get(app).recentDebugEvents",
    ):
        assert symbol in health
    for forbidden in ("git reset", "git clean", "deleteRepository", "rm -rf", "RepositoryWorkspaceStore.retry"):
        assert forbidden not in health


def test_phase43_debug_workbench_activity_surfaces_cockpit_sections_and_safe_actions() -> None:
    activity = read("DebugWorkbenchActivity.java")
    for symbol in (
        "DebugWorkbenchActivity extends Activity",
        "Current health",
        "Probes and entry points",
        "Run Command Trace",
        "Repo Manager Doctor",
        "Validation Workbench health",
        "Recent debug events",
        "Export Debug Bundle",
        "Run bridge roundtrip probe",
        "Copy health report",
        "Copy run trace",
        "Copy recent debug events",
        "Open repository doctor",
        "Open Validation Workbench",
        "SupportBundle.build(this)",
        "DebugWorkbenchHealth.assess(this)",
        "DebugRecorder.ACTION_UPDATED",
    ):
        assert symbol in activity
    for forbidden in ("git reset", "git clean", "rm -rf", "Hard reset", "Discard all"):
        assert forbidden not in activity


def test_phase43_main_settings_and_overview_link_to_workbench() -> None:
    main = read("MainActivity.java")
    assert "openDebugWorkbench()" in main
    assert "DebugWorkbenchActivity.class" in main
    assert main.count("Debug Workbench") >= 2
    assert "Health, run trace, bridge probe, repo doctor, queue recovery and recent events" in main
    assert "Open run trace, bridge and repo diagnostics" in main


def test_phase43_manifest_registers_internal_debug_workbench_activity() -> None:
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert 'android:name=".DebugWorkbenchActivity"' in manifest
    assert 'android:exported="false"' in manifest[manifest.index('android:name=".DebugWorkbenchActivity"'):manifest.index('android:name=".ValidationEvidenceActivity"')]
    assert 'android:label="Debug Workbench"' in manifest


def test_phase43_support_bundle_exports_debug_workbench_summary() -> None:
    support = read("SupportBundle.java")
    assert 'root.put("debug_workbench", DebugWorkbenchHealth.supportSummary(app))' in support
    assert 'root.put("bridge_roundtrip", bridgeRoundtrip(app))' in support
