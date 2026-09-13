from __future__ import annotations

import json
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devtool.android._standalone import artifact_variant
from devtool.android_artifacts import _filter_cached_artifacts_for_tasks
from devtool.artifact_pipeline import collect_and_process_artifacts
from devtool.artifact_registry import load_registry, prune_registry, register_paths
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def _zip(path: Path, name: str = "payload.txt", payload: bytes = b"payload") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, payload)
    return path


def _config(root: Path, *, kind: str = "python", artifacts: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind=kind,
        runner_options={"artifacts": dict(artifacts or {})},
    )


def test_string_false_metadata_does_not_request_collection_or_verification(tmp_path: Path) -> None:
    config = _config(tmp_path, artifacts={"verify": "false"})
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="build",
        spec_metadata={"collect_artifacts": "false", "verify_artifacts": "false"},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert metadata == {}
    assert issues == []


def test_string_false_copy_and_register_are_honored(tmp_path: Path) -> None:
    wheel = _zip(tmp_path / "dist" / "demo-1.0-py3-none-any.whl")
    config = _config(tmp_path, artifacts={"output_directory": ".devtool/build-artifacts"})
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package-check",
        spec_metadata={
            "collect_artifacts": True,
            "verify_artifacts": "false",
            "copy_artifacts": "false",
            "register_artifacts": "false",
        },
        result_metadata={"packages": {"items": [{"path": str(wheel), "valid": True, "type": "wheel"}]}},
        process_output="",
        returncode=0,
    )
    assert not issues
    assert metadata["items"][0]["path"].endswith("dist/demo-1.0-py3-none-any.whl")
    assert not (tmp_path / ".devtool" / "build-artifacts" / wheel.name).exists()
    assert metadata["registry"]["registered"] == 0
    assert load_registry(tmp_path)["items"] == []


def test_artifact_directory_recursive_string_false_is_non_recursive(tmp_path: Path) -> None:
    top = _zip(tmp_path / "out" / "top.zip")
    _zip(tmp_path / "out" / "nested" / "nested.zip")
    config = _config(tmp_path, artifacts={"directories": ["out"], "patterns": ["*.zip"], "recursive": "false"})
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="build",
        spec_metadata={"collect_artifacts": True},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    assert metadata["count"] == 1
    assert metadata["items"][0]["source_path"].endswith(top.name)


def test_gradle_structured_event_did_work_string_false_is_not_produced(tmp_path: Path) -> None:
    artifact = _zip(tmp_path / "build" / "libs" / "demo.jar", "META-INF/MANIFEST.MF")
    event = {
        "kind": "task-finish",
        "task": ":jar",
        "outcome": "success",
        "didWork": "false",
        "artifactOutputs": [str(artifact)],
    }
    config = _config(tmp_path, kind="gradle")
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=time.time() + 60,
        spec_name="package",
        spec_metadata={"collect_artifacts": True},
        result_metadata={},
        process_output="DEVTOOL_EVENT " + json.dumps(event),
        returncode=0,
    )
    assert not issues
    assert metadata["items"][0]["state"] != "produced"


def test_managed_output_copies_outside_artifacts_even_when_one_candidate_already_managed(tmp_path: Path) -> None:
    managed = _zip(tmp_path / ".devtool" / "build-artifacts" / "managed.zip", payload=b"managed")
    outside = _zip(tmp_path / "dist" / "outside.zip", payload=b"outside")
    config = _config(
        tmp_path,
        artifacts={"paths": [str(managed), str(outside)], "output_directory": ".devtool/build-artifacts"},
    )
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package",
        spec_metadata={"collect_artifacts": True},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    paths = {item["path"] for item in metadata["items"]}
    assert ".devtool/build-artifacts/managed.zip" in paths
    assert any(path.startswith(".devtool/build-artifacts/outside") for path in paths)
    assert not any(path == "dist/outside.zip" for path in paths)


def test_replace_collision_cannot_overwrite_another_artifact_in_same_batch(tmp_path: Path) -> None:
    first = _zip(tmp_path / "a" / "same.zip", payload=b"first")
    second = _zip(tmp_path / "b" / "same.zip", payload=b"second")
    config = _config(
        tmp_path,
        artifacts={
            "paths": [str(first), str(second)],
            "output_directory": ".devtool/build-artifacts",
            "output_name": "package.zip",
            "collision": "replace",
        },
    )
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package",
        spec_metadata={"collect_artifacts": True},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert not issues
    copied = sorted(Path(tmp_path, item["path"]) for item in metadata["items"])
    assert len(copied) == 2
    assert copied[0] != copied[1]
    assert {path.read_bytes() for path in copied} == {first.read_bytes(), second.read_bytes()}


def test_malformed_registry_schema_version_is_preserved_and_reset(tmp_path: Path) -> None:
    registry = tmp_path / ".devtool" / "artifacts" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"schema_version": "nope", "items": [], "verifications": []}), encoding="utf-8")
    data = load_registry(tmp_path)
    assert data["items"] == []
    assert data.get("warnings")
    assert list(registry.parent.glob("registry.corrupt-*.json"))


def test_registry_prune_handles_naive_timestamps_and_keeps_retained_file_references(tmp_path: Path) -> None:
    managed = _zip(tmp_path / ".devtool" / "build-artifacts" / "shared.zip")
    old = (datetime.now() - timedelta(days=10)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    registry = tmp_path / ".devtool" / "artifacts" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "schema_version": 2,
        "items": [
            {"id": "old", "path": ".devtool/build-artifacts/shared.zip", "registered_at": old},
            {"id": "new", "path": ".devtool/build-artifacts/shared.zip", "registered_at": new},
        ],
        "verifications": [],
    }), encoding="utf-8")
    result = prune_registry(tmp_path, older_than_days=2, delete_files=True)
    assert result["remaining"] == 1
    assert managed.exists()
    assert load_registry(tmp_path)["items"][0]["id"] == "new"


def test_register_paths_preserves_compound_tar_gz_type_and_boolean_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "pkg.tar.gz"
    artifact.write_bytes(b"not actually compressed for registry-only metadata")
    record = register_paths(
        tmp_path,
        [artifact],
        metadata={"verification_requested": "false", "valid": "false", "copied": "false"},
    )[0]
    assert record["type"] == "tar.gz"
    assert record["verification_requested"] is False
    assert record["valid"] is False
    assert record["metadata"]["copied"] is False


def test_tar_zst_verification_fails_closed_when_not_verifiable(tmp_path: Path) -> None:
    artifact = tmp_path / "dist" / "pkg.tar.zst"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"not-zstd")
    config = _config(tmp_path, artifacts={"paths": [str(artifact)], "verify": True})
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=0,
        spec_name="package",
        spec_metadata={},
        result_metadata={},
        process_output="",
        returncode=0,
    )
    assert metadata["invalid"] == 1
    assert any(issue.code == "ARTIFACT_INVALID" for issue in issues)
    assert metadata["registry"]["registered"] == 0


def test_node_npm_pack_json_does_not_mark_stale_file_as_produced(tmp_path: Path) -> None:
    package = tmp_path / "demo-1.0.0.tgz"
    package.write_bytes(b"stale package")
    old = time.time() - 3600
    os.utime(package, (old, old))
    payload = json.dumps([{"filename": package.name}])
    config = _config(tmp_path, kind="node")
    metadata, issues = collect_and_process_artifacts(
        config,
        project_root=tmp_path,
        started_at=time.time(),
        spec_name="package",
        spec_metadata={"collect_artifacts": True},
        result_metadata={},
        process_output=payload,
        returncode=0,
    )
    assert not issues
    assert metadata["items"][0]["state"] == "reused"


def test_android_variant_fallback_requires_build_type_specific_classifier() -> None:
    debug = Path("app/build/outputs/apk/free/debug/app.apk")
    release = Path("app/build/outputs/apk/free/release/app.apk")
    assert artifact_variant(debug) == "free-debug"
    assert artifact_variant(release) == "free-release"
    filtered = _filter_cached_artifacts_for_tasks([debug, release], [":app:assembleFreeRelease"])
    assert release in filtered
    assert debug not in filtered


def test_artifact_verification_failure_forces_nonzero_result(tmp_path: Path) -> None:
    bad = tmp_path / "dist" / "bad.zip"
    bad.parent.mkdir()
    bad.write_bytes(b"not a zip")
    config = _config(tmp_path, artifacts={"paths": [str(bad)], "verify": True})
    spec = CommandSpec(
        name="package",
        argv=["python", "-c", "print('built')"],
        metadata={"collect_artifacts": True, "verify_artifacts": True, "artifact_required": True},
    )
    results, raw = run_specs(config, [spec], verbose=False)
    assert len(results) == 1
    assert results[0].returncode == 1
    assert results[0].metadata["artifact_verification_failed"] is True
    assert "Devtool artifact verification failures" in results[0].output
    assert "ARTIFACT_INVALID" in raw
