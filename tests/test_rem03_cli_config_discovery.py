from __future__ import annotations

import argparse
import io
import json
import os
import stat
import tempfile
import tomllib
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from devtool.api_output import extract_universal_json_flag
from devtool.bootstrap import current_destination, load_receipt, parse_destination
from devtool.cli import _apply_global_cli_defaults, main, split_runner_passthrough_args
from devtool.config import (
    detect_project_kind,
    discover_gradle_roots,
    global_config_doctor_entries,
    load_project_config,
)
from devtool.config_migration import migrate_config_text
from devtool.detect import detect_repo
from devtool.doctor import doctor_payload
from devtool.features import resolve_feature_flags
from devtool.models import CommandSpec, ProjectConfig
from devtool.execution_environments import detect_current_environment
from devtool.toolchain_requirements import _executable_status
from devtool.toolchains import configured_toolchain_manager, toolchain_doctor_rows


def _write_config(root: Path, text: str) -> Path:
    path = root / ".devtool.toml"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def _no_global_env(root: Path) -> dict[str, str]:
    return {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}


def test_migration_preserves_quoted_default_target_values() -> None:
    value = 'api "quoted" josh\'s'
    text = f'''
        [project]
        kind = "python"
        default_target = {json.dumps(value)}
    '''

    migrated, findings = migrate_config_text(text)
    data = tomllib.loads(migrated)

    assert any(item.compatibility_id == "config.project.default_target" for item in findings)
    assert data["workspace"]["default_target"] == value
    assert "default_target" not in data.get("project", {})


def test_completion_state_inspects_multiple_targets_without_default() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(
            root,
            '''
            [project]
            runner = "python"
            [targets.api]
            runner = "python"
            [targets.web]
            runner = "node"
            ''',
        )
        stream = io.StringIO()
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False), redirect_stdout(stream):
            code = main(["--repo", str(root), "completion-state", "--kind", "targets"])

    assert code == 0
    assert set(stream.getvalue().splitlines()) == {"api", "web"}


def test_malformed_repo_ui_table_uses_repo_source_label_not_unbound_local() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, 'ui = "bad"')
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            with pytest.raises(SystemExit) as exc:
                load_project_config(str(root), None)
    assert "[ui] must be a TOML table" in str(exc.value)
    assert "UnboundLocalError" not in str(exc.value)


def test_global_cli_string_false_does_not_accidentally_enable_booleans() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        global_path = root / "global.toml"
        global_path.write_text(
            '''
            [cli.output]
            copy = "false"
            no_clipboard = "false"
            [cli.environment]
            no_auto_chroot = "false"
            [cli.apply]
            validate = "false"
            ignore_checksum = "false"
            rollback_on_failure = "false"
            apply_notification = "false"
            artifact_script = "false"
            ''',
            encoding="utf-8",
        )
        args = argparse.Namespace(
            command="apply-overlay",
            stdout_payload="auto",
            machine_file="",
            copy=False,
            no_clipboard=False,
            copy_via="auto",
            theme="auto",
            auto_hud=None,
            hud_mode="auto",
            hud_overlay=False,
            hud_no_overlay=False,
            execution_environment="auto",
            no_auto_chroot=False,
            validate=False,
            no_manifest_check=False,
            ignore_checksum=False,
            keep_artifact=False,
            delete_artifact_on_failure=False,
            no_rollback_on_failure=False,
            no_apply_notification=False,
            no_artifact_script=False,
        )
        with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(global_path)}, clear=False):
            _apply_global_cli_defaults(args, ["apply-overlay", "artifact.tar.gz"])

    assert args.copy is False
    assert args.no_clipboard is False
    assert args.no_auto_chroot is False
    assert args.validate is False
    assert args.ignore_checksum is False
    assert args.no_rollback_on_failure is True
    assert args.no_apply_notification is True
    assert args.no_artifact_script is True


def test_global_config_doctor_reports_invalid_cli_shapes_and_values() -> None:
    config = tomllib.loads(
        '''
        cli = "oops"
        [not_allowed]
        value = true
        '''
    )
    entries = dict(global_config_doctor_entries(config))
    assert "invalid: [cli] must be a table" in entries["cli"]
    assert entries["not_allowed"] == "unknown global section"

    config = tomllib.loads(
        '''
        [cli.output]
        stdout_payload = "verbose"
        copy = "maybe"
        [cli.environment]
        execution_environment = "moon"
        '''
    )
    entries = dict(global_config_doctor_entries(config))
    assert "cli.output.stdout_payload" in entries
    assert "cli.output.copy" in entries
    assert "cli.environment.execution_environment" in entries


def test_universal_json_preserves_command_local_json_flags() -> None:
    stripped, requested = extract_universal_json_flag(["artifact", "verify", "out.tar.gz", "--json"])
    assert requested is True
    assert stripped == ["artifact", "verify", "out.tar.gz", "--json"]

    stripped, requested = extract_universal_json_flag(["--json", "artifact", "verify", "out.tar.gz", "--json"])
    assert requested is True
    assert stripped == ["artifact", "verify", "out.tar.gz", "--json"]


def test_runner_passthrough_recognizes_global_value_options_before_command() -> None:
    parse_args, passthrough = split_runner_passthrough_args([
        "--group", "release", "--ui-profile", "developer", "test", "--", "-k", "Smoke"
    ])
    assert parse_args == ["--group", "release", "--ui-profile", "developer", "test"]
    assert passthrough == ["-k", "Smoke"]


def test_bootstrap_receipts_ignore_malformed_schema_and_termux_arch_is_explicit() -> None:
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "state"
        destination = parse_destination("linux-amd64")
        receipt_dir = state / "bootstrap"
        receipt_dir.mkdir(parents=True)
        (receipt_dir / "linux-amd64.json").write_text('{"schema":"not-an-int"}', encoding="utf-8")
        with mock.patch("devtool.bootstrap.state_dir", return_value=state):
            assert load_receipt(destination) is None

    with mock.patch("devtool.bootstrap._running_under_termux", return_value=True), \
        mock.patch("devtool.bootstrap.platform.machine", return_value="x86_64"), \
        mock.patch.dict(os.environ, {"TERMUX_ARCH": "arm64"}, clear=False):
        destination = current_destination()
    assert destination.canonical == "termux"
    assert destination.expected_arch == "arm64"


def test_gradle_version_catalog_android_aliases_and_nested_roots_are_discovered() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        child = root / "samples" / "app"
        child.mkdir(parents=True)
        (root / "settings.gradle.kts").write_text('include(":lib")\n', encoding="utf-8")
        (root / "build.gradle.kts").write_text('plugins { java }\n', encoding="utf-8")
        (child / "settings.gradle.kts").write_text('pluginManagement { repositories { google() } }\n', encoding="utf-8")
        (child / "build.gradle.kts").write_text(
            'plugins { alias(libs.plugins.android.application) apply false }\n',
            encoding="utf-8",
        )
        (child / "gradle").mkdir()
        (child / "gradle" / "libs.versions.toml").write_text(
            '[plugins]\nandroid-application = { id = "com.android.application", version = "8.8.0" }\n',
            encoding="utf-8",
        )

        roots = {path.relative_to(root).as_posix() or "." for path in discover_gradle_roots(root)}
        android_roots = {path.relative_to(root).as_posix() or "." for path in discover_gradle_roots(root, android_only=True)}

    assert roots == {".", "samples/app"}
    assert android_roots == {"samples/app"}


def test_detect_reports_nested_non_dotnet_roots_and_ignores_devtool_workspace() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".devtool").mkdir()
        (root / ".devtool" / "package.json").write_text('{"private":true}', encoding="utf-8")
        nested = root / "apps" / "web"
        nested.mkdir(parents=True)
        (nested / "package.json").write_text('{"scripts":{"test":"node --test"}}', encoding="utf-8")

        assert detect_project_kind(root) == "node"
        info = detect_repo(root)

    assert info["node"]["manifest"] == "apps/web/package.json"
    assert info["node"]["roots"] == ["apps/web"]
    assert info["node"]["root_count"] == 1


def test_target_names_groups_variants_and_runner_shapes_are_strict() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, '[project]\nrunner="command"\n[targets."@bad"]\nrunner="command"')
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            with pytest.raises(SystemExit, match="Invalid target name"):
                load_project_config(str(root), None)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, '[project]\nrunner="command"\n[targets.api]\nrunner="auto"')
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            with pytest.raises(SystemExit, match="must declare a concrete runner"):
                load_project_config(str(root), None)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, '[project]\nrunner="command"\n[targets.api]\nrunner="command"\n[workspace.groups]\n"bad/name"=["api"]')
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            with pytest.raises(SystemExit, match="Invalid workspace group name"):
                load_project_config(str(root), None)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, '[project]\nrunner="command"\nvariant="release"\n[variants.release]\nfoo="bar"')
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            with pytest.raises(SystemExit, match="must be a TOML table"):
                load_project_config(str(root), None)


def test_toolchain_preflight_rejects_unexecutable_paths_and_doctor_does_not_abort() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        script = root / "script.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(stat.S_IRUSR | stat.S_IWUSR)
        spec = CommandSpec(name="script", argv=[str(script)], cwd=str(root))
        executable, status, detail = _executable_status(spec, detect_current_environment())
        assert executable == str(script)
        assert status == "incompatible"
        assert "not executable" in detail

        directory = root / "tool-dir"
        directory.mkdir()
        _, status, detail = _executable_status(CommandSpec(name="dir", argv=[str(directory)], cwd=str(root)), detect_current_environment())
        assert status == "incompatible"
        assert "directory" in detail

        config = ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root), toolchain_manager="mise")
        rows = dict(toolchain_doctor_rows(config, env={"PATH": ""}))
        assert rows["mise"] == "MISSING"
        assert rows["toolchain_active"] == "no"
        assert rows["toolchain_selected"].startswith("error:")


def test_empty_env_mapping_does_not_fall_back_to_os_environ_for_toolchains() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root), toolchain_manager="auto")
        with mock.patch.dict(os.environ, {"DEVTOOL_TOOLCHAIN": "none"}, clear=False):
            assert configured_toolchain_manager(config, env={}) == "auto"


def test_feature_flags_and_doctor_use_explicit_config_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_config(root, '[project]\nrunner="command"\n[features]\nevent_bus=true')
        config_dir = root / "configs"
        config_dir.mkdir()
        custom = config_dir / "custom.toml"
        custom.write_text('[project]\nrunner="command"\n[features]\nevent_bus=false\n', encoding="utf-8")

        flags = resolve_feature_flags(root, config_path=custom, environ={})
        with mock.patch.dict(os.environ, _no_global_env(root), clear=False):
            # A config inside a descendant directory cannot own the parent
            # repository. This denial is an intentional source-identity rule.
            with pytest.raises(SystemExit, match="config ownership mismatch"):
                load_project_config(str(root), str(custom))
            valid_custom = root / "custom.toml"
            valid_custom.write_text(custom.read_text(encoding="utf-8"), encoding="utf-8")
            config = load_project_config(str(root), str(valid_custom))
            payload = doctor_payload(config)

    assert flags.event_bus is False
    assert payload["features"]["event_bus"] is False
