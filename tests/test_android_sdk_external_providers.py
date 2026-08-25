from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool import android_build, android_integration
from devtool.config import ProjectConfig


class AndroidSdkExternalProviderTests(unittest.TestCase):
    def test_normalize_android_sdk_provider_aliases(self) -> None:
        self.assertEqual(android_build.normalize_android_sdk_provider("termux-void"), "termuxvoid")
        self.assertEqual(android_build.normalize_android_sdk_provider("homu"), "homuhomu")
        self.assertEqual(android_build.normalize_android_sdk_provider("google"), "managed")

    def test_release_asset_sha256_prefers_github_digest(self) -> None:
        release = {"body": "ignored"}
        asset = {"name": "android-sdk.tar.xz", "digest": "sha256:" + "a" * 64}
        self.assertEqual(android_build._release_asset_sha256(release, asset), "a" * 64)

    def test_release_asset_sha256_falls_back_to_release_body(self) -> None:
        digest = "b" * 64
        release = {"body": f"android-sdk-aarch64-linux-android.tar.xz  {digest}"}
        asset = {"name": "android-sdk-aarch64-linux-android.tar.xz"}
        self.assertEqual(android_build._release_asset_sha256(release, asset), digest)

    def test_discovery_includes_termuxvoid_prefix_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "usr"
            sdk = prefix / "opt" / "android-sdk"
            (sdk / "build-tools").mkdir(parents=True)
            (sdk / "platform-tools").mkdir(parents=True)
            with patch.dict(
                "os.environ",
                {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": ""},
                clear=False,
            ), patch(
                "devtool.android_build.running_under_termux_python", return_value=True
            ), patch(
                "devtool.android_build._termux_prefix_path", return_value=prefix
            ), patch("devtool.android_build.sdk_host_compatible", return_value=True):
                found, source = android_build.discover_sdk_root_with_source(None, None)
        self.assertEqual(found, sdk.resolve())
        self.assertEqual(source, "TermuxVoid android-sdk")

    def test_termuxvoid_provider_prefers_repository_android_sdk_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "usr"
            sdk = prefix / "opt" / "android-sdk"
            cache = root / "cache"

            def install(packages, *, dry_run: bool):
                self.assertEqual(list(packages), ["android-sdk"])
                self.assertFalse(dry_run)
                (sdk / "build-tools" / "36.0.0").mkdir(parents=True)
                (sdk / "platform-tools").mkdir(parents=True)

            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch(
                "devtool.android_build._termux_prefix_path", return_value=prefix
            ), patch(
                "devtool.android_build.ensure_termuxvoid_repository"
            ) as setup_repo, patch(
                "devtool.android_build._termux_package_available", return_value=True
            ), patch(
                "devtool.android_build._ensure_termux_packages", side_effect=install
            ), patch(
                "devtool.android_build.sdk_host_compatible", return_value=True
            ), patch(
                "devtool.android_build.github_json"
            ) as github:
                selected = android_build.provision_termuxvoid_sdk(
                    cache, force=False, dry_run=False
                )

            setup_repo.assert_called_once_with(cache_root=cache, dry_run=False)
            github.assert_not_called()
            self.assertEqual(selected, sdk.resolve())

    def test_termuxvoid_repo_setup_installs_glibc_and_adds_repo_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "usr"
            cache = root / "cache"
            source = prefix / "etc" / "apt" / "sources.list.d" / "termuxvoid.list"
            key = prefix / "etc" / "apt" / "trusted.gpg.d" / "termuxvoid.gpg"

            def fake_download(_url: str, destination: Path, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"key")
                return destination

            calls: list[list[str]] = []

            def fake_run(command, **_kwargs):
                calls.append([str(item) for item in command])
                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return Result()

            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build._termux_prefix_path", return_value=prefix
            ), patch("devtool.android_build._termux_package_installed", return_value=False), patch(
                "devtool.android_build.shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"pkg", "apt"} else None
            ), patch("devtool.android_build.download", side_effect=fake_download), patch(
                "devtool.android_build.run", side_effect=fake_run
            ):
                android_build.ensure_termuxvoid_repository(cache_root=cache, dry_run=False)

            self.assertEqual(source.read_text(), android_build.TERMUXVOID_REPOSITORY_LINE + "\n")
            self.assertEqual(key.read_bytes(), b"key")
            self.assertTrue(any("glibc-repo" in command for command in calls))
            self.assertTrue(any(command[1:3] == ["update", "-y"] for command in calls))


    def test_7z_provider_extract_retries_ndk_symlink_chain_with_snld(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "android-sdk.7z"
            archive.write_bytes(b"archive")
            staging = root / "stage"
            calls: list[list[str]] = []

            def fake_run(command, **_kwargs):
                rendered = [str(item) for item in command]
                calls.append(rendered)
                if rendered[1] == "t":
                    return __import__("subprocess").CompletedProcess(rendered, 0, "Everything is Ok\n", "")
                if "-snld" not in rendered:
                    return __import__("subprocess").CompletedProcess(
                        rendered,
                        2,
                        "",
                        "Dangerous link via another link was ignored : android-sdk/ndk/toolchains/llvm/prebuilt/linux-arm64/bin/clang++ : clang\n",
                    )
                bindir = staging / "android-sdk" / "ndk" / "toolchains" / "llvm" / "prebuilt" / "linux-arm64" / "bin"
                bindir.mkdir(parents=True, exist_ok=True)
                (bindir / "clang").write_text("clang", encoding="utf-8")
                (bindir / "clang++").symlink_to("clang")
                return __import__("subprocess").CompletedProcess(rendered, 0, "Everything is Ok\n", "")

            with patch("devtool.android_build.shutil.which", return_value="/usr/bin/7z"), patch(
                "devtool.android_build.run", side_effect=fake_run
            ):
                android_build._extract_sdk_provider_archive(archive, staging, dry_run=False)

            self.assertTrue(any("-snld" in command for command in calls))
            self.assertTrue((staging / "android-sdk" / "ndk" / "toolchains" / "llvm" / "prebuilt" / "linux-arm64" / "bin" / "clang++").is_symlink())

    def test_7z_provider_extract_falls_back_to_bsdtar_when_symlink_chain_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "android-sdk.7z"
            archive.write_bytes(b"archive")
            staging = root / "stage"
            calls: list[list[str]] = []

            def fake_which(name: str):
                if name == "7z":
                    return "/usr/bin/7z"
                if name == "bsdtar":
                    return "/usr/bin/bsdtar"
                return None

            def fake_run(command, **_kwargs):
                rendered = [str(item) for item in command]
                calls.append(rendered)
                if rendered[0].endswith("7z") and rendered[1] == "t":
                    return __import__("subprocess").CompletedProcess(rendered, 0, "Everything is Ok\n", "")
                if rendered[0].endswith("7z"):
                    return __import__("subprocess").CompletedProcess(
                        rendered,
                        2,
                        "",
                        "Dangerous link via another link was ignored : android-sdk/ndk/toolchains/llvm/prebuilt/linux-arm64/bin/clang++ : clang\n",
                    )
                bindir = staging / "android-sdk" / "ndk" / "toolchains" / "llvm" / "prebuilt" / "linux-arm64" / "bin"
                bindir.mkdir(parents=True, exist_ok=True)
                (bindir / "clang").write_text("clang", encoding="utf-8")
                (bindir / "clang++").symlink_to("clang")
                return __import__("subprocess").CompletedProcess(rendered, 0, "", "")

            with patch("devtool.android_build.shutil.which", side_effect=fake_which), patch(
                "devtool.android_build.run", side_effect=fake_run
            ):
                android_build._extract_sdk_provider_archive(archive, staging, dry_run=False)

            self.assertTrue(any(command[0].endswith("bsdtar") for command in calls))
            self.assertTrue((staging / "android-sdk" / "ndk" / "toolchains" / "llvm" / "prebuilt" / "linux-arm64" / "bin" / "clang++").is_symlink())

    def test_provider_symlink_validation_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stage"
            nested = root / "sdk" / "bin"
            nested.mkdir(parents=True)
            (nested / "bad").symlink_to("../../../outside")
            with self.assertRaisesRegex(android_build.BuildError, "escaping"):
                android_build._validate_provider_symlinks(root)

    def test_provider_symlink_validation_accepts_internal_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stage"
            nested = root / "sdk" / "bin"
            nested.mkdir(parents=True)
            (nested / "clang").write_text("clang", encoding="utf-8")
            (nested / "clang++").symlink_to("clang")
            (nested / "cc").symlink_to("clang++")
            android_build._validate_provider_symlinks(root)

    def test_provider_extraction_preflight_rejects_low_space_before_unpacking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "android-sdk.7z"
            archive.write_bytes(b"x" * 1024)
            with patch(
                "devtool.android_build.android_sdk_free_bytes", return_value=128 * 1024 * 1024
            ):
                with self.assertRaisesRegex(android_build.BuildError, "Insufficient free space"):
                    android_build._ensure_provider_extraction_space(
                        "termuxvoid", archive, root / "provider"
                    )

    def test_failed_provider_extraction_removes_partial_staging_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            release = {"tag_name": "36.0.0"}
            asset = {
                "name": "android-sdk.7z",
                "browser_download_url": "https://example.invalid/android-sdk.7z",
                "digest": "sha256:" + "a" * 64,
            }

            def fake_download(_url: str, destination: Path, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"archive")
                return destination

            def fail_extract(_archive: Path, staging: Path, **_kwargs):
                staging.mkdir(parents=True, exist_ok=True)
                (staging / "partial.bin").write_bytes(b"partial")
                raise android_build.BuildError("7z failed")

            with patch("devtool.android_build.download", side_effect=fake_download), patch(
                "devtool.android_build.verify_sha256"
            ), patch(
                "devtool.android_build._extract_sdk_provider_archive", side_effect=fail_extract
            ):
                with self.assertRaisesRegex(android_build.BuildError, "7z failed"):
                    android_build._install_provider_sdk_tree(
                        cache_root=cache,
                        provider="termuxvoid",
                        release=release,
                        asset=asset,
                        force=False,
                        dry_run=False,
                    )

            provider_root = cache / "android-sdk-providers" / "termuxvoid"
            self.assertEqual(list(provider_root.glob(".sdk-*.tmp")), [])

    def test_sdk_install_space_preflight_cleans_caches_then_fails_before_sdkmanager(self) -> None:
        gib = 1024 * 1024 * 1024
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cleanup_report = android_build.SpaceCleanupReport(
                free_before=100 * 1024 * 1024,
                free_after=300 * 1024 * 1024,
                reclaimed_bytes=200 * 1024 * 1024,
                removed=(),
                candidates=(),
            )
            with patch(
                "devtool.android_build.android_sdk_free_bytes",
                side_effect=[100 * 1024 * 1024, 300 * 1024 * 1024],
            ), patch(
                "devtool.android_build.clear_android_download_debris"
            ) as android_cache, patch(
                "devtool.android_build.clear_abandoned_android_provider_staging"
            ) as staging_cache, patch(
                "devtool.android_build.cleanup_android_space",
                return_value=cleanup_report,
            ) as space_cleanup:
                with self.assertRaisesRegex(android_build.BuildError, "Insufficient free space"):
                    android_build.ensure_android_sdk_install_space(
                        root / "sdk",
                        root / "cache",
                        ["platforms;android-36", "build-tools;36.0.0"],
                        dry_run=False,
                    )

            android_cache.assert_called_once()
            staging_cache.assert_called_once()
            space_cleanup.assert_called_once()
            self.assertGreater(
                android_build.android_sdk_install_working_space_bytes(
                    ["platforms;android-36", "build-tools;36.0.0"]
                ),
                gib,
            )

    def test_sdkmanager_enospc_is_not_retried(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            cache = root / "cache"
            partial = sdk / "build-tools" / "36.0.0"
            partial.mkdir(parents=True)
            (partial / "partial").write_text("x", encoding="utf-8")
            result = subprocess.CompletedProcess(
                ["sdkmanager"],
                1,
                "Warning: No space left on device\n",
                "",
            )

            with patch(
                "devtool.android_build.ensure_android_sdk_install_space"
            ), patch(
                "devtool.android_build.run_sdkmanager_with_progress", return_value=result
            ) as runner, patch(
                "devtool.android_build.clear_android_download_debris"
            ), patch(
                "devtool.android_build.clear_abandoned_android_provider_staging"
            ), patch(
                "devtool.android_build.clear_android_provider_download_cache"
            ), patch(
                "devtool.android_build.android_sdk_free_bytes", return_value=256 * 1024 * 1024
            ):
                with self.assertRaisesRegex(android_build.BuildError, "out of space"):
                    android_build.install_sdk_packages_with_repair(
                        Path("/tmp/sdkmanager"),
                        sdk,
                        ["build-tools;36.0.0"],
                        env={},
                        dry_run=False,
                        cache_root=cache,
                    )

            runner.assert_called_once()
            self.assertFalse(partial.exists())

    def test_auto_provider_prefers_termuxvoid_then_homuhomu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            homu = cache / "homu-sdk"
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch("devtool.android_build._provider_cached_sdk", return_value=None), patch(
                "devtool.android_build.provision_termuxvoid_sdk", side_effect=android_build.BuildError("broken")
            ) as tv, patch("devtool.android_build.provision_homuhomu_sdk", return_value=homu) as hh, patch(
                "devtool.android_build._clear_failed_provider_cache"
            ) as cleanup:
                root, source = android_build.provision_android_sdk_provider(
                    "auto", cache, force=False, dry_run=False, downloads="auto", network="allow"
                )
        self.assertEqual(root, homu)
        self.assertEqual(source, "HomuHomu ARM64 Android SDK")
        tv.assert_called_once()
        hh.assert_called_once()
        cleanup.assert_called_once_with(cache, "termuxvoid")

    def test_termux_repair_capability_requires_every_native_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "36.0.0").mkdir(parents=True)
            available = Path(tmp) / "bin"
            available.mkdir()
            aapt2 = available / "aapt2"
            aapt2.write_text("tool", encoding="utf-8")

            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch(
                "devtool.android_build.installed_build_tools_versions", return_value=["36.0.0"]
            ), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed",
                return_value=["aapt2", "zipalign"],
            ), patch(
                "devtool.android_build.termux_tool_path",
                side_effect=lambda name: aapt2 if name == "aapt2" else None,
            ), patch(
                "devtool.android_build.native_android_build_tool_compatible", return_value=True
            ):
                self.assertFalse(android_build.termux_can_repair_sdk_build_tools(sdk))

    def test_termux_repair_capability_does_not_count_future_packages_or_other_sdk_donors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk"
            (sdk / "build-tools" / "36.0.0").mkdir(parents=True)
            available = root / "bin"
            available.mkdir()
            aapt2 = available / "aapt2"
            aapt2.write_text("tool", encoding="utf-8")
            donor = root / "prefix" / "opt" / "android-sdk" / "build-tools" / "36.0.0" / "zipalign"
            donor.parent.mkdir(parents=True)
            donor.write_text("tool", encoding="utf-8")

            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch(
                "devtool.android_build.installed_build_tools_versions", return_value=["36.0.0"]
            ), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed",
                return_value=["aapt2", "zipalign"],
            ), patch(
                "devtool.android_build.termux_tool_path",
                side_effect=lambda name: aapt2 if name == "aapt2" else None,
            ), patch(
                "devtool.android_build.native_android_build_tool_compatible", return_value=True
            ), patch(
                "devtool.android_build._termux_package_available", return_value=True
            ), patch(
                "devtool.android_build._termux_prefix_path", return_value=root / "prefix"
            ):
                self.assertFalse(android_build.termux_can_repair_sdk_build_tools(sdk))

    def test_termux_repair_capability_accepts_complete_native_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk"
            (sdk / "build-tools" / "36.0.0").mkdir(parents=True)
            tool = Path(tmp) / "tool"
            tool.write_text("tool", encoding="utf-8")

            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch(
                "devtool.android_build.installed_build_tools_versions", return_value=["36.0.0"]
            ), patch(
                "devtool.android_build.arm64_build_tools_replacements_needed",
                return_value=["aidl", "zipalign", "split-select"],
            ), patch(
                "devtool.android_build.termux_tool_path", return_value=tool
            ), patch(
                "devtool.android_build.native_android_build_tool_compatible", return_value=True
            ):
                self.assertTrue(android_build.termux_can_repair_sdk_build_tools(sdk))

    def test_bootstrap_policy_blocks_external_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(android_build.BuildError):
                android_build.provision_android_sdk_provider(
                    "termuxvoid", Path(tmp), force=False, dry_run=False, downloads="never", network="deny"
                )

    def test_android_builder_forwards_configured_sdk_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle").write_text("plugins { id 'com.android.application' }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, kind="android")
            config.project_root = root
            config.runner_options["android"] = {"sdk_provider": "homuhomu"}
            config.runner_options["gradle"] = {}
            config.runner_options["bootstrap"] = {"downloads": "auto", "network": "allow"}
            with patch("devtool.android_integration.android_project_root", return_value=root), patch(
                "devtool.android_integration._android_execution_mode", return_value="native-termux"
            ):
                argv = android_integration.android_builder_arguments_for_tasks(
                    config, ["assembleDebug"], scan_artifacts=False
                )
        index = argv.index("--sdk-provider")
        self.assertEqual(argv[index + 1], "homuhomu")


    def test_space_candidates_prune_downloads_and_backups_but_preserve_active_ndk_and_qemu_sysroot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            root = Path(tmp)
            cache = root / "gradle-build"
            provider = cache / "android-sdk-providers" / "homuhomu"
            sdk = provider / "sdk"
            (sdk / "build-tools").mkdir(parents=True)
            (sdk / "platform-tools").mkdir(parents=True)
            downloads = provider / "downloads"
            downloads.mkdir(parents=True)
            (downloads / "sdk.tar.xz").write_bytes(b"x" * 4096)

            managed = cache / "android-sdk-arm64"
            canonical = managed / "build-tools" / "36.0.0"
            (canonical / "lib").mkdir(parents=True)
            (canonical / "source.properties").write_text("Pkg.Revision = 36.0.0\n", encoding="utf-8")
            (canonical / "lib" / "d8.jar").write_bytes(b"jar")
            backup = managed / ".devtool-noncanonical-build-tools" / "36.0.0-5"
            backup.mkdir(parents=True)
            (backup / "old").write_bytes(b"old")
            ndk = managed / "ndk" / "28.2.13676358"
            ndk.mkdir(parents=True)
            (ndk / "keep").write_bytes(b"ndk")
            qemu = cache / "qemu" / "x86_64-sysroot" / "rootfs"
            qemu.mkdir(parents=True)
            (qemu / "keep").write_bytes(b"qemu")
            qemu_downloads = cache / "qemu" / "downloads"
            qemu_downloads.mkdir(parents=True)
            (qemu_downloads / "ubuntu.tar.gz").write_bytes(b"archive")

            with patch.dict("os.environ", {"HOME": home}, clear=False):
                candidates = android_build.collect_android_space_candidates(cache)

        paths = {item.path for item in candidates}
        self.assertIn(downloads.resolve(), paths)
        self.assertIn(backup.resolve(), paths)
        self.assertIn(qemu_downloads.resolve(), paths)
        self.assertNotIn(ndk.resolve(), paths)
        self.assertFalse(any(ndk.resolve() in item.path.parents for item in candidates))
        self.assertNotIn(qemu.resolve(), paths)

    def test_cleanup_android_space_removes_safe_candidates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            cache = Path(tmp) / "gradle-build"
            provider = cache / "android-sdk-providers" / "homuhomu"
            sdk = provider / "sdk"
            (sdk / "build-tools").mkdir(parents=True)
            (sdk / "platform-tools").mkdir(parents=True)
            downloads = provider / "downloads"
            downloads.mkdir(parents=True)
            (downloads / "sdk.tar.xz").write_bytes(b"x" * 2048)
            ndk = cache / "android-sdk-arm64" / "ndk" / "28.2.13676358"
            ndk.mkdir(parents=True)
            (ndk / "keep").write_bytes(b"keep")

            with patch.dict("os.environ", {"HOME": home}, clear=False):
                report = android_build.cleanup_android_space(cache, target_free_bytes=None)

            self.assertFalse(downloads.exists())
            self.assertTrue(ndk.exists())
            self.assertGreaterEqual(report.reclaimed_bytes, 2048)
            self.assertTrue(any(item.category == "provider-download" for item in report.removed))

    def test_space_cleanup_never_prunes_gradle_user_cache_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            cache = Path(tmp) / "gradle-build"
            gradle_home = Path(home) / ".gradle"
            old = gradle_home / "caches" / "9.4.1"
            new = gradle_home / "caches" / "9.7.0"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / "old.bin").write_bytes(b"old")
            (new / "new.bin").write_bytes(b"new")

            without = android_build.collect_android_space_candidates(
                cache, gradle_user_home=gradle_home, include_gradle_user_cache=False
            )
            with_opt_in = android_build.collect_android_space_candidates(
                cache, gradle_user_home=gradle_home, include_gradle_user_cache=True
            )

        self.assertNotIn(old.resolve(), {item.path for item in without})
        self.assertIn(old.resolve(), {item.path for item in with_opt_in})
        self.assertNotIn(new.resolve(), {item.path for item in with_opt_in})

    def test_space_cleanup_keeps_newest_managed_gradle_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "gradle-build"
            gradle = cache / "gradle"
            for version in ("8.13", "9.5.0", "9.7.0"):
                path = gradle / f"gradle-{version}"
                path.mkdir(parents=True)
                (path / "bin").write_bytes(version.encode())
            candidates = android_build.collect_android_space_candidates(
                cache, managed_gradle_retention=2
            )

        managed = {item.path.name for item in candidates if item.category == "managed-gradle"}
        self.assertEqual(managed, {"gradle-8.13"})

    def test_space_candidates_skip_staging_owned_by_live_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "gradle-build"
            staging = cache / "android-sdk-providers" / "termuxvoid" / f".sdk-{__import__('os').getpid()}.tmp"
            staging.mkdir(parents=True)
            (staging / "payload").write_bytes(b"active")
            candidates = android_build.collect_android_space_candidates(cache)

        self.assertNotIn(staging.resolve(), {item.path for item in candidates})

    def test_auto_space_management_only_runs_below_trigger(self) -> None:
        gib = 1024 * 1024 * 1024
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "gradle-build"
            report = android_build.SpaceCleanupReport(
                free_before=2 * gib,
                free_after=6 * gib,
                reclaimed_bytes=4 * gib,
                removed=(),
                candidates=(),
            )
            with patch("devtool.android_build.android_sdk_free_bytes", return_value=8 * gib), patch(
                "devtool.android_build.cleanup_android_space", return_value=report
            ) as cleanup:
                result = android_build.auto_manage_android_space(
                    cache, min_free_mb=4096, target_free_mb=6144
                )
                self.assertIsNone(result)
                cleanup.assert_not_called()

            with patch("devtool.android_build.android_sdk_free_bytes", return_value=2 * gib), patch(
                "devtool.android_build.cleanup_android_space", return_value=report
            ) as cleanup:
                result = android_build.auto_manage_android_space(
                    cache, min_free_mb=4096, target_free_mb=6144
                )
                self.assertIs(result, report)
                self.assertEqual(cleanup.call_args.kwargs["target_free_bytes"], 6 * gib)

    def test_selected_provider_download_is_removed_after_sdk_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "gradle-build"
            sdk = cache / "android-sdk-providers" / "homuhomu" / "sdk"
            downloads = cache / "android-sdk-providers" / "homuhomu" / "downloads"
            sdk.mkdir(parents=True)
            downloads.mkdir(parents=True)
            (downloads / "sdk.tar.xz").write_bytes(b"archive")

            android_build.clear_selected_provider_download(cache, sdk, dry_run=False)

            self.assertFalse(downloads.exists())

    def test_android_builder_forwards_storage_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle").write_text("plugins { id 'com.android.application' }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, kind="android")
            config.project_root = root
            config.runner_options["android"] = {
                "storage": {
                    "auto_cleanup": "off",
                    "min_free_mb": 2048,
                    "target_free_mb": 4096,
                    "managed_gradle_retention": 1,
                    "manage_gradle_user_cache": True,
                    "keep_provider_downloads": True,
                    "cleanup_after_build": False,
                }
            }
            config.runner_options["gradle"] = {}
            config.runner_options["bootstrap"] = {"downloads": "auto", "network": "allow"}
            with patch("devtool.android_integration.android_project_root", return_value=root), patch(
                "devtool.android_integration._android_execution_mode", return_value="native-termux"
            ):
                argv = android_integration.android_builder_arguments_for_tasks(
                    config, ["assembleDebug"], scan_artifacts=False
                )

        self.assertEqual(argv[argv.index("--space-cleanup") + 1], "off")
        self.assertEqual(argv[argv.index("--space-min-free-mb") + 1], "2048")
        self.assertEqual(argv[argv.index("--space-target-free-mb") + 1], "4096")
        self.assertEqual(argv[argv.index("--managed-gradle-retention") + 1], "1")
        self.assertIn("--manage-gradle-user-cache", argv)
        self.assertIn("--keep-provider-downloads", argv)
        self.assertIn("--no-post-build-space-cleanup", argv)

    def test_android_builder_parser_accepts_space_management_options(self) -> None:
        args = android_build.parse_args([
            ".",
            "--space-cleanup", "safe",
            "--space-min-free-mb", "3072",
            "--space-target-free-mb", "6144",
            "--managed-gradle-retention", "1",
            "--manage-gradle-user-cache",
            "--keep-provider-downloads",
            "--no-post-build-space-cleanup",
        ])
        self.assertEqual(args.space_cleanup, "safe")
        self.assertEqual(args.space_min_free_mb, 3072)
        self.assertEqual(args.space_target_free_mb, 6144)
        self.assertEqual(args.managed_gradle_retention, 1)
        self.assertTrue(args.manage_gradle_user_cache)
        self.assertTrue(args.keep_provider_downloads)
        self.assertFalse(args.post_build_space_cleanup)


if __name__ == "__main__":
    unittest.main()
