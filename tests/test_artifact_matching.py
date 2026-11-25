from __future__ import annotations

import os
import hashlib
import shutil
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

    def test_resolve_preserves_exact_named_archive_despite_newer_siblings(self) -> None:
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

            self.assertEqual(resolve_artifact_path(plain), plain.resolve())

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

    def test_missing_name_uses_pinned_legacy_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            requested = root / "artifact.zip"
            self._zip(root / "artifact(1).zip", payload_text="one")
            self._zip(root / "artifact_2.zip", payload_text="two")
            self._zip(root / "artifact3.zip", payload_text="three")
            chosen = self._zip(root / "artifact-4.zip", payload_text="four")
            digest = hashlib.sha256(chosen.read_bytes()).hexdigest()
            self.assertEqual(resolve_artifact_path(requested, expected_sha256=digest), chosen)
            self.assertEqual(resolve_artifact_path(requested), root / "artifact(1).zip")
            with self.assertRaisesRegex(ValueError, "matches the expected SHA-256"):
                resolve_artifact_path(requested, expected_sha256="a" * 64)

    def test_stage_siblings_never_substitute_for_exact_or_missing_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._zip(root / "stage-01.zip", payload_text="one")
            fourth = self._zip(root / "stage-04.zip", payload_text="four")
            digest = hashlib.sha256(fourth.read_bytes()).hexdigest()
            self.assertEqual(resolve_artifact_path(first), first)
            with self.assertRaisesRegex(ValueError, "exact artifact SHA-256 mismatch"):
                resolve_artifact_path(first, expected_sha256=digest)
            first.unlink()
            self.assertEqual(resolve_artifact_path(first), first)
            with self.assertRaises(FileNotFoundError):
                resolve_artifact_path(first, expected_sha256=digest)
            self.assertEqual(resolve_artifact_path(fourth), fourth)

    def test_ambiguous_android_downloads_require_explicit_choice(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wanted = root / "artifact.zip"
            first = self._zip(root / "artifact(1).zip", payload_text="one")
            second = self._zip(root / "artifact(2).zip", payload_text="two")
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                resolve_artifact_path(wanted)
            digest = hashlib.sha256(second.read_bytes()).hexdigest()
            self.assertEqual(resolve_artifact_path(wanted, expected_sha256=digest), second)
            shutil.copyfile(first, second)
            self.assertEqual(resolve_artifact_path(wanted), first)

    def test_symlinked_duplicates_are_never_selected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._zip(root / "separate.zip")
            wanted = root / "artifact.zip"
            alias = root / "artifact(1).zip"
            alias.symlink_to(source)
            self.assertEqual(resolve_artifact_path(wanted), wanted)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                resolve_artifact_path(alias)

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
            self.assertEqual((root / "chosen.txt").read_text(encoding="utf-8"), "plain")
            # RMP-C02: selection of one variant does not authorize deletion
            # of other downloads (which can have unrelated contents).
            self.assertFalse(requested.exists())
            self.assertTrue(unused.exists())
            self.assertTrue(chosen.exists())
            self.assertTrue((root / "artifact.zip.devtool.json").exists())
            self.assertTrue((root / "artifact_1.zip.devtool.json").exists())
            self.assertTrue((root / "artifact-2.zip.devtool.json").exists())


if __name__ == "__main__":
    unittest.main()
