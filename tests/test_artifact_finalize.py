from __future__ import annotations

import hashlib
import json
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest import mock

from devtool.artifact_validation import run_artifact_finalize_script
from devtool.artifacts import artifact_finalize_phases, artifact_finalize_write_paths, validate_artifact
from devtool.cli import _artifact_script_result, _maybe_commit_after_apply
from devtool.models import OperationResult


def _archive(tmp_path: Path, script: bytes, *, phases=None, write_paths=None) -> tuple[Path, dict]:
    phases = phases or ["before_validation"]
    write_paths = write_paths or []
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "final-hook-test",
        "target": "devtool",
        "inventory": {"format": 1, "entries": []},
        "finalize": {
            "phases": phases,
            "write_paths": write_paths,
            "script": {
                "path": ".devtool-artifact/finalize.sh",
                "runner": "bash",
                "protocol": "json-v1",
                "timeout_seconds": 10,
                "sha256": hashlib.sha256(script).hexdigest(),
            },
        },
    }
    archive = tmp_path / "hook.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(".devtool-artifact.json", json.dumps(manifest))
        zf.writestr(".devtool-artifact/finalize.sh", script)
    return archive, manifest


def test_finalize_phase_aliases_and_write_paths_normalize() -> None:
    manifest = {
        "finalize": {
            "phases": ["before-validator", "pre_commit", "before_validation"],
            "write_paths": ["generated/tree", "state.txt", "generated/tree"],
            "script": {},
        }
    }
    assert artifact_finalize_phases(manifest) == ["before_validation", "before_commit"]
    assert artifact_finalize_write_paths(manifest) == ["generated/tree", "state.txt"]


def test_finalize_receives_json_v1_on_stdin_and_payload_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = br'''set -eu
cat > "$DEVTOOL_REPO_ROOT/hook-payload.json"
python3 -S - "$DEVTOOL_REPO_ROOT/hook-payload.json" "$DEVTOOL_HOOK_PAYLOAD_PATH" <<'PY2'
import json, pathlib, sys
stdin_payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
file_payload = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert stdin_payload == file_payload
assert stdin_payload["schema_version"] == 1
assert stdin_payload["event"] == "artifact_finalize"
assert stdin_payload["phase"] == "before_validation"
assert stdin_payload["target"] == "devtool"
pathlib.Path(stdin_payload["repo_root"], "hook-result.txt").write_text(stdin_payload["phase"])
PY2
'''
    archive, manifest = _archive(
        tmp_path,
        script,
        write_paths=["hook-payload.json", "hook-result.txt"],
    )
    result = run_artifact_finalize_script(
        repo,
        archive,
        phase="before_validation",
        manifest=manifest,
        validation_requested=True,
        commit_requested=True,
        commit_message="seal",
        commit_paths=["README.md"],
    )
    assert result is not None
    assert result.returncode == 0
    assert result.metadata["finalize_phase"] == "before_validation"
    payload = json.loads((repo / "hook-payload.json").read_text())
    assert payload["commit"]["requested"] is True
    assert payload["commit"]["message"] == "seal"
    assert payload["commit"]["paths"] == ["README.md"]
    assert (repo / "hook-result.txt").read_text() == "before_validation"


def test_finalize_rejects_undeclared_repo_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'printf unsafe > "$DEVTOOL_REPO_ROOT/undeclared.txt"\n'
    archive, manifest = _archive(tmp_path, script)
    result = run_artifact_finalize_script(repo, archive, phase="before_validation", manifest=manifest)
    assert result is not None
    assert result.returncode == 1
    assert result.failure_kind == "finalize_failure"
    assert "undeclared.txt" in result.output


def test_finalize_manifest_member_and_checksum_are_validated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive, _manifest = _archive(tmp_path, b"exit 0\n")
    validation = validate_artifact(repo, archive, check_source_state=False, check_repository_identity=False)
    assert validation.ok, validation.messages

    with zipfile.ZipFile(archive, "r") as zf:
        manifest = json.loads(zf.read(".devtool-artifact.json"))
        script = zf.read(".devtool-artifact/finalize.sh")
    manifest["finalize"]["script"]["sha256"] = "0" * 64
    bad = tmp_path / "bad-hook.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(".devtool-artifact.json", json.dumps(manifest))
        zf.writestr(".devtool-artifact/finalize.sh", script)
    validation = validate_artifact(repo, bad, check_source_state=False, check_repository_identity=False)
    assert not validation.ok
    assert any("Finalize script SHA-256 mismatch" in item for item in validation.messages)


def test_before_commit_finalize_blocks_git_commit_on_failure(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"zip")
    manifest = {"finalize": {"phases": ["before_commit"], "script": {}}}
    config = Namespace(repo_root=tmp_path)
    args = Namespace(
        command="apply-overlay",
        commit="message",
        artifact=str(artifact),
        _devtool_artifact_manifest=manifest,
        _devtool_run_id="run",
        target="devtool",
        solution="",
        validate=True,
        validate_only=False,
        verbose=False,
        dry_run=False,
        ignore_checksum=False,
    )
    existing = [OperationResult("apply-overlay", ["apply"], 0, 0.0, "ok", [])]
    failed = OperationResult(
        "artifact-finalize-script",
        ["artifact-finalize-script"],
        9,
        0.0,
        "blocked",
        [],
        failure_kind="finalize_failure",
    )
    with mock.patch("devtool.cli._artifact_commit_paths", return_value=["README.md"]), \
         mock.patch("devtool.cli.run_artifact_finalize_script", return_value=failed), \
         mock.patch("devtool.cli._git_commit_with_optional_log") as commit:
        results, raw = _maybe_commit_after_apply(config, args, list(existing), "")
    assert results[-1] is failed
    assert "blocked" in raw
    commit.assert_not_called()



def test_before_validation_finalize_runs_before_validator_and_can_block(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"zip")
    manifest = {"validation": {"script": {"path": ".devtool-artifact/validate.sh"}}}
    config = Namespace(repo_root=tmp_path)
    args = Namespace(
        no_artifact_script=False,
        _devtool_run_id="run",
        dry_run=False,
        ignore_checksum=False,
        commit="",
    )
    failed = OperationResult(
        "artifact-finalize-script",
        ["artifact-finalize-script"],
        4,
        0.0,
        "stop",
        [],
        failure_kind="finalize_failure",
    )
    with mock.patch("devtool.cli.run_artifact_finalize_script", return_value=failed) as finalize, \
         mock.patch("devtool.cli.run_artifact_validation_script") as validator, \
         mock.patch("devtool.cli._artifact_commit_paths", return_value=[]):
        result, raw = _artifact_script_result(
            config,
            args,
            artifact,
            manifest,
            target="devtool",
            solution="",
            log_path=None,
            tasker_progress=None,
        )
    assert result is failed
    assert "stop" in raw
    finalize.assert_called_once()
    validator.assert_not_called()
