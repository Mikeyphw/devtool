from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from rich.console import Console

from devtool.npm_progress import NpmProgressTracker, npm_progress_plan


class NpmProgressTests(unittest.TestCase):
    def test_plan_expands_nested_npm_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "packages" / "child"
            child.mkdir(parents=True)
            (root / "package.json").write_text(
                '{"name":"root","scripts":{"build":"npm run build:a && npm --prefix packages/child run build","build:a":"node a.js"}}',
                encoding="utf-8",
            )
            (child / "package.json").write_text(
                '{"name":"child","scripts":{"build":"node child.js"}}',
                encoding="utf-8",
            )

            plan = npm_progress_plan(["npm", "run", "build"], str(root))

            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual([step.script for step in plan.steps], ["build", "build:a", "build"])
            self.assertEqual([step.root for step in plan.steps], [root, root, child])

    def test_tracker_counts_npm_script_banners(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"name":"demo","scripts":{"build":"npm run one && npm run two","one":"node one.js","two":"node two.js"}}',
                encoding="utf-8",
            )
            tracker = NpmProgressTracker(["npm", "run", "build"], str(root), "build", Console(force_terminal=False))

            tracker.update_from_line("> demo@1.0.0 build")
            tracker.update_from_line("> one")
            snapshot = tracker.snapshot()

            # Banners mark starts. The second banner proves only the first
            # script finished, so completion must lag the active script.
            self.assertEqual(snapshot["completed"], 1)
            self.assertEqual(snapshot["total"], 3)
            self.assertEqual(snapshot["unit_label"], "scripts")
            self.assertAlmostEqual(snapshot["percent"], 100 / 3)

    def test_tracker_finishes_to_total_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"scripts":{"build":"npm run one","one":"node one.js"}}',
                encoding="utf-8",
            )
            tracker = NpmProgressTracker(["npm", "run", "build"], str(root), "build", Console(force_terminal=False))

            tracker.update_from_line("> build")
            tracker.finish(0)
            snapshot = tracker.snapshot()

            self.assertEqual(snapshot["completed"], 2)
            self.assertEqual(snapshot["total"], 2)
            self.assertEqual(snapshot["percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
