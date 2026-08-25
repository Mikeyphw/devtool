from __future__ import annotations

from pathlib import Path

import devtool.android_build as android_build


def _fake_adb(
    sdk: Path,
    *device_lines: str,
) -> Path:
    adb = sdk / "platform-tools" / "adb"
    adb.parent.mkdir(parents=True)

    lines = [
        "#!/bin/sh",
        "printf '%s\\n' \\",
        "  'List of devices attached' \\",
    ]

    for index, entry in enumerate(device_lines):
        suffix = " \\" if index < len(device_lines) - 1 else ""
        lines.append(
            f"  '{entry}'{suffix}"
        )

    if not device_lines:
        # Remove continuation from the header when it's the last argument.
        lines[-1] = "  'List of devices attached'"

    adb.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    adb.chmod(0o755)

    return adb


def test_connected_android_test_task_detection() -> None:
    assert android_build.is_connected_android_test_task(
        ":app:connectedDebugAndroidTest"
    )

    assert android_build.is_connected_android_test_task(
        "connectedReleaseAndroidTest"
    )

    assert not android_build.is_connected_android_test_task(
        ":app:assembleDebugAndroidTest"
    )

    assert not android_build.is_connected_android_test_task(
        ":app:compileDebugAndroidTestKotlin"
    )


def test_parse_adb_devices_keeps_states() -> None:
    states = android_build._parse_adb_device_states(
        """List of devices attached
serial-ok device product:test
serial-offline offline
serial-auth unauthorized
"""
    )

    assert states == {
        "serial-ok": "device",
        "serial-offline": "offline",
        "serial-auth": "unauthorized",
    }


def test_connected_tasks_run_when_device_present(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"

    adb = _fake_adb(
        sdk,
        "serial-1 device",
    )

    tasks = [
        ":app:assembleDebug",
        ":app:connectedDebugAndroidTest",
    ]

    retained, skipped, selected, states = (
        android_build.filter_unavailable_connected_android_tests(
            tasks,
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == tasks
    assert skipped == []
    assert selected == str(adb)
    assert states == {
        "serial-1": "device",
    }


def test_explicit_connected_task_is_skipped_without_device(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"
    adb = _fake_adb(sdk)

    retained, skipped, selected, states = (
        android_build.filter_unavailable_connected_android_tests(
            [
                ":app:assembleDebug",
                ":app:connectedDebugAndroidTest",
                ":app:lintDebug",
            ],
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == [
        ":app:assembleDebug",
        ":app:lintDebug",
    ]

    assert skipped == [
        ":app:connectedDebugAndroidTest",
    ]

    assert selected == str(adb)
    assert states == {}


def test_transitive_case_probes_device_even_without_explicit_connected_task(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"
    adb = _fake_adb(sdk)

    # This represents the BKX case:
    #
    # bkxAndroidReleaseSeal
    #   -> connectedDebugAndroidTest
    #
    # The connected task isn't explicit, but Devtool must still know there
    # is no device so it can install the Gradle skip policy.
    tasks = [
        ":app:verifyBkxDeviceQualificationContract",
        ":app:bkxAndroidReleaseSeal",
    ]

    retained, skipped, selected, states = (
        android_build.filter_unavailable_connected_android_tests(
            tasks,
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == tasks
    assert skipped == []
    assert selected == str(adb)
    assert states == {}


def test_transitive_case_detects_device_when_present(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"

    adb = _fake_adb(
        sdk,
        "serial-1 device",
    )

    tasks = [
        ":app:bkxAndroidReleaseSeal",
    ]

    retained, skipped, selected, states = (
        android_build.filter_unavailable_connected_android_tests(
            tasks,
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == tasks
    assert skipped == []
    assert selected == str(adb)
    assert states == {
        "serial-1": "device",
    }


def test_unauthorized_device_is_not_usable(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"

    _fake_adb(
        sdk,
        "serial-1 unauthorized",
    )

    retained, skipped, _, states = (
        android_build.filter_unavailable_connected_android_tests(
            [":app:connectedDebugAndroidTest"],
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == []

    assert skipped == [
        ":app:connectedDebugAndroidTest",
    ]

    assert states == {
        "serial-1": "unauthorized",
    }


def test_offline_device_is_not_usable(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"

    _fake_adb(
        sdk,
        "serial-1 offline",
    )

    retained, skipped, _, states = (
        android_build.filter_unavailable_connected_android_tests(
            [":app:connectedDebugAndroidTest"],
            sdk_root=sdk,
            env={"PATH": ""},
        )
    )

    assert retained == []

    assert skipped == [
        ":app:connectedDebugAndroidTest",
    ]

    assert states == {
        "serial-1": "offline",
    }


def test_no_device_init_script_disables_connected_tasks_lazily(
    tmp_path: Path,
) -> None:
    path = (
        android_build
        ._write_no_device_connected_test_init_script(
            tmp_path
        )
    )

    assert path.is_file()

    text = path.read_text(
        encoding="utf-8"
    )

    assert "gradle.beforeProject" in text
    assert "project.tasks.configureEach" in text
    assert 'startsWith("connected")' in text
    assert 'endsWith("androidtest")' in text
    assert "task.enabled = false" in text


def test_no_device_init_script_is_stable(
    tmp_path: Path,
) -> None:
    first = (
        android_build
        ._write_no_device_connected_test_init_script(
            tmp_path
        )
    )

    before = first.read_text(
        encoding="utf-8"
    )

    second = (
        android_build
        ._write_no_device_connected_test_init_script(
            tmp_path
        )
    )

    assert first == second
    assert second.read_text(
        encoding="utf-8"
    ) == before


def test_dry_run_does_not_probe_or_remove_tasks() -> None:
    tasks = [
        ":app:connectedDebugAndroidTest",
        ":app:bkxAndroidReleaseSeal",
    ]

    retained, skipped, adb, states = (
        android_build.filter_unavailable_connected_android_tests(
            tasks,
            sdk_root=None,
            env={"PATH": ""},
            dry_run=True,
        )
    )

    assert retained == tasks
    assert skipped == []
    assert adb == ""
    assert states == {}


def test_main_source_wires_transitive_policy_into_gradle() -> None:
    source = android_build.standalone_source_path().read_text(
        encoding="utf-8"
    )

    assert (
        "no_usable_connected_device = ("
        in source
    )

    assert (
        "--init-script"
        in source
    )

    assert (
        "_write_no_device_connected_test_init_script"
        in source
    )
