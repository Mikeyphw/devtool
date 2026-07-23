from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "desktop/devtool-control-center/qml"
ANDROID = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_overview_is_action_first_and_actions_wrap() -> None:
    overview = read(QML / "pages/OverviewPage.qml")
    assert 'text: "Next action"' in overview
    assert 'text: "Snapshot"' in overview
    assert 'text: "Quick actions"' in overview
    assert overview.index('text: "Next action"') < overview.index('Latest run')
    assert overview.index('Latest run') < overview.index('text: "Snapshot"')
    assert overview.index('text: "Snapshot"') < overview.index('text: "Quick actions"')
    assert 'Flow {' in overview
    assert 'GridLayout {' in overview and 'columns: root.availableWidth < 640 ? 1 : root.availableWidth < 980 ? 2 : 4' in overview
    assert 'RowLayout {\n            Layout.leftMargin: theme.pageMargin\n            Layout.rightMargin: theme.pageMargin\n            spacing: 10\n            AppButton { text: "Validate repository"' not in overview


def test_desktop_run_uses_dynamic_stepper_and_progressive_disclosure() -> None:
    run = read(QML / "pages/RunPage.qml")
    assert 'readonly property var allStages: ["Project", "Operation", "Tests", "Review"]' in run
    assert 'readonly property var compactStages: ["Project", "Operation", "Review"]' in run
    assert 'function stageForVisibleIndex(index)' in run
    assert 'function visibleStepCount() { return needsTests() ? 4 : 3 }' in run
    assert 'root.stageForVisibleIndex(index)' in run
    assert 'Show project options' in run
    assert 'Show advanced execution' in run
    assert 'Show advanced output' in run
    assert 'Show exact command' in run
    assert 'Danger zone' in run
    assert 'This workflow skips the Tests step.' in run
    assert 'Step "+(root.currentStage+1)+" of 4' not in run


def test_android_overview_is_action_first() -> None:
    main = read(ANDROID / "MainActivity.java")
    overview = main[main.index('private void buildOverviewPage'):main.index('private void buildRunPage')]
    assert 'ui.sectionTitle("Next action")' in overview
    assert 'ui.sectionTitle("Latest run")' in overview
    assert 'ui.sectionTitle("Readiness")' in overview
    assert overview.index('ui.sectionTitle("Next action")') < overview.index('ui.sectionTitle("Latest run")')
    assert overview.index('ui.sectionTitle("Latest run")') < overview.index('ui.sectionTitle("Readiness")')
    assert 'ui.sectionTitle("Advanced setup")' in overview
    assert 'OverlayHudService.setEnabled(this, true)' in overview


def test_android_run_flow_has_progressive_sections_and_dynamic_step_count() -> None:
    main = read(ANDROID / "MainActivity.java")
    assert 'private LinearLayout progressiveSection' in main
    assert 'runProjectAdvancedSection' in main
    assert 'runOperationAdvancedSection' in main
    assert 'runReviewAdvancedSection' in main
    assert 'runDangerZoneSection' in main
    assert 'Show project options' in main
    assert 'Show advanced execution' in main
    assert 'Show advanced output' in main
    assert 'Show danger zone' in main
    assert 'selectedRunNeedsTests()' in main
    assert 'visibleRunStepCount()' in main
    assert 'Step "+(visibleRunStepIndex()+1)+" of "+visibleRunStepCount()' in main
    assert 'new String[]{"Project","Operation","Tests","Review"}[runStage]' not in main
