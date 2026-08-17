from pathlib import Path
from unittest.mock import patch

import pytest

from devtool import android_build


def test_xdm_js_test_task_requires_node() -> None:
    assert android_build.gradle_tasks_require_node(
        [":browser-extension:jsTest"]
    )


def test_regular_android_unit_test_does_not_require_node() -> None:
    assert not android_build.gradle_tasks_require_node(
        [":app:testDebugUnitTest"]
    )


def test_node_runtime_path_uses_effective_path(tmp_path: Path) -> None:
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nexit 0\n")
    node.chmod(0o755)

    assert android_build.node_runtime_path(
        {"PATH": str(tmp_path)}
    ) == node


def test_offline_missing_node_is_reported() -> None:
    with pytest.raises(android_build.BuildError, match="Node.js"):
        android_build.install_missing_system_dependencies(
            need_java=False,
            need_certificates=False,
            need_node=True,
            offline=True,
        )


def test_linux_auto_dependency_installs_nodejs() -> None:
    with (
        patch(
            "devtool.android_build.running_under_termux_python",
            return_value=False,
        ),
        patch(
            "devtool.android_build.shutil.which",
            side_effect=lambda name, **_: (
                "/usr/bin/apt-get" if name == "apt-get" else None
            ),
        ),
        patch(
            "devtool.android_build.root_command",
            side_effect=lambda command: list(command),
        ),
        patch("devtool.android_build.run") as run,
    ):
        android_build.install_missing_system_dependencies(
            need_java=False,
            need_certificates=False,
            need_node=True,
            offline=False,
        )

    commands = [call.args[0] for call in run.call_args_list]

    assert ["/usr/bin/apt-get", "update"] in commands
    assert [
        "/usr/bin/apt-get",
        "install",
        "-y",
        "--no-install-recommends",
        "nodejs",
    ] in commands


def test_termux_auto_dependency_installs_nodejs() -> None:
    with (
        patch(
            "devtool.android_build.running_under_termux_python",
            return_value=True,
        ),
        patch(
            "devtool.android_build.shutil.which",
            side_effect=lambda name, **_: (
                "/data/data/com.termux/files/usr/bin/pkg"
                if name == "pkg"
                else None
            ),
        ),
        patch("devtool.android_build.run") as run,
    ):
        android_build.install_missing_system_dependencies(
            need_java=False,
            need_certificates=False,
            need_node=True,
            offline=False,
        )

    run.assert_called_once_with([
        "/data/data/com.termux/files/usr/bin/pkg",
        "install",
        "-y",
        "nodejs",
    ])
