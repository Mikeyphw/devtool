from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

from devtool.android_artifacts import collect_and_verify_android_artifacts, verify_android_artifact
from devtool.models import ProjectConfig


class AndroidArtifactVerificationTests(unittest.TestCase):
    def _apk(self, root: Path, name: str = "app-debug.apk") -> Path:
        path = root / "app" / "build" / "outputs" / "apk" / "debug" / name
        path.parent.mkdir(parents=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("classes.dex", b"dex")
        return path

    def _apk_at(self, root: Path, relative: str, *, manifest: bool = True) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            if manifest:
                archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("classes.dex", b"dex")
        return path

    def test_apk_structural_verification_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = self._apk(root)
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                record, issues = verify_android_artifact(apk, repo_root=root, project_root=root)

        self.assertTrue(record.valid)
        self.assertEqual(record.type, "apk")
        self.assertEqual(record.variant, "debug")
        self.assertEqual(len(record.sha256), 64)
        self.assertFalse(issues)
        self.assertIn("zip_integrity", {item["name"] for item in record.checks})

    def test_unsigned_release_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = self._apk(root, "app-release-unsigned.apk")
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                record, issues = verify_android_artifact(apk, repo_root=root, project_root=root)

        self.assertFalse(record.valid)
        self.assertTrue(any(issue.code == "RELEASE_SIGNING" for issue in issues))

    def test_collection_attaches_normalized_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._apk(root).touch()
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="android", runner_options={"artifacts": {"required": True}})
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                metadata, issues = collect_and_verify_android_artifacts(config, project_root=root, started_at=0)

        self.assertEqual(metadata["count"], 1)
        self.assertEqual(metadata["valid"], 1)
        self.assertFalse(issues)

    def test_required_collection_accepts_up_to_date_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = self._apk(root)
            old_mtime = apk.stat().st_mtime
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"artifacts": {"required": True}},
            )
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                metadata, issues = collect_and_verify_android_artifacts(
                    config, project_root=root, started_at=old_mtime + 60.0
                )

        self.assertEqual(metadata["count"], 1)
        self.assertEqual(metadata["valid"], 1)
        self.assertTrue(metadata["used_cached_outputs"])
        self.assertFalse(issues)


    def test_chroot_collection_skips_host_sdk_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._apk(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                execution_environment="chroot",
                runner_options={"artifacts": {"required": True}},
            )
            with patch("devtool.android_artifacts._tool_from_sdk") as tool_lookup:
                metadata, issues = collect_and_verify_android_artifacts(
                    config, project_root=root, started_at=0
                )

        self.assertEqual(metadata["valid"], 1)
        self.assertEqual(metadata["invalid"], 0)
        self.assertFalse(metadata["external_verification"]["executed"])
        self.assertIn("chroot", metadata["external_verification"]["skip_reason"])
        tool_lookup.assert_not_called()
        self.assertFalse(issues)

    def test_debug_badging_failure_is_nonfatal_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = self._apk(root)

            def find_tool(name: str):
                return "/fake/aapt2" if name == "aapt2" else None

            with (
                patch("devtool.android_artifacts._tool_from_sdk", side_effect=find_tool),
                patch("devtool.android_artifacts._run_tool", return_value=(1, "badging unavailable")),
            ):
                record, issues = verify_android_artifact(
                    apk, repo_root=root, project_root=root
                )

        self.assertTrue(record.valid)
        self.assertEqual([issue.severity for issue in issues], ["warning"])
        self.assertEqual(issues[0].code, "AAPT2_BADGING")

    def test_release_signature_failure_remains_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = self._apk(root, "app-release.apk")

            def find_tool(name: str):
                return "/fake/apksigner" if name == "apksigner" else None

            with (
                patch("devtool.android_artifacts._tool_from_sdk", side_effect=find_tool),
                patch("devtool.android_artifacts._run_tool", return_value=(1, "DOES NOT VERIFY")),
            ):
                record, issues = verify_android_artifact(
                    apk, repo_root=root, project_root=root
                )

        self.assertFalse(record.valid)
        self.assertTrue(any(issue.code == "SIGNATURE" and issue.severity == "error" for issue in issues))

    def test_required_external_verification_fails_when_tools_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._apk(root)
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={
                    "artifacts": {
                        "required": True,
                        "external_verification": "required",
                    }
                },
            )
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                metadata, issues = collect_and_verify_android_artifacts(
                    config, project_root=root, started_at=0
                )

        self.assertEqual(metadata["invalid"], 1)
        self.assertTrue(any(issue.code == "AAPT2_BADGING" for issue in issues))
        self.assertTrue(any(issue.code == "SIGNATURE" for issue in issues))

    def test_cached_fallback_ignores_unrequested_android_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            official = self._apk_at(root, "app/build/outputs/apk/official/debug/app-official-debug.apk")
            spoofed = self._apk_at(
                root,
                "app/build/outputs/apk/spoofed/debug/app-spoofed-debug.apk",
                manifest=False,
            )
            started_at = max(official.stat().st_mtime, spoofed.stat().st_mtime) + 60.0
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="android",
                runner_options={"artifacts": {"required": True}},
            )
            with patch("devtool.android_artifacts._tool_from_sdk", return_value=None):
                metadata, issues = collect_and_verify_android_artifacts(
                    config,
                    project_root=root,
                    started_at=started_at,
                    tasks=[":app:assembleOfficialDebug", ":app:testOfficialDebugUnitTest", ":app:lintOfficialDebug"],
                )

        self.assertEqual(metadata["count"], 1)
        self.assertEqual(metadata["valid"], 1)
        self.assertTrue(metadata["used_cached_outputs"])
        self.assertEqual(metadata["items"][0]["path"], "app/build/outputs/apk/official/debug/app-official-debug.apk")
        self.assertFalse(issues)
