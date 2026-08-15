from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from devtool.android_instrumentation import _parse_failed_ids
from devtool.config import detect_project_kind, detect_project_kind_details, discover_dotnet_candidates, load_project_config
from devtool.gradle_reports import collect_pytest_report
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import GenericCommandRunner, JvmGradleRunner, PythonRunner, get_project_runner
from devtool.python_support import (
    PythonProjectError,
    _specifier_satisfied,
    build_sync_command,
    inspect_python_project,
    resolve_environment,
    resolve_python_executable,
)
from devtool.test_selection import apply_test_selection, changed_paths, discover_gradle_tests, load_failed_test_ids, resolve_test_selection, save_failed_test_ids
from devtool.toolchains import apply_destination_toolchain_wrapper, apply_toolchain_wrapper


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True)


def _init_git(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Devtool Test")


def test_root_jvm_gradle_is_not_stolen_by_nested_android() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "settings.gradle.kts").write_text('rootProject.name = "jvm"\n', encoding="utf-8")
        (root / "build.gradle.kts").write_text('plugins { java }\n', encoding="utf-8")
        mobile = root / "examples" / "mobile"
        mobile.mkdir(parents=True)
        (mobile / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
        (mobile / "build.gradle.kts").write_text('plugins { id("com.android.application") version "8.8.0" apply false }\n', encoding="utf-8")
        details = detect_project_kind_details(root)
        config = load_project_config(str(root), None)
    assert details["kind"] == "gradle"
    assert (details["family"], details["build_system"], details["subtype"]) == ("jvm", "gradle", "jvm")
    assert config.kind == "gradle"
    assert isinstance(get_project_runner(config), JvmGradleRunner)


def test_root_csproj_outranks_nested_solution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "App.csproj").write_text("<Project />", encoding="utf-8")
        nested = root / "sample"
        nested.mkdir()
        (nested / "Sample.sln").write_text("", encoding="utf-8")
        candidates = discover_dotnet_candidates(root)
    assert candidates == [(root / "App.csproj").resolve()]


def test_nonexistent_root_detection_is_truthful_unknown() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist"
        assert detect_project_kind(missing) == "unknown"


def test_single_nested_python_root_is_used_consistently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested = root / "tools" / "worker"
        nested.mkdir(parents=True)
        (nested / "pyproject.toml").write_text("[project]\nname='worker'\nversion='1'\n", encoding="utf-8")
        config = load_project_config(str(root), None)
    assert config.kind == "python"
    assert Path(config.project_root) == nested.resolve()
    assert Path(config.build_target) == (nested / "pyproject.toml").resolve()


def test_jvm_gradle_has_no_android_inspect_doctor_or_instrumentation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="gradle", project_family="jvm", build_system="gradle", project_subtype="jvm")
        runner = get_project_runner(config)
        specs = runner.build_validation_specs(config)
    caps = runner.capabilities_for(config)
    assert not caps.inspect and not caps.doctor and not caps.instrumentation
    assert runner.inspect_rows(config) == [] and runner.doctor_rows(config) == []
    assert specs
    assert all(not str(spec.metadata.get("validation_scope", "")).startswith("android") for spec in specs)


def test_generic_restore_capability_is_truthful() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runner = GenericCommandRunner()
        none = ProjectConfig(repo_root=root, kind="command")
        yes = ProjectConfig(repo_root=root, kind="command", restore_command="echo restore")
    assert not runner.capabilities_for(none).restore
    assert runner.capabilities_for(yes).restore


def test_explicit_mise_missing_fails_and_clean_restore_are_wrapped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = ProjectConfig(repo_root=root, kind="command", project_root=str(root), toolchain_manager="mise")
        specs = [CommandSpec("restore", ["tool", "restore"], cwd=str(root)), CommandSpec("clean", ["tool", "clean"], cwd=str(root), destructive=True)]
        with mock.patch("devtool.toolchains.shutil.which", return_value=None), pytest.raises(SystemExit, match="explicitly requested"):
            apply_toolchain_wrapper(config, specs, env={"PATH": ""})
        with mock.patch("devtool.toolchains.shutil.which", return_value="/usr/bin/mise"):
            wrapped = apply_toolchain_wrapper(config, specs, env={"PATH": "/usr/bin"})
    assert all(spec.argv[:3] == ["mise", "exec", "--"] for spec in wrapped)


def test_explicit_mise_is_preserved_for_destination_backends_without_host_lookup() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = ProjectConfig(repo_root=root, kind="command", project_root=str(root), toolchain_manager="mise")
        specs = [
            CommandSpec("restore", ["tool", "restore"], cwd=str(root)),
            CommandSpec("build", ["tool", "build"], cwd=str(root)),
            CommandSpec("test", ["tool", "test"], cwd=str(root)),
            CommandSpec("clean", ["tool", "clean"], cwd=str(root), destructive=True),
        ]
        with mock.patch("devtool.toolchains.shutil.which", side_effect=AssertionError("host lookup is forbidden")):
            wrapped = apply_destination_toolchain_wrapper(config, specs, env={"PATH": ""})
    assert all(spec.argv[:3] == ["mise", "exec", "--"] for spec in wrapped)
    assert all(spec.metadata["toolchain_resolution"] == "destination" for spec in wrapped)


def test_explicit_python_missing_fails_without_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(PythonProjectError, match="Explicit Python interpreter is unavailable"):
            resolve_python_executable(root, "/definitely/missing/python", strict_configured=True)


def test_explicit_uv_environment_is_honored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
        info = inspect_python_project(root, "uv", "pytest")
        plan = resolve_environment(info, configured_python=sys.executable, environment=".uv-explicit")
    assert Path(plan.environment_root) == (root / ".uv-explicit").resolve()


def test_internal_requires_python_parser_handles_compatible_and_wildcard_edges() -> None:
    assert _specifier_satisfied((3, 12, 4), "~=3.12")
    assert not _specifier_satisfied((4, 0, 0), "~=3.12")
    assert _specifier_satisfied((3, 12, 4), "==3.12.*")
    assert not _specifier_satisfied((3, 11, 9), "==3.12.*")
    assert not _specifier_satisfied((3, 12, 0), ">=banana")


def test_chroot_requires_python_is_checked_by_guest_runtime_command() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\nrequires-python='>=3.12'\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", execution_environment="chroot", python_executable="python3", runner_options={"python": {"manager": "venv"}})
        spec = PythonRunner()._runtime_python_compatibility_spec(config)
    assert spec is not None
    assert spec.argv[:2] == ["python3", "-c"]
    assert "requires-python >=3.12" in spec.argv[2]


def test_offline_manager_matrix_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
        for manager in ("poetry", "pdm", "pipenv", "hatch"):
            info = inspect_python_project(root, manager, "pytest")
            plan = resolve_environment(info, configured_python=sys.executable)
            with pytest.raises(PythonProjectError, match="Offline restore"):
                build_sync_command(info, plan, offline=True)
        info = inspect_python_project(root, "uv", "pytest")
        plan = resolve_environment(info, configured_python=sys.executable)
        commands = build_sync_command(info, plan, offline=True)
    assert all("--offline" in command for command in commands)


def test_full_python_matrix_missing_runtime_fails_instead_of_skip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"python": {"manager": "venv", "test_framework": "pytest", "matrix": ["9.99"], "matrix_mode": "full"}})
        with mock.patch("devtool.project_runners.shutil.which", return_value=None), pytest.raises(SystemExit, match="cannot silently skip"):
            PythonRunner()._matrix_specs(config)


def test_uv_full_matrix_disables_python_downloads_and_propagates_offline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"python": {"manager": "uv", "test_framework": "pytest", "matrix": ["3.12"], "matrix_mode": "full", "offline": True}})
        with mock.patch("devtool.project_runners.shutil.which", return_value="/usr/bin/uv"):
            spec = PythonRunner()._matrix_specs(config)[0]
    assert "--no-python-downloads" in spec.argv and "--offline" in spec.argv


def test_changed_python_production_source_maps_to_test_node_and_includes_untracked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_git(root)
        (root / "pkg").mkdir(); (root / "tests").mkdir()
        (root / "pkg" / "parser.py").write_text("VALUE=1\n", encoding="utf-8")
        (root / "tests" / "test_parser.py").write_text("def test_parse(): pass\n", encoding="utf-8")
        _git(root, "add", "."); _git(root, "commit", "-qm", "base")
        (root / "pkg" / "parser.py").write_text("VALUE=2\n", encoding="utf-8")
        (root / "new.txt").write_text("new\n", encoding="utf-8")
        assert "new.txt" in changed_paths(root, "HEAD")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"python": {"manager": "venv", "test_framework": "pytest"}, "test_selection": {"changed": True}})
        selection = resolve_test_selection(config)
        spec = apply_test_selection(config, CommandSpec("test", ["pytest"], cwd=str(root), metadata={"test_framework": "pytest"}))
    assert "pkg/parser.py" in selection.paths and "new.txt" in selection.paths
    assert "tests/test_parser.py::test_parse" in spec.argv
    assert "pkg/parser.py" not in spec.argv


def test_changed_selection_surfaces_git_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with mock.patch("devtool.test_selection.subprocess.run", side_effect=OSError("git missing")), pytest.raises(SystemExit, match="Git is required"):
            changed_paths(root, "HEAD")


def test_pytest_multiple_selectors_and_tags_use_or_semantics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); (root / "tests").mkdir()
        (root / "tests" / "test_x.py").write_text("def test_a(): pass\ndef test_b(): pass\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"test_selection": {"selectors": ["alpha", "beta"], "tags": ["fast", "smoke"], "exclude_tags": ["slow"]}})
        spec = apply_test_selection(config, CommandSpec("test", ["pytest"], metadata={"test_framework": "pytest"}))
    k = spec.argv[spec.argv.index("-k") + 1]; m = spec.argv[spec.argv.index("-m") + 1]
    assert " or " in k and "alpha" in k and "beta" in k
    assert "(fast or smoke)" in m and "and not slow" in m


def test_gradle_zero_match_fails_and_direct_ids_are_fully_qualified() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); src = root / "src" / "test" / "java" / "com" / "example"; src.mkdir(parents=True)
        (src / "ParserTest.java").write_text("package com.example; class ParserTest { @Test public void parses() {} }", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="gradle", runner_options={"test_selection": {"tags": ["missing"]}})
        with pytest.raises(SystemExit, match="matched zero"):
            apply_test_selection(config, CommandSpec("test", ["gradle", "test"], metadata={"gradle_direct": True}))
        config.runner_options["test_selection"] = {"methods": ["parses"]}
        spec = apply_test_selection(config, CommandSpec("test", ["gradle", "test"], metadata={"gradle_direct": True}))
        ids = [item.id for item in discover_gradle_tests(root, android=False)]
    assert "com.example.ParserTest.parses" in spec.argv
    assert "com.example.ParserTest.parses" in ids


def test_gradle_mixed_method_tags_do_not_bleed_across_methods() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); src = root / "src" / "test" / "java" / "demo"; src.mkdir(parents=True)
        (src / "MixedTest.java").write_text(
            'package demo; class MixedTest {\n@Tag("fast") @Test public void quick() {}\n@Tag("slow") @Test public void heavy() {}\n}\n', encoding="utf-8")
        items = {item.name: item for item in discover_gradle_tests(root, android=False)}
    assert items["quick"].tags == ("fast",)
    assert items["heavy"].tags == ("slow",)


def test_gradle_tag_after_test_annotation_is_discovered_exactly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); src = root / "src" / "test" / "java" / "demo"; src.mkdir(parents=True)
        (src / "OrderTest.java").write_text(
            'package demo; class OrderTest {\n@Test\n@Tag("fast")\npublic void quick() {}\n}\n',
            encoding="utf-8",
        )
        items = discover_gradle_tests(root, android=False)
    assert [(item.id, item.tags) for item in items] == [("demo.OrderTest.quick", ("fast",))]


def test_unsupported_python_selector_is_rejected_not_ignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1'\n", encoding="utf-8")
        (root / "tox.ini").write_text("[tox]\nenvlist=py\n", encoding="utf-8")
        config = ProjectConfig(repo_root=root, project_root=str(root), kind="python", runner_options={"python": {"manager": "venv", "test_framework": "tox"}, "test_selection": {"selectors": ["parser"]}})
        with pytest.raises(SystemExit, match="tox supports Devtool selection only"):
            PythonRunner().build_specs(config, "test")


def test_failed_pytest_node_ids_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); report = root / "pytest.xml"
        report.write_text('<?xml version="1.0"?><testsuite tests="1" failures="1"><testcase classname="tests.test_parser.TestParser" name="test_bad" file="tests/test_parser.py"><failure message="boom"/></testcase></testsuite>', encoding="utf-8")
        collection = collect_pytest_report(root, report_path=str(report))
        save_failed_test_ids(root, collection.metadata["failed_ids"])
        assert collection.metadata["failed_ids"] == ["tests/test_parser.py::TestParser::test_bad"]
        assert load_failed_test_ids(root) == ["tests/test_parser.py::TestParser::test_bad"]


def test_instrumentation_failed_ids_are_native_class_method_ids() -> None:
    output = """INSTRUMENTATION_STATUS: class=com.example.ParserTest\nINSTRUMENTATION_STATUS: test=parses\nINSTRUMENTATION_STATUS_CODE: -2\n"""
    assert _parse_failed_ids(output) == ["com.example.ParserTest.parses"]
