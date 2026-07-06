from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from devtool.artifacts import (
    create_overlay_bundle,
    list_archive_members,
    read_artifact_manifest,
    validate_artifact,
    write_archive_entries,
)
from devtool.cli import build_parser, main
from devtool.runners import apply_overlay_bundle


class ArchiveBundleSupportTests(unittest.TestCase):
    def _zip_overlay(self, path: Path, payload_name: str, payload_text: str) -> Path:
        with ZipFile(path, "w") as zf:
            zf.writestr(payload_name, payload_text)
            zf.writestr("devtool-artifact.json", json.dumps({"type": "overlay", "files": [payload_name]}))
        return path

    def test_archive_reader_supports_tar_xz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "overlay.tar.xz"
            write_archive_entries(archive, {"file.txt": b"ok"})
            members = list_archive_members(archive, include_data=True)
            self.assertEqual(members[0].name, "file.txt")
            self.assertEqual(members[0].data, b"ok")

    def test_archive_reader_supports_tar_zst_when_zstd_exists(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd command is not available")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "overlay.tar.zst"
            write_archive_entries(archive, {"file.txt": b"ok-zst"})
            members = list_archive_members(archive, include_data=True)
            self.assertEqual(members[0].name, "file.txt")
            self.assertEqual(members[0].data, b"ok-zst")

    def test_overlay_bundle_validates_and_applies_transactionally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            first = self._zip_overlay(root / "001.zip", "a.txt", "one")
            second = self._zip_overlay(root / "002.zip", "b.txt", "two")
            bundle = create_overlay_bundle(root / "bundle.tar.xz", [first, second], name="bundle")

            validation = validate_artifact(repo, bundle, artifact_type="overlay_bundle")
            self.assertTrue(validation.ok, validation.messages)

            result = apply_overlay_bundle(repo, str(bundle))
            self.assertEqual(result.returncode, 0, result.output)
            self.assertEqual((repo / "a.txt").read_text(encoding="utf-8"), "one")
            self.assertEqual((repo / "b.txt").read_text(encoding="utf-8"), "two")
            self.assertIn("Transaction backup:", result.output)

    def test_nested_apply_overlays_parser_normalizes_to_apply_overlays(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "overlays", "bundle.zip"])
        from devtool.cli import _normalize_command_aliases
        _normalize_command_aliases(args)
        self.assertEqual(args.command, "apply-overlays")
        self.assertEqual(args.artifact, "bundle.zip")

    def test_bundle_command_creates_tar_gz_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._zip_overlay(root / "001.zip", "a.txt", "one")
            out = root / "bundle.tar.gz"
            rc = main(["bundle", "overlays", "--out", str(out), str(first)])
            self.assertEqual(rc, 0)
            manifest = read_artifact_manifest(out)
            self.assertEqual(manifest.get("type"), "overlay_bundle")
            self.assertEqual(manifest.get("kind"), "devtool.overlay_bundle")


if __name__ == "__main__":
    unittest.main()
