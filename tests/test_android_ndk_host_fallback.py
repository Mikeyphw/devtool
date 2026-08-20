from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool import android_build


class AndroidNdkHostFallbackTest(unittest.TestCase):
    def _fake_x86_64_elf(self, path: Path) -> None:
        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4] = 2  # 64-bit
        header[5] = 1  # little endian
        header[18:20] = (62).to_bytes(2, "little")  # EM_X86_64
        path.write_bytes(bytes(header))
        path.chmod(0o755)

    def _fake_native_linker(self, bin_dir: Path) -> None:
        linker = bin_dir / "ld.lld"
        linker.write_text("#!/bin/sh\nexit 0\n")
        linker.chmod(0o755)

    def _fake_ndk(self, root: Path, version: str) -> Path:
        ndk = root / "ndk" / version
        (ndk / "build" / "cmake").mkdir(parents=True)
        (ndk / "build" / "cmake" / "android.toolchain.cmake").write_text("# test\n")
        (ndk / "source.properties").write_text(f"Pkg.Revision = {version}\n")
        bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir.parent / "sysroot").mkdir()
        (bin_dir.parent / "lib" / "clang" / "19" / "lib" / "linux").mkdir(parents=True)
        self._fake_x86_64_elf(bin_dir / "clang++")
        self._fake_x86_64_elf(bin_dir / "llvm-ar")
        self._fake_x86_64_elf(bin_dir / "lld")
        (bin_dir / "ld.lld").symlink_to("lld")
        return ndk

    def test_qemu_wrapper_replaces_x86_64_ndk_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)
            qemu = root / "qemu-x86_64"
            qemu.write_text("#!/bin/sh\nexit 0\n")
            qemu.chmod(0o755)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "ndk_host_resource_dir_ready", return_value=True),
            ):
                self.assertTrue(
                    android_build.install_qemu_ndk_wrappers(
                        sdk,
                        version,
                        cache_root=root / "cache",
                        qemu_command=str(qemu),
                        qemu_ld_prefix="none",
                        qemu_sysroot_url="",
                        qemu_sysroot_sha256="",
                        offline=True,
                        force=False,
                        dry_run=False,
                        install_policy="never",
                    )
                )

            clang = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin" / "clang++"
            backup = clang.with_name("clang++.devtool-x86_64")
            self.assertTrue(backup.is_file())
            text = clang.read_text()
            self.assertIn(str(qemu), text)
            self.assertIn(str(backup), text)
            self.assertIn("-U LD_PRELOAD", text)
            self.assertIn(f"-0 {clang}", text)
            resource_dir = clang.parent.parent / "lib" / "clang" / "19"
            self.assertIn(f"-resource-dir {resource_dir}", text)

            linker = clang.parent / "ld.lld"
            linker_text = linker.read_text()
            self.assertIn("-U LD_PRELOAD", linker_text)
            self.assertIn(f"-0 {linker}", linker_text)
            self.assertIn("lld.devtool-x86_64", linker_text)

    def test_qemu_wrapper_repair_recovers_old_multicall_target_and_preserves_argv0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            qemu = root / "qemu-x86_64"
            qemu.write_text("#!/bin/sh\nexit 0\n")
            qemu.chmod(0o755)
            real = bin_dir / "lld.devtool-x86_64"
            self._fake_x86_64_elf(real)
            wrapper = bin_dir / "ld.lld"
            wrapper.write_text(f'#!/bin/sh\nexec {qemu} {real} "$@"\n')
            wrapper.chmod(0o755)

            self.assertEqual(android_build.x86_64_binary_candidate(wrapper), real)
            android_build.write_qemu_wrapper(wrapper, real, qemu, None, dry_run=False)
            repaired = wrapper.read_text()
            self.assertIn("-U LD_PRELOAD", repaired)
            self.assertIn(f"-0 {wrapper}", repaired)
            self.assertIn(str(real), repaired)

    def test_ndk_linker_health_rejects_generic_lld_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            bin_dir = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            bin_dir.mkdir(parents=True)
            linker = bin_dir / "ld.lld"
            linker.write_text("#!/bin/sh\necho 'lld is a generic driver.' >&2\nexit 1\n")
            linker.chmod(0o755)

            self.assertFalse(android_build.ndk_host_linker_runs(sdk, version))

            linker.write_text("#!/bin/sh\necho 'LLD 19.0.1'\nexit 0\n")
            linker.chmod(0o755)
            self.assertTrue(android_build.ndk_host_linker_runs(sdk, version))

    def test_ndk_resource_dir_health_rejects_termux_host_clang_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            bin_dir = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            resource_dir = bin_dir.parent / "lib" / "clang" / "19"
            resource_dir.mkdir(parents=True)
            bin_dir.mkdir(parents=True, exist_ok=True)
            compiler = bin_dir / "clang++"
            compiler.write_text("#!/bin/sh\necho /data/data/com.termux/files/usr/lib/clang/19\n")
            compiler.chmod(0o755)

            self.assertFalse(android_build.ndk_host_resource_dir_ready(sdk, version))

            compiler.write_text(f"#!/bin/sh\necho {resource_dir}\n")
            compiler.chmod(0o755)
            self.assertTrue(android_build.ndk_host_resource_dir_ready(sdk, version))

    def test_ndk_resource_dir_discovery_prefers_highest_complete_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            host = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64"
            for item in ("18", "19.0.1"):
                (host / "lib" / "clang" / item / "lib" / "linux").mkdir(parents=True)
            (host / "lib" / "clang" / "20").mkdir(parents=True)

            self.assertEqual(
                android_build.ndk_clang_resource_dir(sdk, version),
                host / "lib" / "clang" / "19.0.1",
            )

    def test_ndk_android_link_health_checks_both_64_bit_abis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            bin_dir = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir.parent / "sysroot").mkdir()
            compiler = bin_dir / "clang++"
            log = root / "targets.log"
            compiler.write_text(
                "#!/bin/sh\n"
                f"printf '%s\n' \"$*\" >> {log}\n"
                "case \"$*\" in *x86_64-none-linux-android21*) exit 1;; esac\n"
                "exit 0\n"
            )
            compiler.chmod(0o755)

            self.assertFalse(android_build.ndk_host_android_link_runs(sdk, version))
            calls = log.read_text()
            self.assertIn("aarch64-none-linux-android21", calls)
            self.assertIn("x86_64-none-linux-android21", calls)

            compiler.write_text("#!/bin/sh\nexit 0\n")
            compiler.chmod(0o755)
            self.assertTrue(android_build.ndk_host_android_link_runs(sdk, version))

    def test_termux_materializes_ndk_executable_symlinks_as_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir.parent / "sysroot").mkdir()
            lld = bin_dir / "lld"
            lld.write_text("#!/bin/sh\nexit 0\n")
            lld.chmod(0o755)
            ld_lld = bin_dir / "ld.lld"
            ld_lld.symlink_to("lld")
            clang = bin_dir / "clang"
            clang.write_text("#!/bin/sh\nexit 0\n")
            clang.chmod(0o755)
            clangxx = bin_dir / "clang++"
            clangxx.symlink_to("clang")

            with patch.object(android_build, "running_under_termux_python", return_value=True):
                self.assertTrue(
                    android_build.ensure_ndk_termux_multicall_aliases(sdk, version, dry_run=False)
                )

            self.assertFalse(ld_lld.is_symlink())
            self.assertFalse(clangxx.is_symlink())
            self.assertEqual(ld_lld.stat().st_ino, lld.stat().st_ino)
            self.assertEqual(clangxx.stat().st_ino, clang.stat().st_ino)
            self.assertTrue((bin_dir / "ld.lld.devtool-symlink").is_symlink())
            self.assertTrue((bin_dir / "clang++.devtool-symlink").is_symlink())

    def test_native_arm64_ndk_exposes_legacy_linux_x86_alias_for_cmake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            (ndk / "build" / "cmake").mkdir(parents=True)
            (ndk / "build" / "cmake" / "android.toolchain.cmake").write_text("# test\n")
            (ndk / "source.properties").write_text(f"Pkg.Revision = {version}\n")
            arm = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-arm64"
            (arm / "bin").mkdir(parents=True)
            (arm / "sysroot").mkdir()
            clang = arm / "bin" / "clang++"
            clang.write_text("#!/bin/sh\nexit 0\n")
            clang.chmod(0o755)
            self._fake_native_linker(arm / "bin")

            with patch.object(android_build, "normalized_machine", return_value="arm64"):
                self.assertTrue(android_build.expose_arm64_ndk_as_linux_x86_64(sdk, version, dry_run=False))

            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            canonical = prebuilt / "linux-x86_64"
            legacy = prebuilt / "linux-x86"
            self.assertTrue(canonical.is_symlink())
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(canonical.resolve(), arm.resolve())
            self.assertEqual(legacy.resolve(), arm.resolve())
            self.assertTrue((legacy / "sysroot").is_dir())
            self.assertTrue((legacy / "bin" / "clang++").is_file())

    def test_native_arm64_ndk_already_exposed_still_finalizes_termux_cmake_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            cmake = ndk / "build" / "cmake"
            cmake.mkdir(parents=True)
            toolchain = cmake / "android.toolchain.cmake"
            toolchain.write_text(
                "if(CMAKE_HOST_SYSTEM_NAME STREQUAL Linux)\n"
                "  set(ANDROID_HOST_TAG linux-x86_64)\n"
                "endif()\n"
            )
            (ndk / "source.properties").write_text(f"Pkg.Revision = {version}\n")
            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            arm = prebuilt / "linux-arm64"
            (arm / "bin").mkdir(parents=True)
            (arm / "sysroot").mkdir()
            clang = arm / "bin" / "clang++"
            clang.write_text("#!/bin/sh\nexit 0\n")
            clang.chmod(0o755)
            self._fake_native_linker(arm / "bin")
            (prebuilt / "linux-x86_64").symlink_to(arm.name, target_is_directory=True)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "running_under_termux_python", return_value=True),
            ):
                self.assertTrue(android_build.expose_arm64_ndk_as_linux_x86_64(sdk, version, dry_run=False))

            canonical = prebuilt / "linux-x86_64"
            legacy = prebuilt / "linux-x86"
            self.assertEqual(canonical.resolve(), arm.resolve())
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(), arm.resolve())
            self.assertTrue((prebuilt / "bin").is_symlink())
            self.assertTrue((prebuilt / "sysroot").is_symlink())
            self.assertEqual((prebuilt / "bin").resolve(), (arm / "bin").resolve())
            self.assertEqual((prebuilt / "sysroot").resolve(), (arm / "sysroot").resolve())
            patched = toolchain.read_text()
            self.assertIn("CMAKE_HOST_SYSTEM_NAME STREQUAL Android", patched)
            self.assertTrue((prebuilt / "bin" / "clang++").is_file())

    def test_qemu_ndk_exposes_legacy_linux_x86_alias_for_arm_hosted_cmake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = self._fake_ndk(sdk, version)
            qemu = root / "qemu-x86_64"
            qemu.write_text("#!/bin/sh\nexit 0\n")
            qemu.chmod(0o755)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "ndk_host_resource_dir_ready", return_value=True),
            ):
                self.assertTrue(
                    android_build.install_qemu_ndk_wrappers(
                        sdk,
                        version,
                        cache_root=root / "cache",
                        qemu_command=str(qemu),
                        qemu_ld_prefix="none",
                        qemu_sysroot_url="",
                        qemu_sysroot_sha256="",
                        offline=True,
                        force=False,
                        dry_run=False,
                        install_policy="never",
                    )
                )

            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            legacy = prebuilt / "linux-x86"
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.resolve(), (prebuilt / "linux-x86_64").resolve())
            self.assertTrue((legacy / "sysroot").is_dir())
            self.assertIn("qemu-x86_64", (legacy / "bin" / "clang++").read_text())


    def test_termux_empty_host_tag_exposes_bin_and_sysroot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            canonical = prebuilt / "linux-x86_64"
            (canonical / "bin").mkdir(parents=True)
            (canonical / "sysroot").mkdir()
            clang = canonical / "bin" / "clang++"
            clang.write_text("#!/bin/sh\nexit 0\n")
            clang.chmod(0o755)

            with patch.object(android_build, "running_under_termux_python", return_value=True):
                self.assertTrue(
                    android_build.ensure_ndk_empty_host_tag_compat(sdk, version, dry_run=False)
                )
                self.assertTrue(
                    android_build.ensure_ndk_empty_host_tag_compat(sdk, version, dry_run=False)
                )

            self.assertTrue((prebuilt / "bin").is_symlink())
            self.assertTrue((prebuilt / "sysroot").is_symlink())
            self.assertEqual((prebuilt / "bin").resolve(), (canonical / "bin").resolve())
            self.assertEqual((prebuilt / "sysroot").resolve(), (canonical / "sysroot").resolve())
            self.assertTrue((prebuilt / "bin" / "clang++").is_file())

    def test_termux_empty_host_tag_preserves_conflicting_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            canonical = prebuilt / "linux-x86_64"
            (canonical / "bin").mkdir(parents=True)
            (canonical / "sysroot").mkdir()
            (canonical / "bin" / "clang++").write_text("clang")
            (prebuilt / "bin").mkdir()
            (prebuilt / "bin" / "old").write_text("old")
            (prebuilt / "sysroot").mkdir()
            (prebuilt / "sysroot" / "old").write_text("old")

            with patch.object(android_build, "running_under_termux_python", return_value=True):
                self.assertTrue(
                    android_build.ensure_ndk_empty_host_tag_compat(sdk, version, dry_run=False)
                )

            self.assertTrue((prebuilt / "bin.devtool-original" / "old").is_file())
            self.assertTrue((prebuilt / "sysroot.devtool-original" / "old").is_file())
            self.assertTrue((prebuilt / "bin").is_symlink())
            self.assertTrue((prebuilt / "sysroot").is_symlink())

    def test_termux_cmake_host_patch_extends_linux_branch_to_android(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            cmake_dir = sdk / "ndk" / version / "build" / "cmake"
            cmake_dir.mkdir(parents=True)
            source = (
                "if(CMAKE_HOST_SYSTEM_NAME STREQUAL Linux)\n"
                "  set(ANDROID_HOST_TAG linux-x86_64)\n"
                "elseif(CMAKE_HOST_SYSTEM_NAME STREQUAL Darwin)\n"
                "  set(ANDROID_HOST_TAG darwin-x86_64)\n"
                "endif()\n"
            )
            legacy = cmake_dir / "android-legacy.toolchain.cmake"
            primary = cmake_dir / "android.toolchain.cmake"
            legacy.write_text(source)
            primary.write_text(source)

            with patch.object(android_build, "running_under_termux_python", return_value=True):
                self.assertTrue(
                    android_build.ensure_ndk_cmake_termux_host_support(
                        sdk, version, dry_run=False
                    )
                )
                # A second run must be idempotent.
                self.assertTrue(
                    android_build.ensure_ndk_cmake_termux_host_support(
                        sdk, version, dry_run=False
                    )
                )

            for path in (legacy, primary):
                text = path.read_text()
                self.assertEqual(text.count("CMAKE_HOST_SYSTEM_NAME STREQUAL Android"), 1)
                self.assertIn(
                    "if(CMAKE_HOST_SYSTEM_NAME STREQUAL Linux OR "
                    "CMAKE_HOST_SYSTEM_NAME STREQUAL Android)",
                    text,
                )
                self.assertTrue(path.with_name(path.name + ".devtool-original").is_file())

    def test_termux_cmake_host_patch_rejects_unknown_legacy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            cmake_dir = sdk / "ndk" / version / "build" / "cmake"
            cmake_dir.mkdir(parents=True)
            (cmake_dir / "android-legacy.toolchain.cmake").write_text("# unknown layout\n")
            with patch.object(android_build, "running_under_termux_python", return_value=True):
                self.assertFalse(
                    android_build.ensure_ndk_cmake_termux_host_support(
                        sdk, version, dry_run=False
                    )
                )

    def test_native_arm64_ndk_patches_android_host_before_cmake_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = sdk / "ndk" / version
            cmake_dir = ndk / "build" / "cmake"
            cmake_dir.mkdir(parents=True)
            toolchain = (
                "if(CMAKE_HOST_SYSTEM_NAME STREQUAL Linux)\n"
                "  set(ANDROID_HOST_TAG linux-x86_64)\n"
                "elseif(CMAKE_HOST_SYSTEM_NAME STREQUAL Darwin)\n"
                "  set(ANDROID_HOST_TAG darwin-x86_64)\n"
                "endif()\n"
            )
            (cmake_dir / "android-legacy.toolchain.cmake").write_text(toolchain)
            (cmake_dir / "android.toolchain.cmake").write_text(toolchain)
            (ndk / "source.properties").write_text(f"Pkg.Revision = {version}\n")
            arm = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-arm64"
            (arm / "bin").mkdir(parents=True)
            (arm / "sysroot").mkdir()
            clang = arm / "bin" / "clang++"
            clang.write_text("#!/bin/sh\nexit 0\n")
            clang.chmod(0o755)
            self._fake_native_linker(arm / "bin")

            with patch.object(android_build, "normalized_machine", return_value="arm64"), patch.object(
                android_build, "running_under_termux_python", return_value=True
            ):
                self.assertTrue(
                    android_build.expose_arm64_ndk_as_linux_x86_64(
                        sdk, version, dry_run=False
                    )
                )

            patched = (cmake_dir / "android-legacy.toolchain.cmake").read_text()
            self.assertIn("CMAKE_HOST_SYSTEM_NAME STREQUAL Android", patched)
            canonical = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64"
            self.assertTrue((canonical / "bin" / "clang++").is_file())

    def test_auto_provider_requires_configured_arm64_url_or_qemu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)

            with patch.object(android_build, "normalized_machine", return_value="arm64"), patch.object(
                android_build, "find_qemu_x86_64", return_value=None
            ):
                # auto mode should not raise; it leaves a warning when neither
                # a configured ARM64 NDK nor QEMU is available.
                android_build.ensure_ndk_host_compatible(
                    sdk,
                    version,
                    root / "cache",
                    provider="auto",
                    arm64_ndk_url="",
                    qemu_command="",
                    qemu_ld_prefix="none",
                    qemu_sysroot_url="",
                    qemu_sysroot_sha256="",
                    qemu_install="never",
                    offline=True,
                    force=False,
                    dry_run=False,
                )

    def test_android_integration_forwards_ndk_host_options(self) -> None:
        source = Path(android_build.__file__).read_text()
        self.assertIn("--ndk-host-provider", source)
        self.assertIn("--arm64-ndk-url", source)
        self.assertIn("--qemu-install", source)
        self.assertIn("--qemu-sysroot-url", source)

    def test_qemu_auto_prefix_downloads_sysroot_when_loader_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root / "cache" / "qemu" / "x86_64-sysroot" / "ubuntu-base"
            sysroot.mkdir(parents=True)
            (sysroot / "lib64").mkdir()
            (sysroot / "lib64" / "ld-linux-x86-64.so.2").write_text("loader")
            def has_loader(path: Path) -> bool:
                return path == sysroot

            with patch.object(android_build, "qemu_prefix_has_x86_64_loader", side_effect=has_loader), patch.object(
                android_build, "obtain_qemu_x86_64_sysroot", return_value=sysroot
            ) as obtain:
                resolved = android_build.resolve_qemu_ld_prefix(
                    "auto",
                    root / "cache",
                    qemu_sysroot_url="https://example.invalid/rootfs.tar.gz",
                    qemu_sysroot_sha256="",
                    offline=False,
                    force=False,
                    dry_run=False,
                )
            self.assertEqual(resolved, sysroot)
            obtain.assert_called_once()

    def test_existing_qemu_wrappers_are_rewritten_with_new_sysroot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)
            bin_dir = sdk / "ndk" / version / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            clang = bin_dir / "clang++"
            backup = clang.with_name("clang++.devtool-x86_64")
            clang.rename(backup)
            clang.write_text("#!/bin/sh\nexit 127\n")
            clang.chmod(0o755)
            qemu = root / "qemu-x86_64"
            qemu.write_text("#!/bin/sh\nexit 0\n")
            qemu.chmod(0o755)
            sysroot = root / "sysroot"
            (sysroot / "lib64").mkdir(parents=True)
            (sysroot / "lib64" / "ld-linux-x86-64.so.2").write_text("loader")

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "resolve_qemu_ld_prefix", return_value=sysroot),
                patch.object(android_build, "ndk_host_resource_dir_ready", return_value=True),
            ):
                self.assertTrue(
                    android_build.install_qemu_ndk_wrappers(
                        sdk,
                        version,
                        cache_root=root / "cache",
                        qemu_command=str(qemu),
                        qemu_ld_prefix="auto",
                        qemu_sysroot_url="",
                        qemu_sysroot_sha256="",
                        offline=True,
                        force=False,
                        dry_run=False,
                        install_policy="never",
                    )
                )
            text = clang.read_text()
            self.assertIn("-L", text)
            self.assertIn(str(sysroot), text)
            self.assertIn(str(backup), text)

    def test_qemu_fallback_restores_pristine_google_prebuilt_after_native_arm_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = self._fake_ndk(sdk, version)
            prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
            google = prebuilt / "linux-x86_64"
            original_google = prebuilt / "linux-x86_64.google-x86_64"
            google.rename(original_google)

            arm = prebuilt / "linux-aarch64"
            (arm / "bin").mkdir(parents=True)
            native = arm / "bin" / "clang++"
            native.write_text("#!/bin/sh\nexit 0\n")
            native.chmod(0o755)
            (arm / "sysroot").mkdir()
            (arm / "lib" / "clang" / "19" / "lib" / "linux").mkdir(parents=True)
            google.symlink_to(arm, target_is_directory=True)

            self.assertTrue(
                android_build.restore_google_ndk_x86_64_prebuilt_for_qemu(
                    sdk, version, dry_run=False
                )
            )

            self.assertFalse(google.is_symlink())
            self.assertTrue((google / "bin" / "clang++").is_file())
            self.assertEqual(android_build.native_tool_arch(google / "bin" / "clang++"), "x86_64")
            displaced = prebuilt / "linux-x86_64.devtool-arm-host"
            self.assertTrue(displaced.is_symlink())
            self.assertEqual(displaced.resolve(), arm.resolve())
            self.assertFalse(original_google.exists())

    def test_auto_provider_prefers_configured_arm64_ndk_before_existing_qemu_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)

            calls: list[str] = []

            def expose(_sdk: Path, _version: str, *, dry_run: bool) -> bool:
                calls.append("expose")
                return False

            def obtain(_sdk: Path, _version: str, _cache: Path, *, url: str, offline: bool, force: bool, dry_run: bool) -> bool:
                calls.append(f"arm64:{url}")
                return True

            with patch.object(android_build, "normalized_machine", return_value="arm64"), patch.object(
                android_build, "expose_arm64_ndk_as_linux_x86_64", side_effect=expose
            ), patch.object(android_build, "obtain_unofficial_arm64_ndk", side_effect=obtain), patch.object(
                android_build, "ndk_host_toolchain_runs", return_value=True
            ), patch.object(android_build, "ensure_ndk_termux_multicall_aliases", return_value=True), patch.object(
                android_build, "ensure_ndk_legacy_cmake_arm_alias", return_value=True
            ), patch.object(android_build, "ensure_ndk_cmake_termux_host_support", return_value=True), patch.object(
                android_build, "ensure_ndk_empty_host_tag_compat", return_value=True
            ), patch.object(android_build, "ndk_host_linker_runs", return_value=True), patch.object(
                android_build, "ndk_host_resource_dir_ready", return_value=True
            ), patch.object(android_build, "ndk_host_android_link_runs", return_value=True), patch.object(
                android_build, "install_qemu_ndk_wrappers"
            ) as qemu:
                android_build.ensure_ndk_host_compatible(
                    sdk,
                    version,
                    root / "cache",
                    provider="auto",
                    arm64_ndk_url="https://example.invalid/android-ndk-r28c-aarch64.tar.xz",
                    qemu_command="",
                    qemu_ld_prefix="auto",
                    qemu_sysroot_url="",
                    qemu_sysroot_sha256="",
                    qemu_install="auto",
                    offline=False,
                    force=False,
                    dry_run=False,
                )

            self.assertEqual(calls, ["expose", "arm64:https://example.invalid/android-ndk-r28c-aarch64.tar.xz"])
            qemu.assert_not_called()

    def test_auto_native_candidate_defers_synthetic_runtime_probe_to_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "expose_arm64_ndk_as_linux_x86_64", return_value=True) as expose,
                patch.object(android_build, "ndk_host_toolchain_runs", return_value=True),
                patch.object(android_build, "ensure_ndk_termux_multicall_aliases", return_value=True),
                patch.object(android_build, "ensure_ndk_legacy_cmake_arm_alias", return_value=True),
                patch.object(android_build, "ensure_ndk_cmake_termux_host_support", return_value=True),
                patch.object(android_build, "ensure_ndk_empty_host_tag_compat", return_value=True),
                patch.object(android_build, "ndk_host_linker_runs", return_value=True),
                patch.object(android_build, "ndk_host_resource_dir_ready", return_value=True),
                patch.object(android_build, "ndk_host_android_link_runs", return_value=False) as link_probe,
                patch.object(android_build, "restore_google_ndk_x86_64_prebuilt_for_qemu") as restore,
                patch.object(android_build, "install_qemu_ndk_wrappers") as qemu,
                patch.object(android_build, "warn") as warning,
                patch.object(android_build, "info") as information,
            ):
                android_build.ensure_ndk_host_compatible(
                    sdk,
                    version,
                    root / "cache",
                    provider="auto",
                    arm64_ndk_url="",
                    qemu_command="",
                    qemu_ld_prefix="auto",
                    qemu_sysroot_url="",
                    qemu_sysroot_sha256="",
                    qemu_install="auto",
                    offline=False,
                    force=False,
                    dry_run=False,
                )

            expose.assert_called_once()
            link_probe.assert_called_once_with(sdk, version)
            restore.assert_not_called()
            qemu.assert_not_called()
            warning.assert_not_called()
            self.assertTrue(
                any("defers final compatibility to the real Gradle/CMake build" in str(call) for call in information.call_args_list)
            )

    def test_forced_qemu_reports_missing_preserved_google_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            self._fake_ndk(sdk, version)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "ndk_host_toolchain_runs", return_value=False),
                patch.object(android_build, "restore_google_ndk_x86_64_prebuilt_for_qemu", return_value=True),
                patch.object(android_build, "install_qemu_ndk_wrappers", return_value=False),
                patch.object(android_build, "ndk_x86_64_real_binary", return_value=None),
            ):
                with self.assertRaisesRegex(
                    android_build.BuildError,
                    "no preserved x86_64 Google host compiler",
                ):
                    android_build.ensure_ndk_host_compatible(
                        sdk,
                        version,
                        root / "cache",
                        provider="qemu",
                        arm64_ndk_url="",
                        qemu_command="",
                        qemu_ld_prefix="auto",
                        qemu_sysroot_url="",
                        qemu_sysroot_sha256="",
                        qemu_install="auto",
                        offline=False,
                        force=False,
                        dry_run=False,
                    )

    def test_qemu_wrapper_targets_preserved_elf_for_ndk_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            version = "28.2.13676358"
            ndk = self._fake_ndk(sdk, version)
            bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
            clangpp = bin_dir / "clang++"
            clang = bin_dir / "clang"
            clangpp.unlink()
            self._fake_x86_64_elf(clang)
            clangpp.symlink_to("clang")
            qemu = root / "qemu-x86_64"
            qemu.write_text("#!/bin/sh\nexit 0\n")
            qemu.chmod(0o755)

            with (
                patch.object(android_build, "normalized_machine", return_value="arm64"),
                patch.object(android_build, "ndk_host_resource_dir_ready", return_value=True),
            ):
                self.assertTrue(
                    android_build.install_qemu_ndk_wrappers(
                        sdk,
                        version,
                        cache_root=root / "cache",
                        qemu_command=str(qemu),
                        qemu_ld_prefix="none",
                        qemu_sysroot_url="",
                        qemu_sysroot_sha256="",
                        offline=True,
                        force=False,
                        dry_run=False,
                        install_policy="never",
                    )
                )

            clang_backup = clang.with_name("clang.devtool-x86_64")
            self.assertTrue(clang_backup.is_file())
            text = clangpp.read_text()
            self.assertIn(str(qemu), text)
            self.assertIn(str(clang_backup), text)
            self.assertNotIn("clang++.devtool-x86_64", text)

    def test_clean_android_cxx_state_removes_module_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for rel in (".cxx", "app/.cxx", "app/build/.cxx"):
                path = root / rel
                path.mkdir(parents=True)
                (path / "CMakeCache.txt").write_text("stale")
            android_build.clean_android_cxx_state(root, dry_run=False)
            self.assertFalse((root / ".cxx").exists())
            self.assertFalse((root / "app" / ".cxx").exists())
            self.assertFalse((root / "app" / "build" / ".cxx").exists())

    def test_qemu_sysroot_cache_identity_is_url_and_sha_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = android_build.qemu_sysroot_cache_dir(root, "https://one.example/rootfs.tar.gz", "a" * 64)
            second = android_build.qemu_sysroot_cache_dir(root, "https://two.example/rootfs.tar.gz", "a" * 64)
            third = android_build.qemu_sysroot_cache_dir(root, "https://one.example/rootfs.tar.gz", "b" * 64)

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertIn("rootfs", first.name)

    def test_qemu_sysroot_cache_requires_matching_identity_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = android_build.qemu_sysroot_cache_dir(root, "https://one.example/rootfs.tar.gz", "a" * 64)
            (target / "lib64").mkdir(parents=True)
            (target / "lib64" / "ld-linux-x86-64.so.2").write_bytes(b"loader")

            self.assertFalse(
                android_build._qemu_sysroot_identity_matches(
                    target,
                    url="https://one.example/rootfs.tar.gz",
                    expected_sha256="a" * 64,
                )
            )
            android_build._write_qemu_sysroot_identity(
                target,
                url="https://one.example/rootfs.tar.gz",
                expected_sha256="a" * 64,
            )

            self.assertTrue(
                android_build._qemu_sysroot_identity_matches(
                    target,
                    url="https://one.example/rootfs.tar.gz",
                    expected_sha256="a" * 64,
                )
            )
            self.assertFalse(
                android_build._qemu_sysroot_identity_matches(
                    target,
                    url="https://one.example/rootfs.tar.gz",
                    expected_sha256="b" * 64,
                )
            )


    def test_qemu_rootfs_extraction_rewrites_absolute_symlink_for_python314(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "rootfs.tar"
            destination = root / "out"

            payload = b"#!/bin/sh\n"
            with tarfile.open(archive, "w") as package:
                executable = tarfile.TarInfo("usr/bin/mawk")
                executable.mode = 0o755
                executable.size = len(payload)
                package.addfile(executable, io.BytesIO(payload))

                link = tarfile.TarInfo("etc/alternatives/awk")
                link.type = tarfile.SYMTYPE
                link.linkname = "/usr/bin/mawk"
                package.addfile(link)

            android_build.extract_archive(
                archive,
                destination,
                rootfs_links=True,
            )

            extracted_link = destination / "etc/alternatives/awk"
            self.assertTrue(extracted_link.is_symlink())
            self.assertEqual(
                os.readlink(extracted_link),
                "../../usr/bin/mawk",
            )
            self.assertEqual(
                extracted_link.resolve(),
                (destination / "usr/bin/mawk").resolve(),
            )

    def test_qemu_sysroot_uses_rootfs_link_extraction_mode(self) -> None:
        source = Path(android_build.__file__).read_text()
        self.assertIn(
            "extract_archive(archive, staging, rootfs_links=True)",
            source,
        )

if __name__ == "__main__":
    unittest.main()
