from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devtool.cache_cleanup import cleanup_devtool_caches


class CacheCleanupTests(unittest.TestCase):
    def test_cleanup_removes_devtool_managed_cache_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".cache" / "devtool"
            state_root = root / ".local" / "state" / "devtool"
            share_root = root / ".local" / "share" / "devtool"
            logs_dir = state_root / "logs"
            transactions_dir = share_root / "transactions"
            performance_file = share_root / "performance-latest.json"
            for path in (cache_root / "android-sdk", logs_dir, transactions_dir):
                path.mkdir(parents=True, exist_ok=True)
                (path / "marker").write_text("x", encoding="utf-8")
            performance_file.parent.mkdir(parents=True, exist_ok=True)
            performance_file.write_text("{}", encoding="utf-8")

            removed = cleanup_devtool_caches(
                cache_root=cache_root,
                state_root=state_root,
                share_root=share_root,
            )

        self.assertIn(str(cache_root), removed)
        self.assertIn(str(logs_dir), removed)
        self.assertIn(str(transactions_dir), removed)
        self.assertIn(str(performance_file), removed)
        self.assertFalse(cache_root.exists())
        self.assertFalse(logs_dir.exists())
        self.assertFalse(transactions_dir.exists())
        self.assertFalse(performance_file.exists())
