from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch

from devtool.cli import main
from devtool.transactions import (
    abandon_transaction,
    create_apply_transaction,
    find_active_transaction,
    find_active_transactions,
    read_transaction,
    record_transaction_git_commit,
    rollback_transaction,
    transaction_root,
)


class TransactionalApplyTests(unittest.TestCase):
    def _write_generic_config(self, root: Path, *, restore: str = "exit 0", build: str = "exit 0", test: str = "exit 0") -> None:
        root.joinpath(".devtool.toml").write_text(
            "[project]\nkind='generic'\n"
            f"restore_command='bash -lc \"{restore}\"'\n"
            f"build_command='bash -lc \"{build}\"'\n"
            f"test_command='bash -lc \"{test}\"'\n",
            encoding="utf-8",
        )


    def _init_git_repo(self, root: Path) -> str:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "devtool-tests@example.invalid"], cwd=root, check=True)
        (root / "file.txt").write_text("old\n", encoding="utf-8")
        (root / "unrelated.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "file.txt", "unrelated.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

    def test_rollback_restores_exact_git_index_after_failed_commit_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            initial_head = self._init_git_repo(root)
            (root / "unrelated.txt").write_text("preexisting staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "unrelated.txt"], cwd=root, check=True)
            initial_cached = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=root)

            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])

            (root / "file.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            result = rollback_transaction(transaction_path, reason="failed commit staging rollback")

            self.assertEqual(result.returncode, 0, result.output)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), initial_head)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=root), initial_cached)
            self.assertIn("git_state: restored", result.output)

    def test_rollback_rewinds_recorded_apply_commit_and_restores_exact_git_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            initial_head = self._init_git_repo(root)
            (root / "unrelated.txt").write_text("preexisting staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "unrelated.txt"], cwd=root, check=True)
            initial_cached = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=root)

            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])

            (root / "file.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "transaction apply"], cwd=root, check=True)
            applied_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertNotEqual(applied_commit, initial_head)
            record_transaction_git_commit(transaction_path, applied_commit)

            result = rollback_transaction(transaction_path, reason="post-commit verification failure")

            self.assertEqual(result.returncode, 0, result.output)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), initial_head)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=root), initial_cached)
            self.assertIn("git_state: restored", result.output)

    def test_rollback_refuses_to_rewind_unexpected_commit_after_recorded_apply_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            self._init_git_repo(root)
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modified", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])
            (root / "file.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "transaction apply"], cwd=root, check=True)
            applied_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            record_transaction_git_commit(transaction_path, applied_commit)
            (root / "later.txt").write_text("user commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "later.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "later user commit"], cwd=root, check=True)
            later_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

            result = rollback_transaction(transaction_path, reason="must not destroy later commit")

            self.assertEqual(result.returncode, 1)
            self.assertIn("HEAD changed after the devtool apply commit", result.output)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), later_head)
            self.assertTrue(transaction_path.exists())

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

    def test_apply_overlay_explicit_validate_overrides_manifest_disabled_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            self._write_generic_config(root, restore="exit 1")
            artifact = Path(tmp) / "overlay.zip"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "validation": {"required": False, "mode": "disabled"},
            }
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", __import__("json").dumps(manifest))
                zf.writestr("file.txt", "new\n")

            with patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--yes", "--no-clipboard", "apply-overlay", "--validate", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertIsNone(find_active_transaction(root))

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
            # run must not be rolled back by other commands.
            transaction_path = find_active_transactions(root, include_live=True)[0]
            transaction = __import__("json").loads(transaction_path.read_text(encoding="utf-8"))
            transaction["pid"] = 99999999
            transaction["process_start"] = "dead"
            transaction_path.write_text(__import__("json").dumps(transaction), encoding="utf-8")
            marker = transaction_root(root) / "active.json"
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


    def test_journaled_rollback_failure_is_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])
            (root / "file.txt").write_text("new\n", encoding="utf-8")

            with patch("devtool.transactions._copy_path", side_effect=RuntimeError("simulated storage interruption")):
                failed = rollback_transaction(transaction_path, reason="test interruption")

            self.assertEqual(failed.returncode, 1)
            failed_state = read_transaction(transaction_path)
            self.assertEqual(failed_state["status"], "rollback-failed")
            self.assertEqual(failed_state["affected_paths"][0]["restore_state"], "staging")
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "new\n")
            self.assertTrue(transaction_path.exists())

            resumed = rollback_transaction(transaction_path, reason="resume test rollback")
            self.assertEqual(resumed.returncode, 0)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse(transaction_path.exists())
            history = transaction_root(root) / "history" / f"{tx['transaction_id']}.json"
            self.assertEqual(__import__("json").loads(history.read_text(encoding="utf-8"))["status"], "rolled-back")

    def test_corrupt_backup_is_detected_before_target_is_displaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])
            (root / "file.txt").write_text("new\n", encoding="utf-8")
            (Path(tx["backup_dir"]) / "file.txt").write_text("corrupt\n", encoding="utf-8")

            result = rollback_transaction(transaction_path, reason="verify corrupt backup")

            self.assertEqual(result.returncode, 1)
            self.assertIn("staging verification failed", result.output)
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual(read_transaction(transaction_path)["status"], "rollback-failed")

    def test_abandon_transaction_keeps_changes_and_archives_terminal_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "file.txt").write_text("old\n", encoding="utf-8")
            artifact = Path(tmp) / "overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("file.txt", "new\n")
            tx = create_apply_transaction(
                root,
                artifact,
                artifact_type="overlay",
                preview={"changes": [{"kind": "modify", "path": "file.txt"}]},
            )
            transaction_path = Path(tx["path"])
            (root / "file.txt").write_text("new\n", encoding="utf-8")

            abandon_transaction(transaction_path, reason="keep the patched tree")

            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "new\n")
            self.assertFalse(transaction_path.exists())
            self.assertIsNone(find_active_transaction(root))
            history = transaction_root(root) / "history" / f"{tx['transaction_id']}.json"
            payload = __import__("json").loads(history.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "abandoned-kept-changes")
            self.assertEqual(payload["abandon_reason"], "keep the patched tree")


if __name__ == "__main__":
    unittest.main()
