from __future__ import annotations

import unittest
from unittest.mock import patch

from devtool.clipboard import ClipboardResult, copy_text


class ClipboardTests(unittest.TestCase):
    def test_copy_text_prefers_x11_before_wayland(self) -> None:
        calls: list[str] = []

        with (
            patch("devtool.clipboard._can_use_x11", return_value=True),
            patch("devtool.clipboard._can_use_wl_copy", return_value=True),
            patch(
                "devtool.clipboard.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}" if name in {"xclip", "xsel", "wl-copy"} else None,
            ),
            patch(
                "devtool.clipboard._run_clipboard_command",
                side_effect=lambda command, text, backend: calls.append(backend) or ClipboardResult(True, backend=backend),
            ),
        ):
            result = copy_text("hello")

        self.assertTrue(result.success)
        self.assertEqual(calls, ["xclip"])

    def test_copy_text_prefers_termux_clipboard_in_termux(self) -> None:
        calls: list[str] = []

        with (
            patch.dict("os.environ", {"TERMUX_VERSION": "1"}, clear=False),
            patch(
                "devtool.clipboard.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}" if name == "termux-clipboard-set" else None,
            ),
            patch(
                "devtool.clipboard._run_clipboard_command",
                side_effect=lambda command, text, backend: calls.append(backend) or ClipboardResult(True, backend=backend),
            ),
        ):
            result = copy_text("hello")

        self.assertTrue(result.success)
        self.assertEqual(result.backend, "termux-clipboard-set")
        self.assertEqual(calls, ["termux-clipboard-set"])

    def test_copy_text_returns_last_failure_when_backends_fail(self) -> None:
        with (
            patch("devtool.clipboard._can_use_x11", return_value=True),
            patch("devtool.clipboard._can_use_wl_copy", return_value=False),
            patch(
                "devtool.clipboard.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}" if name in {"xclip", "xsel"} else None,
            ),
            patch(
                "devtool.clipboard._run_clipboard_command",
                side_effect=[
                    ClipboardResult(False, backend="xclip", reason="xclip failed"),
                    ClipboardResult(False, backend="xsel", reason="xsel failed"),
                ],
            ),
        ):
            result = copy_text("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.backend, "xsel")
        self.assertEqual(result.reason, "xsel failed")

    def test_termux_clipboard_waits_for_real_success(self) -> None:
        completed = __import__("subprocess").CompletedProcess(
            ["termux-clipboard-set"], 0, "", ""
        )
        with patch("devtool.clipboard.subprocess.run", return_value=completed) as run:
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(
                ["termux-clipboard-set"], "failure summary", "termux-clipboard-set"
            )

        self.assertTrue(result.success)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["input"], "failure summary")

    def test_termux_clipboard_reports_late_failure(self) -> None:
        completed = __import__("subprocess").CompletedProcess(
            ["termux-clipboard-set"], 1, "", "API service unavailable"
        )
        with patch("devtool.clipboard.subprocess.run", return_value=completed):
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(
                ["termux-clipboard-set"], "failure summary", "termux-clipboard-set"
            )

        self.assertFalse(result.success)
        self.assertIn("status 1", result.reason or "")
        self.assertIn("API service unavailable", result.reason or "")

    def test_termux_clipboard_timeout_is_not_success(self) -> None:
        import subprocess

        with patch(
            "devtool.clipboard.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["termux-clipboard-set"], 8),
        ):
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(
                ["termux-clipboard-set"], "failure summary", "termux-clipboard-set"
            )

        self.assertFalse(result.success)
        self.assertIn("did not finish", result.reason or "")

    def test_run_clipboard_command_accepts_long_lived_process(self) -> None:
        events: list[str] = []

        class FakeStdin:
            def write(self, text: str) -> int:
                events.append(f"write:{text}")
                return len(text)

            def close(self) -> None:
                events.append("close")

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()

            def poll(self):
                return None

        with (
            patch("devtool.clipboard.subprocess.Popen", return_value=FakeProcess()),
            patch("devtool.clipboard.time.sleep", return_value=None),
            patch("devtool.clipboard._read_desktop_clipboard", return_value="hello"),
        ):
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(["xclip", "-selection", "clipboard"], "hello", "xclip")

        self.assertTrue(result.success)
        self.assertEqual(result.backend, "xclip")
        self.assertEqual(events, ["write:hello", "close"])

    def test_run_clipboard_command_rejects_stale_xclip_readback(self) -> None:
        class FakeStdin:
            def write(self, text: str) -> int:
                return len(text)

            def close(self) -> None:
                pass

        class FakeProcess:
            stdin = FakeStdin()

            def poll(self):
                return None

        with (
            patch("devtool.clipboard.subprocess.Popen", return_value=FakeProcess()),
            patch("devtool.clipboard.time.sleep", return_value=None),
            patch("devtool.clipboard._read_desktop_clipboard", return_value="old hud summary"),
        ):
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(["xclip", "-selection", "clipboard"], "failure summary", "xclip")

        self.assertFalse(result.success)
        self.assertEqual(result.backend, "xclip")
        self.assertIn("readback did not match", result.reason or "")

    def test_run_clipboard_command_reports_xclip_readback_failure(self) -> None:
        class FakeStdin:
            def write(self, text: str) -> int:
                return len(text)

            def close(self) -> None:
                pass

        class FakeProcess:
            stdin = FakeStdin()

            def poll(self):
                return None

        readback_failure = ClipboardResult(False, backend="xclip", reason="Can't open display")
        with (
            patch("devtool.clipboard.subprocess.Popen", return_value=FakeProcess()),
            patch("devtool.clipboard.time.sleep", return_value=None),
            patch("devtool.clipboard._read_desktop_clipboard", return_value=readback_failure),
        ):
            from devtool.clipboard import _run_clipboard_command

            result = _run_clipboard_command(["xclip", "-selection", "clipboard"], "failure summary", "xclip")

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "Can't open display")


if __name__ == "__main__":
    unittest.main()
