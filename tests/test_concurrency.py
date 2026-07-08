from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtool.concurrency import ConcurrencyLease, lease_root
from devtool.cli import build_parser, _maybe_copy
from devtool.clipboard import ClipboardResult
from devtool.hud import state_from_directory


class ConcurrencyLeaseTests(unittest.TestCase):
    def test_disjoint_write_leases_can_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            first = ConcurrencyLease(repo, "first", writes=["s3mo/src"], timeout=0).acquire()
            try:
                second = ConcurrencyLease(repo, "second", writes=["s3oc/src"], timeout=0).acquire()
                second.release()
            finally:
                first.release()

    def test_parent_child_write_conflict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            first = ConcurrencyLease(repo, "first", writes=["s3mo"], timeout=0).acquire()
            try:
                with self.assertRaises(TimeoutError):
                    ConcurrencyLease(repo, "second", reads=["s3mo/App/Main.cs"], timeout=0).acquire()
            finally:
                first.release()

    def test_read_read_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            first = ConcurrencyLease(repo, "first", reads=["shared/Directory.Build.props"], timeout=0).acquire()
            try:
                second = ConcurrencyLease(repo, "second", reads=["shared/Directory.Build.props"], timeout=0).acquire()
                second.release()
            finally:
                first.release()

    def test_dead_lease_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            leases = lease_root(repo) / "leases"
            leases.mkdir(parents=True)
            stale = leases / "stale.json"
            stale.write_text(json.dumps({"run_id": "old", "pid": 99999999, "process_start": "dead", "reads": [], "writes": ["src"]}), encoding="utf-8")
            lease = ConcurrencyLease(repo, "new", writes=["src"], timeout=0).acquire()
            try:
                self.assertFalse(stale.exists())
            finally:
                lease.release()


class ClipboardGuardTests(unittest.TestCase):
    def test_missing_artifact_does_not_copy(self) -> None:
        args = build_parser().parse_args(["--copy", "apply-overlay", "/tmp/missing.zip"])
        with patch("devtool.cli.copy_text", return_value=ClipboardResult(True, backend="test")) as copy:
            _maybe_copy("Artifact not found: /tmp/missing.zip", "artifact-check: failed exit=1 time=0.00s", '{"warnings":0,"errors":1,"failed":1,"skipped":0}', args)
        copy.assert_not_called()

    def test_interrupted_recovery_guard_does_not_copy(self) -> None:
        args = build_parser().parse_args(["--copy", "restore"])
        with patch("devtool.cli.copy_text", return_value=ClipboardResult(True, backend="test")) as copy:
            _maybe_copy("Requested command was not executed. Re-run it after reviewing the rollback.", "rollback: failed exit=1 time=0.00s", '{"warnings":0,"errors":1,"failed":1,"skipped":0}', args)
        copy.assert_not_called()


class ConcurrentHudTests(unittest.TestCase):
    def test_multiple_active_runs_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "a.json").write_text(json.dumps({"status": "running", "repo_name": "s3mo", "command": "validate", "phase": {"name": "build"}, "progress": {"percent": 50}, "updated_at": "2026-01-01T00:00:02Z"}), encoding="utf-8")
            (runs / "b.json").write_text(json.dumps({"status": "running", "repo_name": "s3oc", "command": "validate", "phase": {"name": "test"}, "progress": {"percent": 80}, "updated_at": "2026-01-01T00:00:01Z"}), encoding="utf-8")
            state = state_from_directory(root)
        self.assertIn("2 active runs", state.title)
        self.assertIn("s3mo", state.text)
        self.assertIn("s3oc", state.text)
