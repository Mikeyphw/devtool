from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from devtool import cli
from devtool.wrapper_launcher import (
    LAUNCHER_MARKER,
    POSIX_LAUNCHER,
    WINDOWS_LAUNCHER,
    WrapperLauncherError,
    expected_launcher_texts,
    install_launchers,
    launcher_status,
    launcher_status_as_dict,
    verify_launchers,
)


def test_wrapper_init_installs_current_posix_and_windows_launchers() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        status = install_launchers(root)

        posix = root / POSIX_LAUNCHER
        windows = root / WINDOWS_LAUNCHER

        assert status.ok
        assert posix.exists()
        assert windows.exists()
        assert LAUNCHER_MARKER in posix.read_text(encoding="utf-8")
        assert LAUNCHER_MARKER in windows.read_text(encoding="utf-8")
        assert "wrapper exec" in posix.read_text(encoding="utf-8")
        assert "wrapper exec" in windows.read_text(encoding="utf-8")
        assert os.access(posix, os.X_OK)
        assert not bool(windows.stat().st_mode & stat.S_IXUSR) or windows.name.endswith(".cmd")
        assert launcher_status(root).ok
        assert verify_launchers(root).ok


def test_wrapper_status_reports_missing_and_current_files_as_machine_readable() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        missing = launcher_status(root)
        missing_payload = launcher_status_as_dict(missing)

        assert not missing.ok
        assert not missing_payload["installed"]
        assert {item["reason"] for item in missing_payload["files"]} == {"missing"}

        current = install_launchers(root)
        current_payload = launcher_status_as_dict(current)
        assert current_payload["ok"] is True
        assert current_payload["installed"] is True
        assert current_payload["drifted"] is False
        assert {item["name"] for item in current_payload["files"]} == {POSIX_LAUNCHER, WINDOWS_LAUNCHER}


def test_wrapper_init_refuses_unmanaged_or_drifted_files_unless_forced_or_updated() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unmanaged = root / POSIX_LAUNCHER
        unmanaged.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")

        try:
            install_launchers(root)
        except WrapperLauncherError as exc:
            assert "Refusing to overwrite" in str(exc)
            assert "unmanaged" in str(exc)
        else:  # pragma: no cover - defensive, keeps the failure message clearer.
            raise AssertionError("init unexpectedly overwrote unmanaged launcher")

        status = install_launchers(root, force=True)
        assert status.ok
        assert LAUNCHER_MARKER in unmanaged.read_text(encoding="utf-8")

        unmanaged.write_text(unmanaged.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
        drifted = launcher_status(root)
        assert drifted.drifted
        assert not drifted.ok

        refreshed = install_launchers(root, update=True)
        assert refreshed.ok
        assert (root / POSIX_LAUNCHER).read_text(encoding="utf-8") == expected_launcher_texts()[POSIX_LAUNCHER]


def test_wrapper_launcher_cli_init_status_verify_json(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        parser = cli.build_parser()

        args = parser.parse_args(["--repo", str(root), "wrapper", "status", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        missing_payload = json.loads(capsys.readouterr().out)
        assert missing_payload["ok"] is False

        args = parser.parse_args(["--repo", str(root), "wrapper", "init", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        init_payload = json.loads(capsys.readouterr().out)
        assert init_payload["ok"] is True

        args = parser.parse_args(["--repo", str(root), "wrapper", "verify", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        verify_payload = json.loads(capsys.readouterr().out)
        assert verify_payload["ok"] is True

        (root / POSIX_LAUNCHER).write_text("#!/bin/sh\n# DEVTOOL_WRAPPER_LAUNCHER=1\necho stale\n", encoding="utf-8")
        args = parser.parse_args(["--repo", str(root), "wrapper", "verify", "--json"])
        assert cli._handle_wrapper_command(args) == 1
        drift_payload = json.loads(capsys.readouterr().out)
        assert drift_payload["ok"] is False
        assert drift_payload["drifted"] is True

        args = parser.parse_args(["--repo", str(root), "wrapper", "update", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        update_payload = json.loads(capsys.readouterr().out)
        assert update_payload["ok"] is True


def test_wrapper_launcher_templates_are_packaged_by_pyproject() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "resources/wrapper/*" in pyproject
    texts = expected_launcher_texts()
    assert set(texts) == {POSIX_LAUNCHER, WINDOWS_LAUNCHER}
    assert all(LAUNCHER_MARKER in text for text in texts.values())
