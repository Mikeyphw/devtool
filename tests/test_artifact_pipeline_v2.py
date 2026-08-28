from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import zipfile

from devtool.artifact_pipeline import atomic_copy_artifact, collect_and_process_artifacts, format_artifact_summary
from devtool.artifact_registry import load_registry, register_paths, save_registry, verify_record
from devtool.cli import build_parser
from devtool.models import OperationResult, ProjectConfig
from devtool.runners import build_specs, build_validation_specs
from devtool.summary import build_json_summary
from devtool.validation_profiles import apply_profile


def _zip(path: Path, name: str = "payload.txt") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"payload")
    return path


def test_registry_registration_is_immutable_even_for_same_path(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "dist" / "demo.zip")
    first = register_paths(tmp_path, [artifact], metadata={"run_id": "one"})[0]
    second = register_paths(tmp_path, [artifact], metadata={"run_id": "two"})[0]
    registry = load_registry(tmp_path)
    assert registry["schema_version"] == 2
    assert len(registry["items"]) == 2
    assert first["id"] != second["id"]
    assert registry["items"][0]["run_id"] == "two"
    assert registry["items"][1]["run_id"] == "one"


def test_registry_corruption_is_preserved_instead_of_silently_lost(tmp_path: Path) -> None:
    registry_path = tmp_path / ".devtool" / "artifacts" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{broken", encoding="utf-8")
    data = load_registry(tmp_path)
    assert data["items"] == []
    assert data.get("warnings")
    assert list(registry_path.parent.glob("registry.corrupt-*.json"))


def test_atomic_copy_unique_and_fail_collision_modes(tmp_path: Path) -> None:
    source = _zip(tmp_path / "source.zip")
    destination = tmp_path / "out" / "package.zip"
    first = atomic_copy_artifact(source, destination, collision="unique")
    second = atomic_copy_artifact(source, destination, collision="unique")
    assert first.name == "package.zip"
    assert second.name == "package-2.zip"
    assert first.read_bytes() == second.read_bytes() == source.read_bytes()
    try:
        atomic_copy_artifact(source, destination, collision="fail")
    except FileExistsError:
        pass
    else:
        raise AssertionError("fail collision policy must reject an existing destination")


def test_failed_command_does_not_register_preexisting_artifact(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "dist" / "demo.zip")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="python",
        runner_options={"artifacts": {"required": True}},
    )
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=time.time() + 60,
        spec_name="build",
        spec_metadata={"collect_artifacts": True, "verify_artifacts": True},
        result_metadata={},
        process_output="",
        returncode=1,
    )
    assert metadata["count"] == 1
    assert metadata["registry"]["registered"] == 0
    assert "not registered" in metadata["registry"]["warning"]
    assert load_registry(tmp_path)["items"] == []
    assert not [issue for issue in issues if issue.code == "ARTIFACT_INVALID"]


def test_declared_artifacts_make_generic_build_output_runner_independent(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "custom" / "payload.zip")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="node",
        runner_options={
            "artifacts": {
                "paths": [str(artifact.relative_to(tmp_path))],
                "output_directory": ".devtool/build-artifacts",
                "verify": True,
            }
        },
    )
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="build",
        spec_metadata={},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    assert metadata["count"] == 1
    assert metadata["valid"] == 1
    copied = tmp_path / metadata["items"][0]["path"]
    assert copied.is_file()
    assert metadata["registry"]["registered"] == 1


def test_android_package_and_release_validation_verify_artifacts(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="android",
        runner_options={
            "tasks": {"build": ["assembleRelease"], "package": ["bundleRelease"]},
            "validation": {"resumable": True},
            "artifacts": {"required": True, "external_verification": "required"},
        },
    )
    package = build_specs(config, "package")[0]
    assert package.metadata["collect_artifacts"] is True
    assert package.metadata["verify_artifacts"] is True
    assert package.metadata["artifact_required"] is True
    assert "bundleRelease" in package.metadata["gradle_tasks"]

    validation = build_validation_specs(config)
    package_phase = next(spec for spec in validation if spec.metadata.get("validation_phase_name") == "package")
    assert package_phase.metadata["collect_artifacts"] is True
    assert package_phase.metadata["verify_artifacts"] is True
    assert package_phase.metadata["artifact_required"] is True
    assert "--output-dir" not in package_phase.argv or package_phase.metadata["collect_artifacts"]


def test_dotnet_package_uses_native_pack(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="dotnet", build_target="Demo.sln")
    spec = build_specs(config, "package")[0]
    assert spec.argv[:2] == ["dotnet", "pack"]
    assert "--no-restore" not in spec.argv
    assert spec.metadata["collect_artifacts"] is True
    assert spec.metadata["artifact_required"] is True


def test_root_bridge_packaging_is_reproducible_and_does_not_chmod_sources(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "root-module" / "devtool-bridge" / "module.prop"
    before = source.stat().st_mode & 0o777
    one = tmp_path / "one.zip"
    two = tmp_path / "two.zip"
    env = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
    subprocess.run([str(root / "build-root-bridge-module.sh"), str(one)], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(root / "build-root-bridge-module.sh"), str(two)], check=True, env=env, capture_output=True, text=True)
    assert one.read_bytes() == two.read_bytes()
    assert (source.stat().st_mode & 0o777) == before


def test_bun_package_uses_bun_pm_pack(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","version":"1.0.0","packageManager":"bun@1.4.0"}', encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="node")
    spec = build_specs(config, "package")[0]
    assert spec.argv[:3] == ["bun", "pm", "pack"]
    assert "--quiet" in spec.argv


def test_cmake_package_configures_cold_checkout(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="cmake")
    specs = build_specs(config, "package")
    assert [spec.name for spec in specs] == ["configure", "package"]
    assert specs[-1].metadata["collect_artifacts"] is True
    assert specs[-1].metadata["artifact_required"] is True


def test_meson_package_configures_cold_checkout(tmp_path: Path) -> None:
    (tmp_path / "meson.build").write_text("project('demo', 'c')\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="meson")
    specs = build_specs(config, "package")
    assert [spec.name for spec in specs] == ["configure", "package"]
    assert specs[-1].metadata["collect_artifacts"] is True
    assert specs[-1].metadata["artifact_required"] is True



def test_verification_is_append_only_and_does_not_mutate_production_record(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "dist" / "demo.zip")
    original = register_paths(tmp_path, [artifact], metadata={"run_id": "immutable"})[0]
    before = load_registry(tmp_path)["items"][0].copy()

    artifact.write_bytes(b"tampered")
    result = verify_record(tmp_path, original["id"])
    registry = load_registry(tmp_path)

    assert result["valid"] is False
    assert result.get("verification_id")
    assert registry["items"][0] == before
    assert len(registry["verifications"]) == 1
    event = registry["verifications"][0]
    assert event["artifact_id"] == original["id"]
    assert event["valid"] is False


def test_save_registry_cannot_rewrite_or_drop_existing_events(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "dist" / "demo.zip")
    original = register_paths(tmp_path, [artifact], metadata={"run_id": "one"})[0]
    stale = load_registry(tmp_path)
    stale["items"][0]["sha256"] = "0" * 64
    stale["items"].append({"id": "new-event", "path": "extra.zip", "sha256": "1" * 64})
    save_registry(tmp_path, stale)
    registry = load_registry(tmp_path)
    existing = next(item for item in registry["items"] if item["id"] == original["id"])
    assert existing["sha256"] == original["sha256"]
    assert any(item.get("id") == "new-event" for item in registry["items"])


def test_jvm_gradle_package_uses_structured_events_and_collects_jar_fallback(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="gradle",
        runner_options={"artifacts": {"required": True}},
    )
    spec = build_specs(config, "package")[0]
    assert "--init-script" in spec.argv
    assert "--no-configuration-cache" in spec.argv
    assert spec.metadata["progress_parser"] == "gradle"
    assert spec.metadata["collect_artifacts"] is True
    assert spec.metadata["artifact_required"] is True

    jar = _zip(tmp_path / "module" / "build" / "libs" / "demo.jar", "META-INF/MANIFEST.MF")
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package",
        spec_metadata={"collect_artifacts": True, "verify_artifacts": True, "artifact_required": True},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    assert metadata["count"] == 1
    assert metadata["items"][0]["source_path"].endswith("module/build/libs/demo.jar")
    assert metadata["registry"]["registered"] == 1
    assert jar.is_file()


def test_maven_multimodule_package_collects_module_artifact(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "module-a" / "target" / "demo.jar", "META-INF/MANIFEST.MF")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package",
        spec_metadata={"collect_artifacts": True, "verify_artifacts": True, "artifact_required": True},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    assert metadata["count"] == 1
    assert metadata["items"][0]["source_path"].endswith("module-a/target/demo.jar")
    assert artifact.is_file()


def test_dotnet_pack_honors_release_variant(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="dotnet", build_target="Demo.sln", variant="release")
    spec = build_specs(config, "package")[0]
    assert spec.argv[:2] == ["dotnet", "pack"]
    assert "-c" in spec.argv
    assert spec.argv[spec.argv.index("-c") + 1] == "Release"
    assert spec.metadata["variant"] == "release"


def test_artifact_summary_and_json_include_variant_verification_and_registry_id(tmp_path: Path) -> None:
    artifact_metadata = {
        "count": 1,
        "valid": 1,
        "invalid": 0,
        "verification_requested": True,
        "items": [{
            "path": ".devtool/build-artifacts/demo-release.jar",
            "type": "jar",
            "size": 123,
            "sha256": "a" * 64,
            "state": "produced",
            "valid": True,
            "variant": "release",
            "registry_id": "abc123",
        }],
        "registry": {"registered": 1, "ids": ["abc123"]},
    }
    text = format_artifact_summary(artifact_metadata)
    assert "[jar]" in text
    assert "123 B" in text
    assert "sha256=aaaaaaaaaaaa" in text
    assert "variant=release" in text
    assert "verified" in text
    assert "registry=abc123" in text

    result = OperationResult(
        name="package", argv=["tool", "package"], returncode=0, duration_seconds=0.1,
        output="", issues=[], metadata={"artifacts": artifact_metadata},
    )
    payload = build_json_summary([result], repo_root=str(tmp_path))
    assert payload["artifacts"]["schema_version"] == 1
    assert payload["artifacts"]["registry_ids"] == ["abc123"]
    assert payload["artifacts"]["items"][0]["variant"] == "release"
    assert payload["artifacts"]["items"][0]["registry_id"] == "abc123"


def test_python_package_build_defers_copy_and_registration_until_package_check(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='1.0'\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="python")
    specs = build_specs(config, "package")
    build = next(spec for spec in specs if spec.name == "build")
    package_check = next(spec for spec in specs if spec.name == "package-check")
    assert build.metadata["register_artifacts"] is False
    assert build.metadata["copy_artifacts"] is False
    assert build.metadata["artifact_stage"] == "package-build"
    assert package_check.metadata["collect_artifacts"] is True
    assert package_check.metadata["verify_artifacts"] is True
    assert package_check.metadata["artifact_required"] is True


def test_android_wrapper_resolves_artifact_from_current_run_only() -> None:
    source = (Path(__file__).resolve().parents[1] / "build-android-app.sh").read_text(encoding="utf-8")
    assert 'DEVTOOL_RUN_ID="$RUN_ID" devtool' in source
    assert 'str(item.get("run_id") or "") != run_id' in source



def test_python_registry_keeps_semantic_package_metadata(tmp_path: Path) -> None:
    wheel = _zip(tmp_path / "dist" / "demo-1.0-py3-none-any.whl", "demo/__init__.py")
    package_item = {
        "path": str(wheel),
        "type": "wheel",
        "name": "demo",
        "version": "1.0",
        "tags": ["py3-none-any"],
        "import_modules": ["demo"],
        "console_scripts": ["demo=demo:main"],
        "record_hashes_valid": True,
        "valid": True,
        "checks": [{"name": "wheel_metadata", "status": "passed", "detail": "ok"}],
    }
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="python",
        runner_options={"artifacts": {"output_directory": ".devtool/build-artifacts"}},
    )
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package-check",
        spec_metadata={"collect_artifacts": True, "verify_artifacts": True, "artifact_required": True},
        result_metadata={"packages": {"items": [package_item]}},
        process_output="",
        returncode=0,
    )
    assert not issues
    item = metadata["items"][0]
    assert item["metadata"]["name"] == "demo"
    assert item["metadata"]["version"] == "1.0"
    assert item["metadata"]["tags"] == ["py3-none-any"]
    assert item["metadata"]["import_modules"] == ["demo"]
    registered = load_registry(tmp_path)["items"][0]
    assert registered["producer"] == "python"
    assert registered["command"] == "package-check"
    assert registered["source_path"].endswith("dist/demo-1.0-py3-none-any.whl")
    assert registered["canonical_path"].startswith(".devtool/build-artifacts/")
    assert registered["exported_path"] == registered["canonical_path"]
    assert registered["verification_requested"] is True
    assert registered["verification_status"] == "valid"
    semantic = registered["metadata"]["metadata"]
    assert semantic["console_scripts"] == ["demo=demo:main"]
    assert semantic["record_hashes_valid"] is True


def test_native_package_defaults_cover_rust_go_maven_and_node(tmp_path: Path) -> None:
    # Rust
    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    rust = build_specs(ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="rust"), "package")[0]
    assert rust.argv[:2] == ["cargo", "package"]
    assert rust.metadata["artifact_required"] is True

    # Go uses an explicit output directory instead of ambiguous `go build ./...` outputs.
    go = build_specs(ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="go"), "package")[0]
    assert go.argv[:2] == ["go", "build"]
    assert "-o" in go.argv
    assert go.metadata["artifact_directories"]

    # Maven uses its native distributable lifecycle.
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    maven = build_specs(ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven"), "package")[0]
    assert "package" in maven.argv
    assert maven.metadata["artifact_required"] is True

    # npm/pnpm/yarn default to the package manager's pack operation.
    (tmp_path / "package.json").write_text('{"name":"demo","version":"1.0.0","packageManager":"npm@11.0.0"}', encoding="utf-8")
    node = build_specs(ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="node"), "package")[0]
    assert node.argv[:2] == ["npm", "pack"]
    assert node.metadata["artifact_required"] is True


def test_build_and_package_share_artifact_output_verification_cli() -> None:
    parser = build_parser()
    for command in ("build", "package"):
        args = parser.parse_args([
            command,
            "--output-dir", "dist/out",
            "--output-name", "demo",
            "--artifact-collision", "fail",
            "--no-verify-artifacts",
        ])
        assert args.artifact_output_dir == "dist/out"
        assert args.artifact_output_name == "demo"
        assert args.artifact_collision == "fail"
        assert args.verify_artifacts_cli is False



def test_builtin_release_profile_reaches_android_package_artifact_contract(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text("rootProject.name='demo'\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path,
        project_root=str(tmp_path),
        kind="android",
        runner_options={"validation": {"resumable": True}, "tasks": {"build": ["assembleRelease"]}},
    )
    args = argparse.Namespace(
        validation_workflow_profile="release",
        validation_resource_profile="automatic",
        skip_restore=False,
        skip_build=False,
        skip_test=False,
    )
    assert apply_profile(config, args) == "release"
    assert config.runner_options["artifacts"]["required"] is True
    assert config.runner_options["artifacts"]["external_verification"] == "required"
    package = next(
        spec for spec in build_validation_specs(config)
        if spec.metadata.get("validation_phase_name") == "package"
    )
    assert package.metadata["collect_artifacts"] is True
    assert package.metadata["verify_artifacts"] is True
    assert package.metadata["artifact_required"] is True
