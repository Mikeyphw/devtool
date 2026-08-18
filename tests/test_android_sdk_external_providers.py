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
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build._termux_prefix_path", return_value=prefix
            ), patch("devtool.android_build.sdk_host_compatible", return_value=True):
                found, source = android_build.discover_sdk_root_with_source(None, None)
        self.assertEqual(found, sdk.resolve())
        self.assertEqual(source, "TermuxVoid android-sdk")

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

    def test_auto_provider_prefers_termuxvoid_then_homuhomu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            homu = cache / "homu-sdk"
            with patch("devtool.android_build.running_under_termux_python", return_value=True), patch(
                "devtool.android_build.normalized_machine", return_value="arm64"
            ), patch("devtool.android_build._provider_cached_sdk", return_value=None), patch(
                "devtool.android_build.provision_termuxvoid_sdk", side_effect=android_build.BuildError("broken")
            ) as tv, patch("devtool.android_build.provision_homuhomu_sdk", return_value=homu) as hh:
                root, source = android_build.provision_android_sdk_provider(
                    "auto", cache, force=False, dry_run=False, downloads="auto", network="allow"
                )
        self.assertEqual(root, homu)
        self.assertEqual(source, "HomuHomu ARM64 Android SDK")
        tv.assert_called_once()
        hh.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
