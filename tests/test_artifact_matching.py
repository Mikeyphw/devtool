from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from devtool.artifacts import artifact_cleanup_paths, resolve_artifact_path
from devtool.cli import main


class ArtifactMatchingTests(unittest.TestCase):
    def _zip(self, path: Path, payload_name: str = "payload.txt", payload_text: str = "ok") -> Path:
        with ZipFile(path, "w") as zf:
            zf.writestr(payload_name, payload_text)
        return path

    def test_resolve_prefers_highest_numeric_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = self._zip(root / "s3oc_debug_full_system_overlay.zip", payload_text="plain")
            low = self._zip(root / "s3oc_debug_full_system_overlay(1).zip", payload_text="one")
            high = self._zip(root / "s3oc_debug_full_system_overlay-4.zip", payload_text="four")
            older = time.time() - 100
            newer = time.time()
            os.utime(plain, (older, older))
            os.utime(low, (older + 1, older + 1))
            os.utime(high, (newer, newer))

            self.assertEqual(resolve_artifact_path(plain), high.resolve())

    def test_resolve_prefers_unsuffixed_when_it_is_newer_than_highest_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = self._zip(root / "s3oc_debug_full_system_overlay.zip", payload_text="plain")
            high = self._zip(root / "s3oc_debug_full_system_overlay-4.zip", payload_text="four")
            older = time.time() - 100
            newer = time.time()
            os.utime(high, (older, older))
            os.utime(plain, (newer, newer))

            self.assertEqual(resolve_artifact_path(plain), plain.resolve())

    def test_resolve_accepts_all_duplicate_suffix_forms(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            requested = root / "s3oc_debug_full_system_overlay.zip"
            self._zip(root / "s3oc_debug_full_system_overlay(1).zip")
            self._zip(root / "s3oc_debug_full_system_overlay_2.zip")
            self._zip(root / "s3oc_debug_full_system_overlay3.zip")
            high = self._zip(root / "s3oc_debug_full_system_overlay-4.zip")

            self.assertEqual(resolve_artifact_path(requested), high.resolve())

    def test_cleanup_paths_include_entire_duplicate_family_and_sidecars(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = self._zip(root / "artifact.zip")
            paren = self._zip(root / "artifact(1).zip")
            dashed = self._zip(root / "artifact-2.zip")
            for archive in (plain, paren, dashed):
                archive.with_name(archive.name + ".devtool.json").write_text("{}", encoding="utf-8")

            cleanup_names = {path.name for path in artifact_cleanup_paths(plain)}

            for name in {
                "artifact.zip",
                "artifact.zip.devtool.json",
                "artifact(1).zip",
                "artifact(1).zip.devtool.json",
                "artifact-2.zip",
                "artifact-2.zip.devtool.json",
            }:
                self.assertIn(name, cleanup_names)

    def test_apply_overlay_deletes_only_consumed_variant(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            requested = self._zip(root / "artifact.zip", payload_name="chosen.txt", payload_text="plain")
            unused = self._zip(root / "artifact_1.zip", payload_name="chosen.txt", payload_text="one")
            chosen = self._zip(root / "artifact-2.zip", payload_name="chosen.txt", payload_text="two")
            requested.with_name(requested.name + ".devtool.json").write_text("{}", encoding="utf-8")
            unused.with_name(unused.name + ".devtool.json").write_text("{}", encoding="utf-8")
            chosen.with_name(chosen.name + ".devtool.json").write_text("{}", encoding="utf-8")
            old = time.time() - 100
            now = time.time()
            os.utime(requested, (old, old))
            os.utime(unused, (old + 1, old + 1))
            os.utime(chosen, (now, now))

            rc = main(["--repo", str(root), "--yes", "apply-overlay", str(requested)])

            self.assertEqual(rc, 0)
            self.assertEqual((root / "chosen.txt").read_text(encoding="utf-8"), "two")
            # RMP-C02: selection of one variant does not authorize deletion
            # of other downloads (which can have unrelated contents).
            self.assertTrue(requested.exists())
            self.assertTrue(unused.exists())
            self.assertFalse(chosen.exists())
            self.assertTrue((root / "artifact.zip.devtool.json").exists())
            self.assertTrue((root / "artifact_1.zip.devtool.json").exists())
            self.assertTrue((root / "artifact-2.zip.devtool.json").exists())


if __name__ == "__main__":
    unittest.main()
