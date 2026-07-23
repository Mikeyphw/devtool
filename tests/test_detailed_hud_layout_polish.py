from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_HUD = ROOT / "desktop/devtool-control-center/qml/CompactHud.qml"
ANDROID_HUD = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud/OverlayHudService.java"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_hud_keeps_detail_but_has_clear_scan_regions() -> None:
    qml = read(DESKTOP_HUD)
    assert "function stateGlyph()" in qml
    assert 'text: "Current run"' in qml
    assert 'text: stateGlyph() + " " + statusLead()' in qml
    assert 'text: "Workspace lanes"' in qml
    assert 'text: scopedLanes().length + " visible"' in qml
    assert 'text: "Diagnostics parser"' in qml
    assert "opacity: active || expanded ? 1.0 : 0.74" in qml
    # Detail-preservation guard: the polish must not remove rich HUD material.
    for token in (
        "sharedVisibleSteps()",
        "workspaceColumn",
        "laneMetrics(modelData)",
        "aggregateParserMetrics()",
        "parserExpanded",
        "runnerLabel()",
        "executionLabel()",
        "statusDetail()",
    ):
        assert token in qml


def test_android_overlay_hud_persists_and_clamps_position() -> None:
    source = read(ANDROID_HUD)
    for token in (
        'private static final String KEY_X = "x"',
        'private static final String KEY_Y = "y"',
        "onConfigurationChanged(Configuration newConfig)",
        "clampAndUpdateOverlayPosition()",
        "saveHudPosition()",
        "maxOverlayX(int width)",
        "maxOverlayY(int height)",
        "clampInt(int value, int low, int high)",
        "cardsContainer.post(this::clampAndUpdateOverlayPosition)",
        "overlayRoot.post(this::clampAndUpdateOverlayPosition)",
    ):
        assert token in source
    assert "windowParams.x = dp(12)" not in source
    assert "windowParams.y = dp(72)" not in source


def test_android_overlay_hud_keeps_detailed_content_with_better_hierarchy() -> None:
    source = read(ANDROID_HUD)
    for token in (
        "card(HudUpdate update, boolean primaryRun)",
        'primaryRun ? "Current run" : "Recent run"',
        "statusGlyph(update)",
        "update.notificationStatusLead()",
        "update.notificationStatusDetail()",
        'label("Pipeline"',
        'label("Metrics"',
        "card.setAlpha(primaryRun ? 1.0f : 0.86f)",
        "update.runnerLabel() + \" · \" + update.environmentLabel()",
    ):
        assert token in source
    # Rich details remain: per-step pipeline, metrics, tests, diagnostics and commit.
    for token in ("for (HudUpdate.Step step : update.steps)", "passed", "failed", "warnings", "errors", "Commit "):
        assert token in source
