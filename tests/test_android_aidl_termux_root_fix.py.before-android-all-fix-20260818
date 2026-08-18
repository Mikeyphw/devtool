from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from devtool import android_build
from devtool import termux_toolchains


def test_aidl_probe_rejects_shell_syntax_fallback() -> None:
    result = subprocess.CompletedProcess(
        ["aidl", "--version"],
        2,
        stdout=b"",
        stderr=b"aidl: 1: Syntax error: word unexpected (expecting \")\")\n",
    )
    with patch("devtool.android_build.subprocess.run", return_value=result):
        assert not android_build.android_build_tool_executes(
            Path("/tmp/aidl"), "aidl"
        )


def test_aidl_probe_rejects_nonzero_without_loader_marker() -> None:
    result = subprocess.CompletedProcess(
        ["aidl", "--version"], 2, stdout=b"", stderr=b"usage error\n"
    )
    with patch("devtool.android_build.subprocess.run", return_value=result):
        assert not android_build.android_build_tool_executes(
            Path("/tmp/aidl"), "aidl"
        )


def test_aidl_probe_accepts_successful_version_probe() -> None:
    result = subprocess.CompletedProcess(
        ["aidl", "--version"], 0, stdout=b"aidl version\n", stderr=b""
    )
    with patch("devtool.android_build.subprocess.run", return_value=result):
        assert android_build.android_build_tool_executes(Path("/tmp/aidl"), "aidl")


def test_packaging_revision_collapses_to_canonical_build_tools_id() -> None:
    assert android_build.canonical_build_tools_package_id("36.0.0-5") == "36.0.0"
    assert android_build.canonical_build_tools_package_id("36.0.0") == "36.0.0"
    assert android_build.canonical_build_tools_package_id("36.0.0-rc3") == "36.0.0-rc3"


def test_installed_versions_do_not_expose_packaging_suffix_to_agp() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        sdk = Path(temporary)
        legacy = sdk / "build-tools" / "36.0.0-5"
        legacy.mkdir(parents=True)
        (legacy / "source.properties").write_text("Pkg.Revision = 36.0.0-5\n")

        assert android_build.installed_build_tools_versions(sdk) == ["36.0.0"]


def test_normalize_repairs_legacy_packaging_suffix_in_place() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        sdk = Path(temporary)
        legacy = sdk / "build-tools" / "36.0.0-5"
        legacy.mkdir(parents=True)
        (legacy / "source.properties").write_text("Pkg.Revision = 36.0.0-5\n")
        (legacy / "aidl").write_bytes(b"payload")

        android_build.normalize_sdk_build_tools_locations(sdk, dry_run=False)

        assert not legacy.exists()
        assert (sdk / "build-tools" / "36.0.0" / "aidl").read_bytes() == b"payload"


def test_canonical_directory_is_not_renamed_from_packaging_metadata() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        sdk = Path(temporary)
        canonical = sdk / "build-tools" / "36.0.0"
        canonical.mkdir(parents=True)
        (canonical / "source.properties").write_text("Pkg.Revision = 36.0.0-5\n")

        android_build.normalize_sdk_build_tools_locations(sdk, dry_run=False)

        assert canonical.is_dir()
        assert not (sdk / "build-tools" / "36.0.0-5").exists()


def test_sdk_host_compatibility_uses_runtime_probe() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        sdk = Path(temporary)
        tool = sdk / "build-tools" / "36.0.0" / "aidl"
        tool.parent.mkdir(parents=True)
        tool.write_bytes(
            b"\x7fELF"
            + bytes([2, 1, 1])
            + bytes(11)
            + (183).to_bytes(2, "little")
            + bytes(64)
        )
        tool.chmod(0o755)

        with patch("devtool.android_build.normalized_machine", return_value="arm64"), patch(
            "devtool.android_build.native_android_build_tool_compatible",
            return_value=False,
        ) as compatible:
            assert not android_build.sdk_host_compatible(sdk)
            compatible.assert_called_once_with(tool, "aidl", "arm64")


def test_termux_aidl_provider_tracks_aidl_package() -> None:
    assert termux_toolchains._TERMUX_ANDROID_TOOL_PACKAGES["aidl"] == "aidl"
