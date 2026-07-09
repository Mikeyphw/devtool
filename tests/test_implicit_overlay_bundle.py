from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from devtool.artifacts import (
    is_overlay_bundle,
    materialize_overlay_bundle_entries,
    read_overlay_bundle_manifest,
)
from devtool.cli import main


class ImplicitOverlayBundleTests(unittest.TestCase):
    def _overlay_bytes(self, name: str, content: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("payload.txt", content)
            return path.read_bytes()

    def test_wrapper_with_only_nested_archives_is_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "bundle.zip"
            with zipfile.ZipFile(wrapper, "w") as zf:
                zf.writestr("02_second_overlay.zip", self._overlay_bytes("second.zip", "two"))
                zf.writestr("01_first_overlay.zip", self._overlay_bytes("first.zip", "one"))

            self.assertTrue(is_overlay_bundle(wrapper))
            manifest = read_overlay_bundle_manifest(wrapper)
            self.assertTrue(manifest.get("implicit"))
            self.assertEqual(
                [item["path"] for item in manifest["apply_order"]],
                ["01_first_overlay.zip", "02_second_overlay.zip"],
            )

            out = root / "out"
            _manifest, entries = materialize_overlay_bundle_entries(wrapper, out)
            self.assertEqual([entry.bundle_path for entry in entries], ["01_first_overlay.zip", "02_second_overlay.zip"])
            self.assertTrue(all(entry.path.exists() for entry in entries))

    def test_archive_payload_plus_normal_file_is_not_implicit_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "overlay.zip"
            with zipfile.ZipFile(wrapper, "w") as zf:
                zf.writestr("assets/reference.zip", self._overlay_bytes("ref.zip", "ref"))
                zf.writestr("src/file.txt", "normal payload")
            self.assertFalse(is_overlay_bundle(wrapper))

    def test_apply_overlay_expands_wrapper_instead_of_copying_inner_zips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            first = root / "01_first.zip"
            second = root / "02_second.zip"
            with zipfile.ZipFile(first, "w") as zf:
                zf.writestr("first.txt", "one")
            with zipfile.ZipFile(second, "w") as zf:
                zf.writestr("second.txt", "two")
            wrapper = root / "bundle.zip"
            with zipfile.ZipFile(wrapper, "w") as zf:
                zf.writestr(first.name, first.read_bytes())
                zf.writestr(second.name, second.read_bytes())

            rc = main(["--repo", str(repo), "--yes", "apply-overlay", "--keep-artifact", str(wrapper)])

            self.assertEqual(rc, 0)
            self.assertEqual((repo / "first.txt").read_text(encoding="utf-8"), "one")
            self.assertEqual((repo / "second.txt").read_text(encoding="utf-8"), "two")
            self.assertFalse((repo / first.name).exists())
            self.assertFalse((repo / second.name).exists())


if __name__ == "__main__":
    unittest.main()
