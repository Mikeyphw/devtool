from pathlib import Path
import tempfile

from devtool.android_build import (
    _force_gradle_configuration_cache_off,
)
from devtool.diagnostics import parse_issues


def test_no_device_policy_forces_configuration_cache_off() -> None:
    assert _force_gradle_configuration_cache_off([]) == [
        "--no-configuration-cache"
    ]


def test_no_device_policy_replaces_configuration_cache_on() -> None:
    result = _force_gradle_configuration_cache_off(
        [
            "-Pexample=true",
            "--configuration-cache",
            "--warning-mode=all",
        ]
    )

    assert "--configuration-cache" not in result
    assert result.count("--no-configuration-cache") == 1
    assert "-Pexample=true" in result
    assert "--warning-mode=all" in result


def test_no_device_policy_deduplicates_configuration_cache_off() -> None:
    result = _force_gradle_configuration_cache_off(
        [
            "--no-configuration-cache",
            "--no-configuration-cache",
        ]
    )

    assert result == [
        "--no-configuration-cache"
    ]


def test_gradle_10_future_error_deprecation_is_warning() -> None:
    output = (
        "Declaring dependencies using multi-string notation has been "
        "deprecated. This will fail with an error in Gradle 10. "
        'Please use single-string notation '
        '("com.example:runtime:1.0@jar") instead.\\n'
    )

    with tempfile.TemporaryDirectory() as tmp:
        issues = parse_issues(
            Path(tmp),
            "build",
            output,
        )

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].code == "GRADLE_DEPRECATION"
    assert "deprecated" in issues[0].message


def test_project_dependency_gradle_10_deprecation_is_warning() -> None:
    output = (
        "Using a Project object as a dependency notation has been "
        "deprecated. This will fail with an error in Gradle 10. "
        "Please use the project(String) method instead.\\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        issues = parse_issues(
            Path(tmp),
            "build",
            output,
        )

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].code == "GRADLE_DEPRECATION"


def test_no_device_init_script_wiring_forces_cache_off() -> None:
    import devtool.android_build as android_build

    source = Path(
        android_build.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "connected_test_skip_init_script is not None"
        in source
    )

    assert (
        "args.gradle_args = "
        "_force_gradle_configuration_cache_off("
        in source
    )

    assert '"--init-script"' in source
