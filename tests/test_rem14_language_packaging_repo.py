from __future__ import annotations

import os
from pathlib import Path

import pytest

from devtool.final_seal import final_seal_report
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import (
    CMakeRunner,
    DotnetRunner,
    GoRunner,
    MakeRunner,
    MavenRunner,
    NodeRunner,
    PythonRunner,
    RustRunner,
    _int_option,
    _shell_split_option,
)
from devtool.test_selection import TestDescriptor, apply_test_selection, resolve_test_selection, select_catalog


REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(root: Path, kind: str, options: dict[str, object] | None = None, **kwargs: object) -> ProjectConfig:
    return ProjectConfig(repo_root=root, project_root=str(root), kind=kind, runner_options=options or {}, **kwargs)


def test_string_false_options_do_not_enable_language_runner_features(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test.js"}}\n', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    node = _config(tmp_path, "node", {"node": {"clean_install": "false"}})
    assert NodeRunner().build_specs(node, "restore")[0].argv[1] == "install"

    dotnet = _config(tmp_path, "dotnet", {"dotnet": {"coalesce_validation": "false"}}, build_target="App.sln", test_target="App.sln")
    assert [spec.name for spec in DotnetRunner().build_validation_specs(dotnet)] == ["build", "test"]

    rust = _config(tmp_path, "rust", {"rust": {"coalesce_validation": "false", "lint": "false"}})
    assert [spec.name for spec in RustRunner().build_validation_specs(rust)] == ["build", "test"]

    go = _config(tmp_path, "go", {"go": {"vet": "false"}})
    assert [spec.name for spec in GoRunner().build_validation_specs(go)] == ["build", "test"]

    maven = _config(tmp_path, "maven", {"maven": {"coalesce_validation": "false"}})
    assert [spec.name for spec in MavenRunner().build_validation_specs(maven)] == ["build", "test"]


def test_python_numeric_and_shell_config_errors_are_actionable() -> None:
    assert _int_option("3", 0, name="python.flaky_retries", minimum=0) == 3
    with pytest.raises(SystemExit, match="python.flaky_retries"):
        _int_option("many", 0, name="python.flaky_retries", minimum=0)
    with pytest.raises(SystemExit, match="Invalid shell-style command"):
        _shell_split_option("pytest 'unterminated", name="config.test_command")


@pytest.mark.parametrize(
    "config_kwargs, option_fragment",
    [
        ({"runner_options": {"python": {"service": {"start": "python server.py", "enabled": "false"}}}}, "python-service-lifecycle"),
        ({"runner_options": {"python": {"package": {"reproducible": "false"}}}}, "python-reproducible"),
        ({"runner_options": {"python": {"security": {"enabled": "false"}}}}, "python-security"),
    ],
)
def test_python_string_false_is_not_truthy_for_optional_checks(tmp_path: Path, config_kwargs: dict[str, object], option_fragment: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n",
        encoding="utf-8",
    )
    config = _config(tmp_path, "python", config_kwargs["runner_options"])
    names = [spec.name for spec in PythonRunner().build_validation_specs(config, skip_restore=True, skip_build=True, skip_test=True)]
    assert option_fragment not in names


def test_catalog_ids_are_exact_not_substring() -> None:
    catalog = [
        TestDescriptor(id="DemoTest.case", name="case", suite="DemoTest", project="demo", framework="pytest"),
        TestDescriptor(id="DemoTest.caseSlow", name="caseSlow", suite="DemoTest", project="demo", framework="pytest"),
    ]
    config = _config(Path("/tmp/demo"), "python", {"test_selection": {"ids": ["DemoTest.case"]}})
    selected = select_catalog(catalog, resolve_test_selection(config))
    assert [item.id for item in selected] == ["DemoTest.case"]


def test_go_and_ctest_generic_selectors_are_escaped_exact_regexes(tmp_path: Path) -> None:
    go_config = _config(tmp_path, "go", {"test_selection": {"ids": ["TestFoo[bar]", "TestFoo.bar"]}})
    go_spec = apply_test_selection(go_config, CommandSpec("test", ["go", "test", "./..."], metadata={"contains_tests": True}))
    assert "-run" in go_spec.argv
    assert go_spec.argv[go_spec.argv.index("-run") + 1] == r"^(?:TestFoo\[bar\]|TestFoo\.bar)$"

    cmake_config = _config(tmp_path, "cmake", {"test_selection": {"ids": ["case.one"], "exclude_tags": ["slow+"]}})
    ctest_spec = apply_test_selection(cmake_config, CommandSpec("test", ["ctest", "--output-on-failure"], metadata={"contains_tests": True}))
    assert ctest_spec.argv[ctest_spec.argv.index("-R") + 1] == r"^(?:case\.one)$"
    assert ctest_spec.argv[ctest_spec.argv.index("-E") + 1] == r"^(?:slow\+)$"


def test_dotnet_maven_node_and_make_selector_contracts(tmp_path: Path) -> None:
    dotnet = _config(tmp_path, "dotnet", {"test_selection": {"ids": ["Demo.Tests.Unit.Case"], "tags": ["unit"]}})
    dotnet_spec = apply_test_selection(dotnet, CommandSpec("test", ["dotnet", "test"], metadata={"contains_tests": True}))
    assert "--filter" in dotnet_spec.argv
    assert "FullyQualifiedName=Demo.Tests.Unit.Case" in dotnet_spec.argv[dotnet_spec.argv.index("--filter") + 1]
    assert "Category=unit" in dotnet_spec.argv[dotnet_spec.argv.index("--filter") + 1]

    maven = _config(tmp_path, "maven", {"test_selection": {"ids": ["ExampleTest#caseOne"], "tags": ["fast"]}})
    maven_spec = apply_test_selection(maven, CommandSpec("test", ["mvn", "test"], metadata={"contains_tests": True}))
    assert "-Dtest=ExampleTest#caseOne" in maven_spec.argv
    assert "-Dgroups=fast" in maven_spec.argv
    with pytest.raises(SystemExit, match="Maven test selector"):
        apply_test_selection(_config(tmp_path, "maven", {"test_selection": {"ids": ["Example.*"]}}), CommandSpec("test", ["mvn", "test"], metadata={"contains_tests": True}))

    with pytest.raises(SystemExit, match="Node test selection cannot safely translate"):
        apply_test_selection(_config(tmp_path, "node", {"test_selection": {"ids": ["case"]}}), CommandSpec("test", ["npm", "run", "test"], metadata={"contains_tests": True}))
    node_native = apply_test_selection(_config(tmp_path, "node", {"test_selection": {"filter": "-- --runInBand"}}), CommandSpec("test", ["npm", "run", "test"], metadata={"contains_tests": True}))
    assert node_native.argv[-3:] == ["--", "--", "--runInBand"]

    (tmp_path / "Makefile").write_text("test:\n\t@true\nunit:\n\t@true\n", encoding="utf-8")
    make = _config(tmp_path, "make", {"test_selection": {"tasks": ["unit"]}})
    assert MakeRunner().build_specs(make, "test")[0].argv[-1] == "unit"
    with pytest.raises(SystemExit, match="Make test selection supports only Make targets"):
        MakeRunner().build_specs(_config(tmp_path, "make", {"test_selection": {"ids": ["unit"]}}), "test")


def test_cmake_and_meson_reconfigure_when_stamps_are_missing_or_stale(tmp_path: Path) -> None:
    cmake_runner = CMakeRunner()
    cmake_config = _config(tmp_path, "cmake")
    build_dir = tmp_path / ".devtool" / "build" / "cmake"
    (build_dir / "CMakeFiles").mkdir(parents=True)
    (build_dir / "CMakeCache.txt").write_text("cache\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    os.utime(build_dir / "CMakeCache.txt", (10, 10))
    os.utime(tmp_path / "CMakeLists.txt", (20, 20))
    assert not cmake_runner._configured(cmake_config)

    meson = _config(tmp_path, "meson")
    meson_dir = tmp_path / ".devtool" / "build" / "meson"
    (meson_dir / "meson-private").mkdir(parents=True)
    (meson_dir / "build.ninja").write_text("rule\n", encoding="utf-8")
    (meson_dir / "meson-private" / "coredata.dat").write_text("stamp\n", encoding="utf-8")
    (tmp_path / "meson.build").write_text("project('demo')\n", encoding="utf-8")
    os.utime(meson_dir / "meson-private" / "coredata.dat", (10, 10))
    os.utime(tmp_path / "meson.build", (20, 20))
    from devtool.project_runners import MesonRunner
    assert not MesonRunner()._configured(meson)


def test_install_packaging_android_and_root_bridge_source_contracts() -> None:
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    suffix_block = install.split("SKIP_SUFFIXES = {", 1)[1].split("}", 1)[0]
    assert '".jar"' not in suffix_block
    assert "oct(mode)" in install
    assert "root bridge module can be installed only on Android/Termux" in install
    assert "clipboard monitor service is available only for the desktop Control Center" in install
    assert "CLI: skipped (--skip-core)" in install

    uninstall = (REPO_ROOT / "uninstall.sh").read_text(encoding="utf-8")
    assert "--prefix" in uninstall
    assert "--skip-pip" in uninstall
    assert "DOTCLI_ZSH_DIR" in uninstall

    pack_repo = (REPO_ROOT / "pack_repo.sh").read_text(encoding="utf-8")
    assert "--mode=u+rw,go+rX,go-w" in pack_repo
    assert 'chmod 0644 "$manifest_path"' in pack_repo
    assert 'chmod 0644 "$output_path.sha256"' in pack_repo

    release = (REPO_ROOT / "tools" / "build-release.py").read_text(encoding="utf-8")
    assert "oct(permissions)" in release
    assert "os.chmod(temporary, 0o644)" in release

    candidate = (REPO_ROOT / "tools" / "make-release-candidate.sh").read_text(encoding="utf-8")
    assert "resolve_python_build_frontend" in candidate
    assert "DEVTOOL_PYTHON_BUILD_CMD" in candidate

    deb = (REPO_ROOT / "desktop" / "gtk-control-center" / "package-deb.sh").read_text(encoding="utf-8")
    assert "__pycache__" in deb
    assert "-m compileall" not in deb
    assert "unreadable by group/others" in deb
    assert "dpkg-deb --fsys-tarfile" in deb

    android_build = (REPO_ROOT / "android" / "devtool-live-hud" / "build.sh").read_text(encoding="utf-8")
    assert android_build.index("App directory not found") < android_build.index('run "${prefix[@]}" "$APT_COMMAND" update')
    assert "DRY RUN COMPLETE - no APK was built or copied" in android_build

    bridge = (REPO_ROOT / "root-module" / "devtool-bridge" / "bin" / "devtool-root-bridge").read_text(encoding="utf-8")
    assert "DEVTOOL_ANDROID_OPERATION_ID" in bridge
    assert 'wait "$child_pid"' in bridge
    assert 'exit "$status"' in bridge

    broker = (REPO_ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "RootCommandBroker.java").read_text(encoding="utf-8")
    assert "marked_pgids" in broker
    assert 'kill -TERM \\"-$pgid\\"' in broker
    assert 'kill -KILL \\"-$pgid\\"' in broker


def test_final_seal_backup_hygiene_scans_workspace_even_without_tracked_git_backup(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    (tmp_path / "module.py.bak").write_text("old\n", encoding="utf-8")
    report = final_seal_report(tmp_path)
    backup_check = next(item for item in report["checks"] if item["id"] == "repository.backup-hygiene")
    assert backup_check["status"] == "fail"
    assert "module.py.bak" in backup_check["details"]
