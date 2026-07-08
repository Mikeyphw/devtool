from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch

from devtool.cli import main
from devtool.transactions import create_apply_transaction, find_active_transaction, find_active_transactions, transaction_root


class TransactionalApplyTests(unittest.TestCase):
    def _write_generic_config(self, root: Path, *, restore: str = "exit 0", build: str = "exit 0", test: str = "exit 0") -> None:
        root.joinpath(".devtool.toml").write_text(
            "[project]\nkind='generic'\n"
            f"restore_command='bash -lc \"{restore}\"'\n"
            f"build_command='bash -lc \"{build}\"'\n"
            f"test_command='bash -lc \"{test}\"'\n",
            encoding="utf-8",
        )

    def test_apply_overlay_validate_failure_restores_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            self._write_generic_config(root, restore="exit 1")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
                zf.writestr("new.txt", "added\n")

            with patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--yes", "--no-clipboard", "apply-overlay", "--validate", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((root / "new.txt").exists())
            self.assertIsNone(find_active_transaction(root))
            self.assertFalse((root / ".devtool" / "transactions").exists())
            self.assertFalse(any(transaction_root(root).glob("*/backup")))

    def test_stale_apply_transaction_is_restored_before_next_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            self._write_generic_config(root)
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
                zf.writestr("new.txt", "added\n")

            preview = {"changes": [{"kind": "modify", "path": "file.txt"}, {"kind": "add", "path": "new.txt"}], "counts": {"modify": 1, "add": 1}}
            create_apply_transaction(root, artifact, artifact_type="overlay", preview=preview)
            # Simulate a killed process. Live transactions from another devtool
            # run must not be rolled back by concurrent commands.
            transaction_path = find_active_transactions(root, include_live=True)[0]
            transaction = __import__("json").loads(transaction_path.read_text(encoding="utf-8"))
            transaction["pid"] = 99999999
            transaction["process_start"] = "dead"
            transaction_path.write_text(__import__("json").dumps(transaction), encoding="utf-8")
            marker = next((transaction_root(root) / "active").glob("*.json"))
            marker_data = __import__("json").loads(marker.read_text(encoding="utf-8"))
            marker_data["pid"] = 99999999
            marker_data["process_start"] = "dead"
            marker.write_text(__import__("json").dumps(marker_data), encoding="utf-8")
            (root / "file.txt").write_text("new\n", encoding="utf-8")
            (root / "new.txt").write_text("added\n", encoding="utf-8")

            with patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--yes", "--no-clipboard", "restore"])

            self.assertEqual(rc, 1)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((root / "new.txt").exists())
            self.assertIsNone(find_active_transaction(root))
            self.assertFalse((root / ".devtool" / "transactions").exists())
            self.assertFalse(any(transaction_root(root).glob("*/backup")))

    def test_delete_manifest_paths_are_transactional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "keep.txt").write_text("keep\n", encoding="utf-8")
            (root / "remove-me.txt").write_text("old-delete-target\n", encoding="utf-8")
            self._write_generic_config(root, restore="exit 1")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("keep.txt", "changed\n")
                zf.writestr(".devtool-delete", "remove-me.txt\n")

            with patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--yes", "--no-clipboard", "apply-overlay", "--validate", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertEqual((root / "keep.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertEqual((root / "remove-me.txt").read_text(encoding="utf-8"), "old-delete-target\n")
            self.assertIsNone(find_active_transaction(root))
            self.assertFalse(any(transaction_root(root).glob("*/backup")))


if __name__ == "__main__":
    unittest.main()
