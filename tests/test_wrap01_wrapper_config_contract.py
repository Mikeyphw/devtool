from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from devtool.cli import main
from devtool.config import global_config_doctor_entries, load_project_config, load_wrapper_project_config, resolve_config_dict
from devtool.doctor import doctor_payload
from devtool.wrapper_config import WRAPPER_SCHEMA_VERSION


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def test_valid_repository_wrapper_contract_is_typed_and_exposed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root,
            '''
            [project]
            runner = "command"

            [workspace]
            default_target = "android"

            [targets.android]
            runner = "command"

            [targets.android.workflows]
            codegen = ["runner:command#build"]

            [wrapper]
            schema = 1
            default_command = "build"

            [wrapper.commands.build]
            description = "Build app"
            devtool = "build"
            target = "android"
            aliases = ["bld"]
            allow_passthrough = true

            [wrapper.commands.build.options.release]
            type = "boolean"
            aliases = ["r"]
            command_argv = ["--variant", "release"]
            conflicts = ["debug"]

            [wrapper.commands.build.options.debug]
            type = "boolean"
            command_argv = ["--variant", "debug"]
            conflicts = ["release"]

            [wrapper.commands.build.options.abi]
            type = "choice"
            choices = ["arm64-v8a", "x86_64"]
            passthrough_argv = ["-Pabi={value}"]

            [wrapper.commands.codegen]
            workflow = "codegen"
            target = "android"
            ''',
        )
        config = _load(root)
        payload = resolve_config_dict(config)

    assert config.wrapper.schema == WRAPPER_SCHEMA_VERSION
    assert config.wrapper.default_command == "build"
    assert config.wrapper.commands["build"].options["abi"].choices == ("arm64-v8a", "x86_64")
    assert payload["wrapper"]["commands"]["build"]["execution_kind"] == "native"
    assert payload["wrapper"]["commands"]["codegen"]["execution_kind"] == "workflow"


def test_wrapper_is_repository_owned_and_global_config_cannot_inject_commands() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "repo"
        root.mkdir()
        _write(root, '[project]\nrunner = "command"')
        global_path = base / "global.toml"
        global_path.write_text(
            '''
            [wrapper]
            schema = 1
            [wrapper.commands.publish]
            devtool = "build"
            ''',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(global_path)}, clear=False):
            config = load_project_config(str(root), None, allow_ambiguous_target=True)

    assert config.wrapper.commands == {}
    assert dict(global_config_doctor_entries({"wrapper": {"schema": 1}}))["wrapper"] == "repo-scoped; keep this in .devtool.toml"


def test_wrapper_command_must_select_exactly_one_execution_kind() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.bad]
            devtool = "build"
            workflow = "codegen"
        ''')
        with pytest.raises(SystemExit, match="exactly one of 'devtool' or 'workflow'"):
            _load(root)


def test_wrapper_rejects_noncanonical_native_command_alias() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.bad]
            devtool = "b"
        ''')
        with pytest.raises(SystemExit, match="supported canonical wrapper command"):
            _load(root)


def test_wrapper_validates_target_group_and_mutually_exclusive_scope() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            [workspace.groups]
            both = ["a"]
            [wrapper.commands.bad]
            devtool = "build"
            target = "a"
            group = "both"
        ''')
        with pytest.raises(SystemExit, match="only one of target, group, or all_targets"):
            _load(root)


def test_workflow_binding_requires_declared_workflow_on_static_scope() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            [targets.b]
            runner = "command"
            [targets.a.workflows]
            codegen = ["runner:command#build"]
            [workspace.groups]
            all = ["a", "b"]
            [wrapper.commands.codegen]
            workflow = "codegen"
            group = "all"
        ''')
        with pytest.raises(SystemExit, match=r"not declared by target\(s\): b"):
            _load(root)


def test_workflow_binding_without_scope_requires_deterministic_repository_default() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            [targets.a.workflows]
            codegen = ["runner:command#build"]
            [targets.b]
            runner = "command"
            [targets.b.workflows]
            codegen = ["runner:command#build"]
            [wrapper.commands.codegen]
            workflow = "codegen"
        ''')
        with pytest.raises(SystemExit, match="requires a deterministic target scope"):
            _load(root)


def test_command_and_option_alias_collisions_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            aliases = ["test"]
            [wrapper.commands.test]
            devtool = "test"
        ''')
        with pytest.raises(SystemExit, match="command spelling 'test' collides"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.release]
            aliases = ["r"]
            [wrapper.commands.build.options.retry]
            aliases = ["r"]
        ''')
        with pytest.raises(SystemExit, match="option spelling 'r' collides"):
            _load(root)


def test_typed_defaults_choices_and_path_bases_are_validated() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.mode]
            type = "choice"
            choices = ["fast", "full"]
            default = "other"
        ''')
        with pytest.raises(SystemExit, match="is not one of"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.out]
            type = "path"
            relative_to = "moon"
        ''')
        with pytest.raises(SystemExit, match="relative_to must be one of"):
            _load(root)


def test_templates_are_token_only_and_only_value_placeholder_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.name]
            command_argv = ["--name={other}"]
        ''')
        with pytest.raises(SystemExit, match=r"only \{value\} is allowed"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.name]
            command_argv = ["bad\\nvalue"]
        ''')
        with pytest.raises(SystemExit, match="NUL or newline"):
            _load(root)


def test_passthrough_bindings_require_explicit_native_opt_in_and_workflows_reject_it() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
            [wrapper.commands.build.options.abi]
            passthrough_argv = ["-Pabi={value}"]
        ''')
        with pytest.raises(SystemExit, match="allow_passthrough is false"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [targets.a]
            runner = "command"
            [targets.a.workflows]
            codegen = ["runner:command#build"]
            [wrapper.commands.codegen]
            workflow = "codegen"
            target = "a"
            allow_passthrough = true
        ''')
        with pytest.raises(SystemExit, match="not supported for workflow wrapper commands"):
            _load(root)


def test_unknown_schema_fields_and_invalid_default_command_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper]
            schema = 1
            surprise = true
        ''')
        with pytest.raises(SystemExit, match="unknown field"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [wrapper]
            default_command = "missing"
            [wrapper.commands.build]
            devtool = "build"
        ''')
        with pytest.raises(SystemExit, match="default_command references unknown"):
            _load(root)


def test_positionals_validate_order_and_config_doctor_reports_wrapper_summary() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.install]
            devtool = "run"
            [[wrapper.commands.install.positionals]]
            name = "artifact"
            type = "path"
            relative_to = "cwd"
            required = false
            [[wrapper.commands.install.positionals]]
            name = "required_after_optional"
            required = true
        ''')
        with pytest.raises(SystemExit, match="cannot be required after an optional positional"):
            _load(root)

        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
        ''')
        stream = io.StringIO()
        with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False), redirect_stdout(stream):
            assert main(["--repo", str(root), "config", "doctor"]) == 0
        output = stream.getvalue()
        assert "wrapper_schema" in output
        assert "wrapper_commands" in output
        assert "build" in output


def test_wrapper_loader_can_inspect_mixed_multitarget_repo_before_target_selection() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8")
        (root / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
        _write(root, '''
            [project]
            runner = "auto"
            [targets.py]
            runner = "python"
            [targets.web]
            runner = "node"
            [wrapper.commands.build-web]
            devtool = "build"
            target = "web"
        ''')
        with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
            with pytest.raises(SystemExit, match="Multiple targets are configured and none is the default"):
                load_project_config(str(root), None)
            config = load_wrapper_project_config(str(root), None)

    assert config.selected_target == ""
    assert set(config.targets) == {"py", "web"}
    assert config.wrapper.commands["build-web"].target == "web"


def test_general_doctor_payload_exposes_wrapper_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, '''
            [project]
            runner = "command"
            [wrapper.commands.build]
            devtool = "build"
        ''')
        config = _load(root)
        payload = doctor_payload(config)

    assert payload["wrapper"]["schema"] == 1
    assert "build" in payload["wrapper"]["commands"]
    wrapper_checks = [item for item in payload["checks"] if item["name"] == "wrapper"]
    assert wrapper_checks and wrapper_checks[0]["status"] == "ok"
