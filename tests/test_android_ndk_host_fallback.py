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

    def _fake_ndk(self, root: Path, version: str) -> Path:
        ndk = root / "ndk" / version
        (ndk / "build" / "cmake").mkdir(parents=True)
        (ndk / "build" / "cmake" / "android.toolchain.cmake").write_text("# test\n")
        (ndk / "source.properties").write_text(f"Pkg.Revision = {version}\n")
        bin_dir = ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
        bin_dir.mkdir(parents=True)
        self._fake_x86_64_elf(bin_dir / "clang++")
        self._fake_x86_64_elf(bin_dir / "llvm-ar")
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

            with patch.object(android_build, "normalized_machine", return_value="arm64"):
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

            with patch.object(android_build, "normalized_machine", return_value="arm64"), patch.object(
                android_build, "resolve_qemu_ld_prefix", return_value=sysroot
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
            ), patch.object(android_build, "install_qemu_ndk_wrappers") as qemu:
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

            with patch.object(android_build, "normalized_machine", return_value="arm64"):
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
