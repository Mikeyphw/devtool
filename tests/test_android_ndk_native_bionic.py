from pathlib import Path
import tempfile
from unittest.mock import patch

from devtool import android_build


def _fake_arm64_elf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1

    # ELF e_machine = EM_AARCH64 (183)
    header[18:20] = (183).to_bytes(2, "little")

    path.write_bytes(header)
    path.chmod(0o755)


def test_bionic_ndk_keeps_linux_arm64_and_adds_linux_aarch64_alias():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sdk = root / "sdk"
        version = "29.0.14206865"

        ndk = sdk / "ndk" / version
        prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
        arm = prebuilt / "linux-arm64"

        _fake_arm64_elf(arm / "bin" / "clang-21")

        (arm / "bin" / "clang++").symlink_to("clang-21")
        (arm / "bin" / "ld.lld").symlink_to("clang-21")

        (arm / "sysroot").mkdir(parents=True)

        with (
            patch.object(
                android_build,
                "running_under_termux_python",
                return_value=True,
            ),
            patch.object(
                android_build,
                "normalized_machine",
                return_value="arm64",
            ),
            patch.object(
                android_build,
                "executable_runs",
                return_value=True,
            ),
        ):
            assert (
                android_build.native_android_ndk_prebuilt(
                    sdk,
                    version,
                )
                == arm
            )

            assert android_build.ensure_native_android_ndk_layout(
                sdk,
                version,
                dry_run=False,
            )

        alias = prebuilt / "linux-aarch64"

        assert alias.is_symlink()
        assert alias.resolve() == arm.resolve()

        # Most important regression:
        # the native Bionic NDK must NOT be disguised as Google x86_64.
        assert not (prebuilt / "linux-x86_64").exists()
