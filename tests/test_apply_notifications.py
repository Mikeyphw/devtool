from __future__ import annotations

from unittest.mock import patch

from devtool.apply_notifications import ApplyNotification, post_apply_notification


def test_android_apply_notification_is_skipped_when_app_run_notification_is_active(tmp_path) -> None:
    notification = ApplyNotification("Devtool overlay applied", "demo.zip", "Applied: demo.zip")
    with (
        patch.dict(
            "os.environ",
            {
                "DEVTOOL_HUD_BRIDGE_DIR": "/tmp/devtool-hud",
                "DEVTOOL_RUN_ID": "run-1",
                "DEVTOOL_NOTIFICATIONS": "auto",
                "DEVTOOL_TOASTS": "auto",
                "DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "absent.toml"),
            },
            clear=False,
        ),
        patch("devtool.apply_notifications._prefer_android_side", return_value=True),
        patch("devtool.notification_controls.companion_installed", return_value=False),
        patch("devtool.apply_notifications._post_termux_notification") as termux_notification,
        patch("devtool.apply_notifications._post_termux_toast", return_value=True) as termux_toast,
        patch("devtool.apply_notifications._post_android_bridge_toast") as bridge_toast,
    ):
        post_apply_notification(notification)

    termux_notification.assert_not_called()
    termux_toast.assert_called_once_with(notification.toast)
    bridge_toast.assert_not_called()


def test_android_apply_notification_posts_when_app_run_notification_is_not_active(tmp_path) -> None:
    notification = ApplyNotification("Devtool overlay applied", "demo.zip", "Applied: demo.zip")
    with (
        patch.dict(
            "os.environ",
            {
                "DEVTOOL_HUD_BRIDGE_DIR": "",
                "DEVTOOL_RUN_ID": "",
                "DEVTOOL_NOTIFICATIONS": "auto",
                "DEVTOOL_TOASTS": "auto",
                "DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "absent.toml"),
            },
            clear=False,
        ),
        patch("devtool.apply_notifications._prefer_android_side", return_value=True),
        patch("devtool.notification_controls.companion_installed", return_value=False),
        patch("devtool.apply_notifications._post_termux_notification", return_value=True) as termux_notification,
        patch("devtool.apply_notifications._post_termux_toast", return_value=True),
        patch("devtool.apply_notifications._post_android_bridge_toast"),
    ):
        post_apply_notification(notification)

    termux_notification.assert_called_once_with(notification)
