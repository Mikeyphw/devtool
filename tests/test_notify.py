from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from repo_devtool.notify import available_notifier, send_notification


class NotifyTests(unittest.TestCase):
    def test_available_notifier_prefers_termux_notification(self) -> None:
        with (
            patch.dict("os.environ", {"TERMUX_VERSION": "1"}, clear=False),
            patch(
                "repo_devtool.notify.shutil.which",
                side_effect=lambda name: {
                    "termux-notification": "/data/data/com.termux/files/usr/bin/termux-notification",
                    "notify-send": "/data/data/com.termux/files/usr/bin/notify-send",
                }.get(name),
            ),
        ):
            self.assertEqual(available_notifier(), "termux-notification")

    def test_available_notifier_disables_notify_send_on_termux_without_gui(self) -> None:
        with (
            patch.dict("os.environ", {"TERMUX_VERSION": "1"}, clear=True),
            patch(
                "repo_devtool.notify.shutil.which",
                side_effect=lambda name: "/data/data/com.termux/files/usr/bin/notify-send" if name == "notify-send" else None,
            ),
        ):
            self.assertIsNone(available_notifier())

    def test_send_notification_uses_termux_notification(self) -> None:
        with (
            patch("repo_devtool.notify.available_notifier", return_value="termux-notification"),
            patch("repo_devtool.notify.subprocess.run") as run_mock,
        ):
            self.assertTrue(send_notification("Done", "PASS"))
        run_mock.assert_called_once_with(
            ["termux-notification", "--title", "Done", "--content", "PASS"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    unittest.main()
