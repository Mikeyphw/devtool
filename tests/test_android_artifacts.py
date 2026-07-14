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
