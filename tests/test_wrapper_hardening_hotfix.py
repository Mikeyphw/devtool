from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.completion import ZSH_COMPLETION
from devtool.config import load_project_config
from devtool.wrapper_launcher import LAUNCHER_MARKER, POSIX_LAUNCHER, WrapperLauncherError, install_launchers
from devtool.wrapper_resolution import WrapperResolutionError, resolve_wrapper_argv, wrapper_resolution_as_dict


def _write(root: Path, wrapper: str) -> None:
    (root / ".devtool.toml").write_text(
        f'''
[project]
runner = "command"

[workspace]
default_target = "android"

[targets.android]
runner = "command"

{wrapper.strip()}
'''.strip() + "\n",
        encoding="utf-8",
    )


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


@pytest.mark.parametrize(
    ("lane", "token"),
    [
        ("command_argv", "--target=evil"),
        ("command_argv", "--target={value}"),
        ("command_argv", "--group=evil"),
        ("global_argv", "--repo=/tmp/evil"),
        ("global_argv", "-r/tmp/evil"),
        ("global_argv", "--config=/tmp/evil.toml"),
        ("global_argv", "-c/tmp/evil.toml"),
        ("global_argv", "--target=evil"),
        ("global_argv", "--group=evil"),
        ("global_argv", "--all-targets"),
    ],
)
def test_attached_routing_control_spellings_fail_closed(lane: str, token: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            f'''
[wrapper.commands.build]
devtool = "build"
target = "android"

[wrapper.commands.build.options.bad]
type = "string"
{lane} = ["{token}"]
''',
        )
        with pytest.raises(SystemExit, match="control token"):
            _load(root)


def test_dynamic_binding_value_cannot_become_routing_control() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            '''
[wrapper.commands.build]
devtool = "build"
target = "android"

[wrapper.commands.build.options.extra]
type = "string"
command_argv = ["{value}"]
''',
        )
        config = _load(root)
        with pytest.raises(WrapperResolutionError, match="routing control"):
            resolve_wrapper_argv(config, ["build", "--extra", "--target=evil"])


def test_update_protects_unmanaged_launcher_until_force() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        custom = root / POSIX_LAUNCHER
        custom.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
        with pytest.raises(WrapperLauncherError, match="unmanaged file"):
            install_launchers(root, update=True)
        assert custom.read_text(encoding="utf-8") == "#!/bin/sh\necho custom\n"
        status = install_launchers(root, update=True, force=True)
        assert status.ok
        assert LAUNCHER_MARKER in custom.read_text(encoding="utf-8")


def test_force_replaces_launcher_symlink_without_following_target() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        outside = root / "outside.sh"
        outside.write_text("do-not-touch\n", encoding="utf-8")
        link = root / POSIX_LAUNCHER
        link.symlink_to(outside)
        with pytest.raises(WrapperLauncherError, match="symlink"):
            install_launchers(root, update=True)
        install_launchers(root, update=True, force=True)
        assert not link.is_symlink()
        assert outside.read_text(encoding="utf-8") == "do-not-touch\n"
        assert LAUNCHER_MARKER in link.read_text(encoding="utf-8")


def test_machine_resolution_never_exports_sensitive_value() -> None:
    secret = "TOPSECRET123"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            f'''
[wrapper.commands.upload]
devtool = "run"
target = "android"

[wrapper.commands.upload.options.api_token]
type = "string"
default = "{secret}"
command_argv = ["--token={{value}}"]
''',
        )
        resolution = resolve_wrapper_argv(_load(root), ["upload"])
        assert secret in " ".join(resolution.canonical_argv)
        payload = wrapper_resolution_as_dict(resolution)
        encoded = json.dumps(payload, sort_keys=True)
        assert secret not in encoded
        assert payload["options"]["api_token"]["value"] == "<redacted>"
        assert "<redacted>" in " ".join(payload["canonical_argv"])


def test_generated_zsh_completion_integrates_wrapper_surface() -> None:
    assert "'wrapper:resolve, inspect, and execute repository wrapper commands'" in ZSH_COMPLETION
    assert "wrapper)" in ZSH_COMPLETION
    assert "describe help discover resolve exec init update verify status seal" in ZSH_COMPLETION
    assert "_devtool_wrapper_commands" in ZSH_COMPLETION
    assert "_devtool_wrapper_options" in ZSH_COMPLETION
    assert "--force[explicitly replace unmanaged launcher files]" in ZSH_COMPLETION


def test_wrapper_update_cli_requires_force_for_unmanaged_file(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        custom = root / POSIX_LAUNCHER
        custom.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
        parser = cli.build_parser()
        args = parser.parse_args(["--repo", str(root), "wrapper", "update", "--json"])
        assert cli._handle_wrapper_command(args) == 2
        assert "unmanaged file" in capsys.readouterr().err
        args = parser.parse_args(["--repo", str(root), "wrapper", "update", "--force", "--json"])
        assert cli._handle_wrapper_command(args) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
