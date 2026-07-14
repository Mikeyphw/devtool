from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devtool.models import ProjectConfig
from devtool.project_runners import DotnetRunner, PythonRunner
from devtool.test_selection import discover_tests, resolve_test_selection, select_catalog
from devtool.runners import build_specs


class TestCatalogTests(unittest.TestCase):
    def test_discovers_and_selects_pytest_node_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_parser.py").write_text(
                "import pytest\n\n@pytest.mark.fast\ndef test_manifest():\n    pass\n\nclass TestOther:\n    def test_slow(self):\n        pass\n",
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"test_selection": {"tag": ["fast"]}})
            catalog = discover_tests(config)
            selected = select_catalog(catalog, resolve_test_selection(config))

        self.assertEqual(len(catalog), 2)
        self.assertEqual([item.name for item in selected], ["test_manifest"])
        self.assertEqual(selected[0].id, "tests/test_parser.py::test_manifest")

    def test_dotnet_selection_translates_to_native_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="dotnet",
                test_target="Demo.sln",
                runner_options={"test_selection": {"classes": ["ParserTests"], "exclude_tags": ["Slow"]}},
            )
            spec = build_specs(config, "test")[0]

        self.assertIn("--filter", spec.argv)
        expression = spec.argv[spec.argv.index("--filter") + 1]
        self.assertIn("FullyQualifiedName~ParserTests", expression)
        self.assertIn("Category!=Slow", expression)
        self.assertTrue(DotnetRunner.capabilities.selective_tests)

    def test_python_profile_translates_to_pytest_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                runner_options={
                    "python": {"manager": "uv"},
                    "test_profiles": {"quick": {"tags": ["unit"], "exclude_tags": ["slow"]}},
                    "test_selection": {"profile": "quick"},
                },
            )
            spec = build_specs(config, "test")[0]

        self.assertIn("-m", spec.argv)
        marker = spec.argv[spec.argv.index("-m") + 1]
        # The first -m belongs to `python -m` only for direct Python; uv invokes pytest directly.
        if marker == "pytest":
            marker = spec.argv[spec.argv.index("-m", spec.argv.index("-m") + 1) + 1]
        self.assertIn("unit", marker)
        self.assertIn("not slow", marker)
        self.assertTrue(PythonRunner.capabilities.selective_tests)
