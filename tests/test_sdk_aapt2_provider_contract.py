from pathlib import Path

import devtool.android_build as android_build


def test_explicit_sdk_provider_has_real_sdk_selection_lane() -> None:
    source = android_build.standalone_source_path().read_text(
        encoding="utf-8"
    )

    assert (
        'args.aapt2_provider in {"auto", "sdk"}'
        in source
    )

    assert (
        'args.aapt2_provider in {"auto", "sdk", "none"}'
        not in source
    )

    assert (
        "Explicit Android SDK AAPT2 provider was requested"
        in source
    )


def test_sdk_aapt2_selector_accepts_compatible_local_binary(
    tmp_path,
    monkeypatch,
) -> None:
    sdk = tmp_path / "sdk"

    aapt2 = (
        sdk
        / "build-tools"
        / "36.0.0"
        / "aapt2"
    )

    aapt2.parent.mkdir(parents=True)
    aapt2.write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )

    aapt2.chmod(0o755)

    monkeypatch.setattr(
        android_build,
        "termux_aapt2_supports_platform",
        lambda *args, **kwargs: True,
    )

    selected = (
        android_build.select_sdk_build_tools_aapt2(
            sdk,
            "36.0.0",
            "android-36",
        )
    )

    assert selected == aapt2
