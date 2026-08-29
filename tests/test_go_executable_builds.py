from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from devtool.artifact_pipeline import collect_and_process_artifacts
from devtool.config import load_project_config
from devtool.models import ProjectConfig
from devtool.project_runners import GoRunner

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain is unavailable")


def _run(spec) -> subprocess.CompletedProcess[str]:
    return subprocess.run(spec.argv, cwd=spec.cwd, env={**__import__("os").environ, **spec.env}, text=True, capture_output=True, check=False)


def _module(root: Path, module: str = "example.com/demo") -> None:
    subprocess.run(["go", "mod", "init", module], cwd=root, check=True, stdout=subprocess.DEVNULL)


def _main(root: Path, relative: str) -> None:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")


def _library(root: Path, relative: str = "internal/lib") -> None:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "lib.go").write_text("package lib\nconst Value = 1\n", encoding="utf-8")


def _config(root: Path, **go_options: object) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="go",
        runner_options={"go": go_options} if go_options else {},
    )


def test_go_build_discovers_main_packages_and_writes_real_executables(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "cmd/foo")
    _main(tmp_path, "cmd/bar")
    _library(tmp_path)

    spec = GoRunner().build_specs(_config(tmp_path), "build")[0]
    assert spec.argv[:2] == ["sh", "-c"]
    assert spec.metadata["collect_artifacts"] is True
    assert spec.metadata["verify_artifacts"] is True
    assert spec.metadata["artifact_required"] is False

    completed = _run(spec)
    assert completed.returncode == 0, completed.stderr
    output_dir = tmp_path / ".devtool" / "build" / "go"
    assert {path.name for path in output_dir.iterdir()} == {"foo", "bar"}
    assert "DEVTOOL_GO_ARTIFACT" in completed.stdout
    assert "DEVTOOL_GO_BUILDINFO" in completed.stdout


def test_go_build_registers_verified_binary_and_embedded_build_metadata(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "cmd/demo")
    config = _config(tmp_path)
    spec = GoRunner().build_specs(config, "build")[0]
    started = time.time()
    completed = _run(spec)
    assert completed.returncode == 0, completed.stderr

    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=started,
        spec_name="build",
        spec_metadata=dict(spec.metadata),
        result_metadata=dict(spec.metadata),
        process_output=completed.stdout + completed.stderr,
        returncode=completed.returncode,
    )
    assert not issues
    assert metadata["count"] == 1
    item = metadata["items"][0]
    assert item["type"] == "go-binary"
    assert item["valid"] is True
    assert item["state"] == "produced"
    assert item["registry_id"]
    assert item["metadata"]["go_package"] == "example.com/demo/cmd/demo"
    build_info = item["metadata"]["go_build_info"]
    assert build_info["module_path"] == "example.com/demo"
    assert build_info["go_version"].startswith("go")
    assert build_info["goos"]
    assert build_info["goarch"]
    assert any(check["name"] == "go_build_info" and check["status"] == "passed" for check in item["checks"])


def test_go_library_only_build_falls_back_to_compile_check(tmp_path: Path) -> None:
    _module(tmp_path)
    _library(tmp_path, "pkg/lib")
    spec = GoRunner().build_specs(_config(tmp_path), "build")[0]
    completed = _run(spec)
    assert completed.returncode == 0, completed.stderr
    assert "DEVTOOL_GO_ARTIFACT" not in completed.stdout
    output_dir = tmp_path / ".devtool" / "build" / "go"
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_go_library_only_package_fails_with_actionable_error(tmp_path: Path) -> None:
    _module(tmp_path)
    _library(tmp_path, "pkg/lib")
    spec = GoRunner().build_specs(_config(tmp_path), "package")[0]
    completed = _run(spec)
    assert completed.returncode == 2
    assert "found no package main targets" in completed.stderr
    assert "build_targets" in completed.stderr


def test_go_duplicate_main_basenames_fail_instead_of_overwriting(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "a/server")
    _main(tmp_path, "b/server")
    spec = GoRunner().build_specs(_config(tmp_path), "build")[0]
    completed = _run(spec)
    assert completed.returncode == 2
    assert "Go executable name collision: server" in completed.stderr
    assert "[go.outputs]" in completed.stderr
    assert not (tmp_path / ".devtool" / "build" / "go").exists()


def test_go_output_overrides_disambiguate_colliding_main_packages(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "a/server")
    _main(tmp_path, "b/server")
    config = _config(
        tmp_path,
        build_targets=["./a/server", "./b/server"],
        outputs={"./a/server": "server-a", "./b/server": "server-b"},
    )
    completed = _run(GoRunner().build_specs(config, "build")[0])
    assert completed.returncode == 0, completed.stderr
    output_dir = tmp_path / ".devtool" / "build" / "go"
    assert {path.name for path in output_dir.iterdir()} == {"server-a", "server-b"}


def test_go_work_build_discovers_main_packages_across_workspace_modules(tmp_path: Path) -> None:
    module_a = tmp_path / "module-a"
    module_b = tmp_path / "module-b"
    module_a.mkdir()
    module_b.mkdir()
    _module(module_a, "example.com/a")
    _module(module_b, "example.com/b")
    _main(module_a, "cmd/a")
    _main(module_b, "cmd/b")
    subprocess.run(["go", "work", "init", "./module-a", "./module-b"], cwd=tmp_path, check=True)

    completed = _run(GoRunner().build_specs(_config(tmp_path), "build")[0])
    assert completed.returncode == 0, completed.stderr
    output_dir = tmp_path / ".devtool" / "build" / "go"
    assert {path.name for path in output_dir.iterdir()} == {"a", "b"}


def test_go_root_main_uses_module_basename_for_default_binary_name(tmp_path: Path) -> None:
    _module(tmp_path, "example.com/acme/root-tool")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")

    completed = _run(GoRunner().build_specs(_config(tmp_path), "build")[0])
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / ".devtool" / "build" / "go" / "root-tool").is_file()


def test_go_single_build_target_alias_limits_executable_discovery(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "cmd/one")
    _main(tmp_path, "cmd/two")
    config = _config(tmp_path, build_target="./cmd/two")

    completed = _run(GoRunner().build_specs(config, "build")[0])
    assert completed.returncode == 0, completed.stderr
    output_dir = tmp_path / ".devtool" / "build" / "go"
    assert {path.name for path in output_dir.iterdir()} == {"two"}


def test_go_packages_scope_does_not_limit_executable_discovery_when_build_targets_are_set(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "cmd/app")
    _library(tmp_path, "pkg/lib")
    config = _config(tmp_path, packages=["./pkg/..."], build_targets=["./cmd/..."])
    runner = GoRunner()

    build = runner.build_specs(config, "build")[0]
    test = runner.build_specs(config, "test")[0]
    completed = _run(build)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / ".devtool" / "build" / "go" / "app").is_file()
    assert test.argv[-1] == "./pkg/..."


def test_go_outputs_table_is_loaded_from_devtool_toml(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "a/server")
    _main(tmp_path, "b/server")
    (tmp_path / ".devtool.toml").write_text(
        """\
[project]
kind = "go"

[go]
build_targets = ["./a/server", "./b/server"]
build_directory = ".devtool/custom-go-build"

[go.outputs]
"./a/server" = "server-a"
"./b/server" = "server-b"
""",
        encoding="utf-8",
    )

    config = load_project_config(str(tmp_path), None)
    options = config.runner_options["go"]
    assert options["build_targets"] == ["./a/server", "./b/server"]
    assert options["outputs"] == {"./a/server": "server-a", "./b/server": "server-b"}
    completed = _run(GoRunner().build_specs(config, "build")[0])
    assert completed.returncode == 0, completed.stderr
    output_dir = tmp_path / ".devtool" / "custom-go-build"
    assert {path.name for path in output_dir.iterdir()} == {"server-a", "server-b"}


def test_go_work_library_only_build_falls_back_to_workspace_compile_check(tmp_path: Path) -> None:
    module_a = tmp_path / "module-a"
    module_b = tmp_path / "module-b"
    module_a.mkdir()
    module_b.mkdir()
    _module(module_a, "example.com/a")
    _module(module_b, "example.com/b")
    _library(module_a, "pkg/a")
    _library(module_b, "pkg/b")
    subprocess.run(["go", "work", "init", "./module-a", "./module-b"], cwd=tmp_path, check=True)

    completed = _run(GoRunner().build_specs(_config(tmp_path), "build")[0])
    assert completed.returncode == 0, completed.stderr
    assert "DEVTOOL_GO_ARTIFACT" not in completed.stdout


def test_go_windows_cross_build_adds_exe_suffix_and_verifies_build_info(tmp_path: Path) -> None:
    _module(tmp_path)
    _main(tmp_path, "cmd/demo")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="go",
        env={"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"},
    )
    spec = GoRunner().build_specs(config, "build")[0]
    started = time.time()
    completed = _run(spec)
    assert completed.returncode == 0, completed.stderr
    binary = tmp_path / ".devtool" / "build" / "go" / "demo.exe"
    assert binary.is_file()

    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=started,
        spec_name="build",
        spec_metadata=dict(spec.metadata),
        result_metadata=dict(spec.metadata),
        process_output=completed.stdout + completed.stderr,
        returncode=completed.returncode,
    )
    assert not issues
    item = metadata["items"][0]
    assert item["path"].endswith("demo.exe")
    assert item["metadata"]["executable_format"] == "pe"
    assert item["metadata"]["go_build_info"]["goos"] == "windows"
    assert item["metadata"]["go_build_info"]["goarch"] == "amd64"

@pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable")
def test_go_artifact_metadata_records_vcs_provenance_when_embedded(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Test"], cwd=tmp_path, check=True)
    _module(tmp_path, "example.com/vcs-provenance-demo")
    _main(tmp_path, "cmd/demo")
    (tmp_path / ".gitignore").write_text(".devtool/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    config = _config(tmp_path)
    spec = GoRunner().build_specs(config, "build")[0]
    started = time.time()
    completed = _run(spec)
    assert completed.returncode == 0, completed.stderr
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=started,
        spec_name="build",
        spec_metadata=dict(spec.metadata),
        result_metadata=dict(spec.metadata),
        process_output=completed.stdout + completed.stderr,
        returncode=completed.returncode,
    )
    assert not issues
    build_info = metadata["items"][0]["metadata"]["go_build_info"]
    assert build_info["vcs"] == "git"
    assert build_info["vcs_revision"] == revision
    assert build_info["vcs_modified"] in {"true", "false"}
