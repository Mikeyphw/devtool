from pathlib import Path
from unittest.mock import patch

from devtool import android_build


def test_native_termux_rejects_chroot_sdk_path() -> None:
    with patch(
        "devtool.android_build.running_under_termux_python",
        return_value=True,
    ):
        result = android_build._native_termux_sanitize_guest_path(
            Path("/home/mike/Android/Sdk"),
            label="Android SDK root",
        )

    assert result is None


def test_native_termux_rehomes_chroot_gradle_path(tmp_path: Path) -> None:
    fallback = tmp_path / ".gradle"

    with patch(
        "devtool.android_build.running_under_termux_python",
        return_value=True,
    ):
        result = android_build._native_termux_sanitize_guest_path(
            Path("/home/mike/.gradle"),
            label="Gradle user home",
            fallback=fallback,
        )

    assert result == fallback


def test_real_chroot_keeps_guest_paths() -> None:
    value = Path("/home/mike/Android/Sdk")

    with patch(
        "devtool.android_build.running_under_termux_python",
        return_value=False,
    ):
        result = android_build._native_termux_sanitize_guest_path(
            value,
            label="Android SDK root",
        )

    assert result == value


def test_both_java_provisioning_paths_force_fresh_discovery() -> None:
    source = Path(android_build.__file__).read_text(encoding="utf-8")

    main = source[source.index("def main("):]

    always = main[
        main.index('if args.system_deps == "always":'):
        main.index('elif args.system_deps == "auto"')
    ]

    auto = main[
        main.index('elif args.system_deps == "auto"'):
        main.index('elif need_java:')
    ]

    for branch in (always, auto):
        install = branch.index("install_missing_system_dependencies(")
        rediscovery = branch.index("java_home = find_java_home(", install)
        window = branch[rediscovery:rediscovery + 700]

        assert "provider_evidence=None" in window
        assert "provider_evidence=provider_evidence" not in window
