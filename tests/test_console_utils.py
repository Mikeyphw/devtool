from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_devtool.console_utils import create_console, is_narrow_console, resolve_cli_path, syntax_line_numbers
from repo_devtool.summary import render_status_table


class ConsoleUtilsTests(unittest.TestCase):
    def test_termux_console_uses_detected_width(self) -> None:
        with patch.dict("os.environ", {"TERMUX_VERSION": "1", "COLUMNS": "72"}, clear=False):
            console = create_console()
            self.assertEqual(console.width, 72)
            self.assertTrue(is_narrow_console(console))
            self.assertFalse(syntax_line_numbers(console))

    def test_render_status_table_compacts_on_narrow_termux(self) -> None:
        stream = io.StringIO()
        with patch.dict("os.environ", {"TERMUX_VERSION": "1", "COLUMNS": "72"}, clear=False):
            console = create_console()
            with console.capture() as capture:
                render_status_table([("repo", "/tmp/example"), ("kind", "dotnet")], console_obj=console)
            output = capture.get()
        self.assertIn("Status", output)
        self.assertIn("repo", output)
        self.assertIn("/tmp/example", output)


    def test_resolve_cli_path_rewrites_home_to_override_on_termux(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TERMUX_VERSION": "1",
                "HOME": "/data/data/com.termux/files/home",
                "DEVTOOL_TERMUX_HOME_OVERRIDE": "/sdcard",
            },
            clear=False,
        ):
            self.assertEqual(str(resolve_cli_path("~/Downloads/overlay.zip")), str(Path("/sdcard/Downloads/overlay.zip").resolve()))
            self.assertEqual(str(resolve_cli_path("$HOME/Downloads/overlay.zip")), str(Path("/sdcard/Downloads/overlay.zip").resolve()))


if __name__ == "__main__":
    unittest.main()
