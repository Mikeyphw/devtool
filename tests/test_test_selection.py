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

    def test_python_selection_does_not_inject_pytest_flags_into_tox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            (root / "tox.ini").write_text("[tox]\nenvlist=py\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                runner_options={
                    "python": {"manager": "uv", "test_framework": "tox"},
                    "test_selection": {"selectors": ["parser"], "tags": ["fast"]},
                },
            )
            with self.assertRaisesRegex(SystemExit, "tox supports Devtool selection only"):
                build_specs(config, "test")

    def test_custom_python_test_command_needs_explicit_pytest_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                project_root=str(root),
                kind="python",
                test_command="tox -e py312",
                runner_options={"test_selection": {"selectors": ["parser"]}},
            )
            with self.assertRaisesRegex(SystemExit, "custom Python adapter"):
                build_specs(config, "test")

class ValidationTestSelectionCliAndRunnerTests(unittest.TestCase):
    def test_android_validation_task_selection_accepts_multiple_tasks(self) -> None:
        from devtool.android_integration import configured_tasks
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="android",
                runner_options={"test_selection": {"tasks": [":media:test", ":app:testDebugUnitTest"]}},
            )
            self.assertEqual(configured_tasks(config, "test"), [":media:test", ":app:testDebugUnitTest"])

    def test_node_validation_task_selection_chooses_named_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"scripts":{"test":"vitest","test:unit":"vitest run unit"}}',
                encoding="utf-8",
            )
            config = ProjectConfig(
                repo_root=root,
                kind="node",
                runner_options={"test_selection": {"tasks": ["test:unit"]}},
            )
            spec = build_specs(config, "test")[0]
        self.assertEqual(spec.argv[:3], ["npm", "run", "test:unit"])
        self.assertEqual(spec.metadata.get("node_script"), "test:unit")

    def test_go_validation_selector_translates_to_run_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
            config = ProjectConfig(
                repo_root=root,
                kind="go",
                runner_options={"test_selection": {"methods": ["TestParser"]}},
            )
            spec = build_specs(config, "test")[0]
        self.assertIn("-run", spec.argv)
        self.assertEqual(spec.argv[spec.argv.index("-run") + 1], "TestParser")

    def test_cmake_validation_selector_translates_to_ctest_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(
                repo_root=root,
                kind="cmake",
                runner_options={"test_selection": {"selectors": ["parser"]}},
            )
            spec = build_specs(config, "test")[0]
        self.assertIn("-R", spec.argv)
        self.assertEqual(spec.argv[spec.argv.index("-R") + 1], "parser")
