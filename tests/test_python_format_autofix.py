from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from devtool.cli import (
    _apply_runner_cli_options,
    build_parser,
)
from devtool.gradle_reports import (
    collect_python_tool_output,
)
from devtool.models import ProjectConfig
from devtool.python_support import run_tool
from devtool.runners import (
    build_specs,
    build_validation_specs,
)


class PythonFormatAutofixTests(unittest.TestCase):
    def _config(
        self,
        root: Path,
        lint: list[str],
        *,
        format_fix: bool = False,
    ) -> ProjectConfig:
        return ProjectConfig(
            repo_root=root,
            project_root=str(root),
            kind="python",
            runner_options={
                "python": {
                    "manager": "venv",
                    "lint": lint,
                    "format_fix": format_fix,
                }
            },
        )

    def test_default_is_check_only(self) -> None:
        parser = build_parser()

        self.assertFalse(
            parser.parse_args(
                ["lint"]
            ).python_format_fix
        )

        self.assertFalse(
            parser.parse_args(
                ["validate"]
            ).python_format_fix
        )

        self.assertFalse(
            parser.parse_args(
                [
                    "apply-overlay",
                    "artifact.zip",
                ]
            ).python_format_fix
        )

    def test_format_and_fix_aliases(self) -> None:
        parser = build_parser()

        self.assertTrue(
            parser.parse_args(
                [
                    "lint",
                    "--format",
                ]
            ).python_format_fix
        )

        self.assertTrue(
            parser.parse_args(
                [
                    "validate",
                    "--fix",
                ]
            ).python_format_fix
        )

        self.assertTrue(
            parser.parse_args(
                [
                    "apply-overlay",
                    "--format",
                    "artifact.zip",
                ]
            ).python_format_fix
        )

        self.assertTrue(
            parser.parse_args(
                [
                    "start-validation",
                    "--format",
                ]
            ).python_format_fix
        )

    def test_autofix_must_be_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            config = self._config(
                root,
                ["black"],
                format_fix=True,
            )

            args = build_parser().parse_args(
                ["lint"]
            )

            _apply_runner_cli_options(
                config,
                args,
            )

            self.assertFalse(
                config.runner_options[
                    "python"
                ]["format_fix"]
            )

            args = build_parser().parse_args(
                [
                    "lint",
                    "--format",
                ]
            )

            _apply_runner_cli_options(
                config,
                args,
            )

            self.assertTrue(
                config.runner_options[
                    "python"
                ]["format_fix"]
            )

    def test_black_check_only_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            spec = build_specs(
                self._config(
                    root,
                    ["black"],
                ),
                "lint",
            )[0]

            self.assertIn(
                "--check",
                spec.argv,
            )

    def test_black_format_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            spec = build_specs(
                self._config(
                    root,
                    ["black"],
                    format_fix=True,
                ),
                "lint",
            )[0]

            self.assertNotIn(
                "--check",
                spec.argv,
            )

    def test_validation_uses_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            normal = build_validation_specs(
                self._config(
                    root,
                    ["black"],
                ),
                skip_restore=True,
                skip_test=True,
                skip_build=True,
            )

            fixed = build_validation_specs(
                self._config(
                    root,
                    ["black"],
                    format_fix=True,
                ),
                skip_restore=True,
                skip_test=True,
                skip_build=True,
            )

            normal_black = next(
                spec
                for spec in normal
                if spec.name == "lint:black"
            )

            fixed_black = next(
                spec
                for spec in fixed
                if spec.name == "lint:black"
            )

            self.assertIn(
                "--check",
                normal_black.argv,
            )

            self.assertNotIn(
                "--check",
                fixed_black.argv,
            )

    def test_fix_mode_other_formatters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            specs = build_specs(
                self._config(
                    root,
                    [
                        "isort",
                        "ruff-format",
                        "ruff",
                    ],
                    format_fix=True,
                ),
                "lint",
            )

            by_name = {
                spec.name: spec
                for spec in specs
            }

            self.assertNotIn(
                "--check-only",
                by_name[
                    "lint:isort"
                ].argv,
            )

            self.assertNotIn(
                "--check",
                by_name[
                    "lint:ruff-format"
                ].argv,
            )

            self.assertIn(
                "--fix",
                by_name[
                    "lint:ruff"
                ].argv,
            )

    def test_black_reformat_is_warning(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as xdg,
        ):
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            proc = mock.Mock(
                returncode=1,
                stdout=(
                    "would reformat src/demo.py\n"
                    "would reformat tests/test_demo.py\n"
                    "2 files would be reformatted.\n"
                ),
            )

            args = SimpleNamespace(
                root=root,
                manager="auto",
                framework="auto",
                python="python3",
                environment="",
                timeout=30,
                tool="black",
                tool_args=(
                    "--check",
                    ".",
                ),
            )

            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "DEVTOOL_XDG_DIR": xdg
                    },
                    clear=False,
                ),
                mock.patch(
                    "devtool.python_support._run_capture",
                    return_value=proc,
                ),
                mock.patch(
                    "sys.stdout",
                    new=StringIO(),
                ) as stdout,
            ):
                rc = run_tool(args)

            self.assertEqual(
                rc,
                0,
            )

            payload_line = next(
                line
                for line in (
                    stdout
                    .getvalue()
                    .splitlines()
                )
                if line.startswith(
                    "DEVTOOL_PYTHON_TOOL_RESULT "
                )
            )

            payload = json.loads(
                payload_line.split(
                    " ",
                    1,
                )[1]
            )

            self.assertEqual(
                payload["errors"],
                0,
            )

            self.assertEqual(
                payload["warnings"],
                2,
            )

            self.assertEqual(
                {
                    item["severity"]
                    for item
                    in payload["issues"]
                },
                {"warning"},
            )

    def test_black_real_failure_is_error(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as xdg,
        ):
            root = Path(tmp)

            (root / "pyproject.toml").write_text(
                "[project]\n"
                "name='demo'\n"
                "version='1'\n",
                encoding="utf-8",
            )

            proc = mock.Mock(
                returncode=123,
                stdout=(
                    "error: cannot format "
                    "src/demo.py: Cannot parse\n"
                ),
            )

            args = SimpleNamespace(
                root=root,
                manager="auto",
                framework="auto",
                python="python3",
                environment="",
                timeout=30,
                tool="black",
                tool_args=(
                    "--check",
                    ".",
                ),
            )

            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "DEVTOOL_XDG_DIR": xdg
                    },
                    clear=False,
                ),
                mock.patch(
                    "devtool.python_support._run_capture",
                    return_value=proc,
                ),
                mock.patch(
                    "sys.stdout",
                    new=StringIO(),
                ) as stdout,
            ):
                rc = run_tool(args)

            self.assertEqual(
                rc,
                123,
            )

            payload_line = next(
                line
                for line in (
                    stdout
                    .getvalue()
                    .splitlines()
                )
                if line.startswith(
                    "DEVTOOL_PYTHON_TOOL_RESULT "
                )
            )

            payload = json.loads(
                payload_line.split(
                    " ",
                    1,
                )[1]
            )

            self.assertGreaterEqual(
                payload["errors"],
                1,
            )

    def test_collector_preserves_warning(self) -> None:
        payload = {
            "tool": "black",
            "returncode": 0,
            "errors": 0,
            "warnings": 1,
            "issues": [
                {
                    "severity": "warning",
                    "message": (
                        "would reformat demo.py"
                    ),
                }
            ],
        }

        output = (
            "DEVTOOL_PYTHON_TOOL_RESULT "
            + json.dumps(payload)
        )

        collection = collect_python_tool_output(
            output,
            repo_root=Path("/tmp"),
            command_name="lint:black",
        )

        self.assertEqual(
            len(collection.issues),
            1,
        )

        self.assertEqual(
            collection.issues[0].severity,
            "warning",
        )


if __name__ == "__main__":
    unittest.main()
