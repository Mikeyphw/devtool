from __future__ import annotations

from pathlib import Path

import pytest

import devtool.android_build as android_build
from devtool.android.tasks import (
    clear_connected_android_device_probe_cache,
    classify_gradle_task,
    filter_unavailable_connected_android_tests,
    gradle_tasks_need_connected_device_probe,
    optimize_gradle_task_list,
    plan_gradle_task_list,
)
from devtool.models import ProjectConfig
from devtool.android_integration import android_builder_arguments_for_tasks

ROOT = Path(__file__).resolve().parents[1]


def _config(root: Path) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind="android",
        runner="android",
        project_root=str(root),
        selected_target="app",
        variant="debug",
        runner_options={"android": {"variant": "debug"}},
    )


def test_gradle_task_classifier_is_shared_for_android_task_policy() -> None:
    assert classify_gradle_task(":app:testDebugUnitTest") == "unit_test"
    assert classify_gradle_task(":app:lintRelease") == "lint"
    assert classify_gradle_task(":app:assembleRelease") == "assemble_release"
    assert classify_gradle_task(":app:bundleRelease") == "bundle_release"
    assert classify_gradle_task(":app:connectedDebugAndroidTest") == "connected_test"
    assert classify_gradle_task(":app:installDebug") == "install"
    assert classify_gradle_task(":app:publishReleaseBundle") == "publish"
    assert classify_gradle_task(":app:check") == "aggregate_check"
    assert classify_gradle_task(":app:connectedCheck") == "connected_aggregate"
    assert classify_gradle_task(":app:customVerification") == "unknown"


def test_gradle_task_planner_prunes_only_component_tasks_implied_by_assemble() -> None:
    plan = plan_gradle_task_list([
        ":app:assembleDebug",
        ":app:compileDebugKotlin",
        ":app:processDebugResources",
        ":app:mergeDebugResources",
        ":app:generateDebugSources",
        ":app:packageDebug",
        ":app:testDebugUnitTest",
        ":app:lintDebug",
        ":app:connectedDebugAndroidTest",
        ":app:assembleDebug",
        "app:assembleDebug",
    ])

    assert list(plan.optimized) == [
        ":app:assembleDebug",
        ":app:testDebugUnitTest",
        ":app:lintDebug",
        ":app:connectedDebugAndroidTest",
    ]
    assert [entry.normalized for entry in plan.removed] == [
        ":app:compileDebugKotlin",
        ":app:processDebugResources",
        ":app:mergeDebugResources",
        ":app:generateDebugSources",
        ":app:packageDebug",
    ]
    assert all(entry.implied_by == ":app:assembleDebug" for entry in plan.removed)
    assert plan.probe_connected_devices is True


def test_gradle_task_planner_keeps_other_project_component_tasks() -> None:
    assert optimize_gradle_task_list([
        ":app:assembleDebug",
        ":feature:compileDebugKotlin",
        ":feature:packageDebug",
    ]) == [
        ":app:assembleDebug",
        ":feature:compileDebugKotlin",
        ":feature:packageDebug",
    ]


def test_plain_android_app_tasks_do_not_require_connected_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_probe(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("ordinary app tasks must not probe adb")

    monkeypatch.setattr(android_build, "connected_android_devices", fail_probe)
    retained, skipped, adb, states = filter_unavailable_connected_android_tests(
        [":app:assembleDebug", ":app:lintDebug", ":app:testDebugUnitTest"],
        sdk_root=None,
        env={"PATH": ""},
    )

    assert retained == [":app:assembleDebug", ":app:lintDebug", ":app:testDebugUnitTest"]
    assert skipped == []
    assert adb == ""
    assert states == {}
    assert gradle_tasks_need_connected_device_probe(retained) is False


def test_connected_device_probe_result_is_cached_per_run(tmp_path: Path) -> None:
    clear_connected_android_device_probe_cache()
    sdk = tmp_path / "sdk"
    adb = sdk / "platform-tools" / "adb"
    marker = tmp_path / "calls.txt"
    adb.parent.mkdir(parents=True)
    adb.write_text(
        "#!/bin/sh\n"
        "printf x >> '" + str(marker) + "'\n"
        "printf 'List of devices attached\\nserial-1 device\\n'\n",
        encoding="utf-8",
    )
    adb.chmod(0o755)

    first = filter_unavailable_connected_android_tests(
        [":app:connectedDebugAndroidTest"],
        sdk_root=sdk,
        env={"PATH": ""},
    )
    calls_after_first = len(marker.read_text(encoding="utf-8"))
    second = filter_unavailable_connected_android_tests(
        [":app:connectedDebugAndroidTest"],
        sdk_root=sdk,
        env={"PATH": ""},
    )

    assert first == second
    assert calls_after_first > 0
    assert len(marker.read_text(encoding="utf-8")) == calls_after_first


def test_probe_cache_can_be_disabled_for_debugging(tmp_path: Path) -> None:
    clear_connected_android_device_probe_cache()
    sdk = tmp_path / "sdk"
    adb = sdk / "platform-tools" / "adb"
    marker = tmp_path / "calls.txt"
    adb.parent.mkdir(parents=True)
    adb.write_text(
        "#!/bin/sh\n"
        "printf x >> '" + str(marker) + "'\n"
        "printf 'List of devices attached\\nserial-1 device\\n'\n",
        encoding="utf-8",
    )
    adb.chmod(0o755)
    env = {"PATH": "", "DEVTOOL_ANDROID_DEVICE_PROBE_CACHE": "0"}

    filter_unavailable_connected_android_tests(
        [":app:connectedDebugAndroidTest"],
        sdk_root=sdk,
        env=env,
    )
    calls_after_first = len(marker.read_text(encoding="utf-8"))
    filter_unavailable_connected_android_tests(
        [":app:connectedDebugAndroidTest"],
        sdk_root=sdk,
        env=env,
    )

    assert calls_after_first > 0
    assert len(marker.read_text(encoding="utf-8")) == calls_after_first * 2


def test_android_builder_arguments_use_optimized_gradle_task_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("devtool.android_integration.stage_gradle_init_script", lambda: tmp_path / "events.gradle")
    argv = android_builder_arguments_for_tasks(
        _config(tmp_path),
        [
            ":app:assembleDebug",
            ":app:compileDebugKotlin",
            ":app:processDebugResources",
            ":app:testDebugUnitTest",
            ":app:lintDebug",
        ],
        scan_artifacts=False,
    )

    task_values = [argv[index + 1] for index, value in enumerate(argv) if value == "--task"]
    assert task_values == [":app:assembleDebug", ":app:testDebugUnitTest", ":app:lintDebug"]


def test_standalone_source_keeps_planner_available_for_build_apk() -> None:
    source = android_build.standalone_source_path().read_text(encoding="utf-8")
    assert "class GradleTaskPlan" in source
    assert "def plan_gradle_task_list" in source
    assert "connected_device_probe_needed" in source
    assert "disable_transitive_connected_tests" in source


def test_plain_check_does_not_probe_but_connected_check_does() -> None:
    assert gradle_tasks_need_connected_device_probe([":app:check"]) is False
    assert gradle_tasks_need_connected_device_probe([":app:connectedCheck"]) is True


def test_android_hud_target_coalesces_normal_validation_and_disables_redundant_resumable_phases() -> None:
    config = (ROOT / ".devtool.toml").read_text(encoding="utf-8")
    assert "[targets.android_hud.validation]" in config
    block = config.split("[targets.android_hud.validation]", 1)[1].split("[targets.devtool.validation]", 1)[0]
    assert "split_gradle = false" in block
    phases = config.split("[targets.android_hud.validation.phases]", 1)[1]
    assert "dependencies = []" in phases
    assert "compile = []" in phases
    assert "resources = []" in phases


def test_android_hud_gradle_verification_tasks_are_incremental_and_aggregated() -> None:
    root_build = (ROOT / "android/devtool-live-hud/build.gradle").read_text(encoding="utf-8")
    app_build = (ROOT / "android/devtool-live-hud/app/build.gradle").read_text(encoding="utf-8")
    assert "stableBaselineStamp" in root_build
    assert "outputs.file(stableBaselineStamp)" in root_build
    assert "devtoolDebugValidation" in root_build
    assert "dependsOn ':app:assembleDebug', ':app:testDebugUnitTest', ':app:lintDebug'" in root_build
    assert "releaseSigningStamp" in app_build
    assert "releaseApkVerificationReport" in app_build
    assert "devtoolReleaseValidation" in app_build
