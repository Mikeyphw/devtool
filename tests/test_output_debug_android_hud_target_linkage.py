from pathlib import Path


def test_android_hud_output_debug_linkage_report_is_present() -> None:
    report = Path("android/devtool-live-hud/OUTPUT_DEBUG_EVIDENCE_LINKAGE_TARGET_REPORT.md")
    text = report.read_text(encoding="utf-8")
    assert "output-debug" in text
    assert "evidence/<run_id>/logs/" in text
    assert "shared Devtool core code" in text


def test_android_hud_target_still_declares_seal_tasks() -> None:
    config = Path(".devtool.toml").read_text(encoding="utf-8")
    assert "[targets.android_hud.tasks]" in config
    assert "seal = [" in config
    assert ":app:testDebugUnitTest" in config
    assert ":app:lintDebug" in config
