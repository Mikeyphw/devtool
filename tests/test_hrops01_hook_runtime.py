from __future__ import annotations

import hashlib
import json
import subprocess
from argparse import Namespace
from pathlib import Path
from unittest import mock

import pytest

from devtool.artifact_inspection import inspect_artifact
from devtool.artifact_validation import run_artifact_finalize_script, run_artifact_preflight_script, run_artifact_validation_script
from devtool.artifacts import artifact_hook_capabilities, artifact_preflight_phases, preview_artifact, validate_artifact, write_archive_entries
from devtool.cli import _artifact_script_result, _maybe_commit_after_apply, _record_apply_history
from devtool.models import Issue, OperationResult


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_with_hooks(
    path: Path,
    *,
    preflight: bytes | None = None,
    validator: bytes | None = None,
    finalize: bytes | None = None,
    preflight_protocol: str | None = None,
    validation_protocol: str | None = None,
    preflight_phases: list[str] | None = None,
    finalize_phases: list[str] | None = None,
    validation_write_paths: list[str] | None = None,
    capabilities: dict[str, list[str]] | None = None,
) -> tuple[Path, dict]:
    manifest: dict = {"schema_version": 2, "type": "overlay", "target": "devtool"}
    entries: dict[str, bytes] = {}
    capabilities = capabilities or {}
    if preflight is not None:
        script: dict = {"path": ".devtool-artifact/preflight.sh", "runner": "bash", "sha256": sha(preflight), "timeout_seconds": 20}
        if preflight_protocol:
            script["protocol"] = preflight_protocol
        manifest["preflight"] = {
            "phases": preflight_phases or ["before_extract"],
            "write_paths": ["preflight.json"],
            "capabilities": capabilities.get("preflight", []),
            "script": script,
        }
        entries[".devtool-artifact/preflight.sh"] = preflight
    if validator is not None:
        script = {"path": ".devtool-artifact/validate.sh", "runner": "bash", "sha256": sha(validator), "timeout_seconds": 20}
        if validation_protocol:
            script["protocol"] = validation_protocol
        manifest["validation"] = {
            "required": True,
            "restore": False,
            "build": False,
            "test": False,
            "write_paths": list(validation_write_paths or []),
            "capabilities": capabilities.get("validation", []),
            "script": script,
        }
        entries[".devtool-artifact/validate.sh"] = validator
    if finalize is not None:
        manifest["finalize"] = {
            "phases": finalize_phases or ["before_validation", "before_commit"],
            "write_paths": ["finalize.json"],
            "capabilities": capabilities.get("finalize", []),
            "script": {
                "path": ".devtool-artifact/finalize.sh",
                "runner": "bash",
                "protocol": "json-v1",
                "sha256": sha(finalize),
                "timeout_seconds": 20,
            },
        }
        entries[".devtool-artifact/finalize.sh"] = finalize
    entries[".devtool-artifact.json"] = json.dumps(manifest).encode()
    write_archive_entries(path, entries)
    return path, manifest


def test_preflight_json_v1_preserves_legacy_env_and_common_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    code = (
        'import json,os,pathlib,sys; '
        'p=json.load(sys.stdin); f=json.loads(pathlib.Path(sys.argv[1]).read_text()); '
        'assert p==f; assert p["schema_version"]==1; assert p["event"]=="artifact_preflight"; '
        'assert p["hook"]["kind"]=="preflight"; assert p["phase"]=="before_extract"; '
        'assert p["target"]=="devtool"; assert p["artifact"]["private_copy"] is True; assert "source_path" not in p["artifact"]; '
        'pathlib.Path(sys.argv[2]).write_text(json.dumps({"legacy":os.environ["DEVTOOL_PREFLIGHT_PHASE"],"protocol":os.environ["DEVTOOL_HOOK_PROTOCOL"]}))'
    )
    script = f'''set -eu
python3 -S -c '{code}' "$DEVTOOL_HOOK_PAYLOAD_PATH" "$DEVTOOL_REPO_ROOT/preflight.json"
'''.encode()
    archive, manifest = archive_with_hooks(tmp_path / "p.zip", preflight=script, preflight_protocol="json-v1")
    result = run_artifact_preflight_script(repo, archive, phase="before_extract", manifest=manifest)
    assert result and result.returncode == 0, result.output if result else ""
    assert json.loads((repo / "preflight.json").read_text()) == {"legacy": "before_extract", "protocol": "json-v1"}
    assert result.metadata["hook_event"]["protocol"] == "json-v1"


def test_legacy_preflight_remains_env_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''set -eu
test "$DEVTOOL_PREFLIGHT_PHASE" = before_extract
test "$DEVTOOL_HOOK_PROTOCOL" = legacy-env
test -z "${DEVTOOL_HOOK_PAYLOAD_PATH:-}"
printf ok > "$DEVTOOL_REPO_ROOT/preflight.json"
'''
    archive, manifest = archive_with_hooks(tmp_path / "legacy.zip", preflight=script)
    result = run_artifact_preflight_script(repo, archive, phase="before_extract", manifest=manifest)
    assert result and result.returncode == 0
    assert (repo / "preflight.json").read_text() == "ok"


def test_validator_json_result_surfaces_warnings_evidence_and_annotations(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    code = (
        'import json,pathlib,sys; p=json.load(sys.stdin); '
        'assert p["event"]=="artifact_validation"; assert p["hook"]["kind"]=="validation"; '
        'pathlib.Path(sys.argv[1]).write_text(json.dumps({"schema_version":1,"status":"warn","summary":"validator evidence",'
        '"warnings":["soft warning"],"annotations":[{"severity":"info","code":"proof","message":"evidence captured"}],'
        '"evidence":{"answer":42}}))'
    )
    script = f'''set -eu
python3 -S -c '{code}' "$DEVTOOL_HOOK_RESULT_PATH"
'''.encode()
    archive, manifest = archive_with_hooks(tmp_path / "v.zip", validator=script, validation_protocol="json-v1")
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 0, result.output if result else ""
    assert result.metadata["hook_result"]["evidence"] == {"answer": 42}
    assert result.metadata["hook_event"]["status"] == "warn"
    assert any(issue.severity == "warning" and issue.message == "soft warning" for issue in result.issues)
    assert "Hook result: warn: validator evidence" in result.output


def test_structured_hook_fail_status_fails_even_when_process_exits_zero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''set -eu
printf '%s\n' '{"schema_version":1,"status":"fail","summary":"blocked"}' > "$DEVTOOL_HOOK_RESULT_PATH"
'''
    archive, manifest = archive_with_hooks(tmp_path / "fail.zip", validator=script, validation_protocol="json-v1")
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 1
    assert result.failure_kind == "validation_failure"
    assert result.metadata["hook_event"]["status"] == "fail"


def test_malformed_structured_result_is_a_hook_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''set -eu
printf 'not-json' > "$DEVTOOL_HOOK_RESULT_PATH"
'''
    archive, manifest = archive_with_hooks(tmp_path / "bad-result.zip", validator=script)
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 2
    assert result.failure_kind == "validation_failure"
    assert "invalid JSON" in result.output


def test_capabilities_are_normalized_and_truthfully_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'printf ok > "$DEVTOOL_REPO_ROOT/preflight.json"\n'
    archive, manifest = archive_with_hooks(
        tmp_path / "caps.zip", preflight=script,
        capabilities={"preflight": ["repo-read", "repo_write", "network", "subprocess"]},
    )
    assert artifact_hook_capabilities(manifest, "preflight") == ["repo_read", "repo_write", "network", "subprocess"]
    result = run_artifact_preflight_script(repo, archive, phase="before_extract", manifest=manifest)
    report = result.metadata["hook_event"]["effective_capabilities"]
    assert report["effective"]["repo_write"]["enforcement"] == "partially_enforced"
    assert report["effective"]["network"]["enforcement"] == "declared_only"
    assert "network namespace" in report["effective"]["network"]["reason"]


@pytest.mark.parametrize("capability", ["root", "filesystem_all", "magic"])
def test_unknown_hook_capabilities_are_rejected(tmp_path: Path, capability: str) -> None:
    archive, _ = archive_with_hooks(tmp_path / "bad-cap.zip", preflight=b"exit 0\n", capabilities={"preflight": [capability]})
    validation = validate_artifact(tmp_path, archive, check_source_state=False, check_repository_identity=False)
    assert not validation.ok
    assert any("Unsupported preflight.capabilities capability" in item for item in validation.messages)


def test_invalid_preflight_and_validation_protocols_are_rejected(tmp_path: Path) -> None:
    p, _ = archive_with_hooks(tmp_path / "bad-p.zip", preflight=b"exit 0\n", preflight_protocol="json-v2")
    assert any("preflight.script.protocol" in item for item in validate_artifact(tmp_path, p, check_source_state=False, check_repository_identity=False).messages)
    v, _ = archive_with_hooks(tmp_path / "bad-v.zip", validator=b"exit 0\n", validation_protocol="yaml")
    assert any("validation.script.protocol" in item for item in validate_artifact(tmp_path, v, check_source_state=False, check_repository_identity=False).messages)


def test_preflight_supports_both_extract_phases(tmp_path: Path) -> None:
    _, manifest = archive_with_hooks(tmp_path / "two.zip", preflight=b"exit 0\n", preflight_phases=["before-extract", "after_extract"])
    assert artifact_preflight_phases(manifest) == ["before_extract", "after_extract"]


def test_validation_declared_writes_are_enforced_and_previewed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'printf good > "$DEVTOOL_REPO_ROOT/generated.txt"\nprintf bad > "$DEVTOOL_REPO_ROOT/other.txt"\n'
    archive, manifest = archive_with_hooks(tmp_path / "writes.zip", validator=script, validation_write_paths=["generated.txt"])
    preview = preview_artifact(repo, archive, snapshot=False)
    assert {item["kind"] for item in preview["changes"] if item["path"] == "generated.txt"} == {"validation-write"}
    result = run_artifact_validation_script(repo, archive, manifest=manifest)
    assert result and result.returncode == 1
    assert "other.txt" in result.output
    assert "generated.txt" in result.metadata["hook_event"]["detected_writes"]


def test_inspect_artifact_exposes_hook_configuration(tmp_path: Path) -> None:
    archive, _ = archive_with_hooks(
        tmp_path / "inspect.zip", preflight=b"exit 0\n", validator=b"exit 0\n", finalize=b"exit 0\n",
        preflight_protocol="json-v1", validation_protocol="json-v1",
        preflight_phases=["before_extract", "after_extract"],
        finalize_phases=["before_validation", "after_validation", "before_commit", "after_commit"],
        capabilities={"preflight": ["repo_read"], "validation": ["subprocess"], "finalize": ["repo_read"]},
    )
    payload = inspect_artifact(archive)
    assert payload["hooks"]["preflight"]["script"]["protocol"] == "json-v1"
    assert payload["hooks"]["preflight"]["phases"] == ["before_extract", "after_extract"]
    assert payload["hooks"]["validation"]["capabilities"] == ["subprocess"]
    assert payload["hooks"]["finalize"]["phases"][-1] == "after_commit"


def test_after_validation_runs_after_validator_and_receives_status(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"zip")
    manifest = {
        "validation": {"script": {"path": ".devtool-artifact/validate.sh"}},
        "finalize": {"phases": ["before_validation", "after_validation"], "script": {}},
    }
    config = Namespace(repo_root=tmp_path)
    args = Namespace(no_artifact_script=False, _devtool_run_id="run", dry_run=False, ignore_checksum=False, commit="")
    calls: list[tuple[str, str]] = []

    def finalizer(*_args, **kwargs):
        calls.append((kwargs["phase"], kwargs["validation_status"]))
        return OperationResult("artifact-finalize-script", ["f"], 0, 0.0, kwargs["phase"], [], metadata={"hook_event": {"kind": "finalize", "phase": kwargs["phase"]}})

    validator = OperationResult("artifact-validate-script", ["v"], 0, 0.0, "ok", [], metadata={"hook_event": {"kind": "validation", "phase": "validate"}})
    with mock.patch("devtool.cli.run_artifact_finalize_script", side_effect=finalizer), \
         mock.patch("devtool.cli.run_artifact_validation_script", return_value=validator), \
         mock.patch("devtool.cli._artifact_commit_paths", return_value=[]):
        result, _ = _artifact_script_result(config, args, artifact, manifest, target="devtool", solution="", log_path=None, tasker_progress=None)
    assert result is validator
    assert calls == [("before_validation", "pending"), ("after_validation", "passed")]
    assert [event["phase"] for event in result.metadata["hook_events"]] == ["before_validation", "validate", "after_validation"]


def test_after_commit_failure_is_observational_and_preserves_commit_result(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"zip")
    manifest = {"finalize": {"phases": ["after_commit"], "script": {}}}
    config = Namespace(repo_root=tmp_path)
    args = Namespace(
        command="apply-overlay", commit="message", artifact=str(artifact), _devtool_artifact_manifest=manifest,
        _devtool_run_id="run", target="devtool", solution="", validate=True, validate_only=False,
        verbose=False, dry_run=False, ignore_checksum=False,
    )
    commit_result = OperationResult("git-commit", ["git", "commit"], 0, 0.0, "done", [])
    failed = OperationResult(
        "artifact-finalize-script", ["hook"], 9, 0.0, "post failure",
        [Issue("error", "hook", "x", "exit", "failed")],
        failure_kind="finalize_failure", metadata={"hook_event": {"write_restoration_errors": [], "status": "fail"}},
    )
    with mock.patch("devtool.cli._artifact_commit_paths", return_value=["README.md"]), \
         mock.patch("devtool.cli.run_artifact_finalize_script", side_effect=[None, failed]), \
         mock.patch("devtool.cli._git_commit_with_optional_log", return_value=([commit_result], "commit ok")):
        results, raw = _maybe_commit_after_apply(config, args, [OperationResult("apply-overlay", ["apply"], 0, 0.0, "ok", [])], "")
    assert any(item.name == "git-commit" and item.returncode == 0 for item in results)
    post = results[-1]
    assert post.returncode == 0
    assert post.metadata["observational_failure"] is True
    assert "Git commit was preserved" in raw


def test_after_commit_write_violation_is_restored_in_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("committed")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    script = b'printf hacked > "$DEVTOOL_REPO_ROOT/tracked.txt"\nprintf new > "$DEVTOOL_REPO_ROOT/new.txt"\n'
    archive, manifest = archive_with_hooks(tmp_path / "post.zip", finalize=script, finalize_phases=["after_commit"])
    result = run_artifact_finalize_script(repo, archive, phase="after_commit", manifest=manifest)
    assert result and result.returncode == 1
    assert (repo / "tracked.txt").read_text() == "committed"
    assert not (repo / "new.txt").exists()
    assert result.metadata["hook_event"]["writes_restored"] is True


def test_apply_history_collects_hook_events(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"artifact")
    config = Namespace(repo_root=tmp_path)
    args = Namespace(
        command="apply-overlay", validate=True, validate_only=False, preview=False, artifact=str(artifact),
        commit="", _devtool_artifact_type="overlay", _devtool_artifact_preview={"files": 0, "counts": {}},
        _devtool_artifact_manifest={}, _devtool_artifact_sha="", _devtool_artifact_size=None,
        _devtool_run_id="run", _devtool_resolved_validation_plan={},
    )
    result = OperationResult(
        "artifact-finalize-script", ["hook"], 0, 0.0, "ok", [],
        metadata={"hook_event": {"kind": "finalize", "phase": "before_commit", "script_sha256": "a" * 64}},
    )
    captured: dict = {}

    def append(_root, entry, **_kwargs):
        captured.update(entry)
        out = tmp_path / "history.json"
        out.write_text("{}")
        return out

    with mock.patch("devtool.cli.append_history", side_effect=append), \
         mock.patch("devtool.cli._repo_local_artifact_history_enabled", return_value=False):
        _record_apply_history(config, args, [result])
    assert captured["hook_events"][0]["phase"] == "before_commit"
    assert captured["hook_events"][0]["script_sha256"] == "a" * 64


def test_artifact_validation_module_no_longer_owns_subprocess_execution() -> None:
    source = Path("devtool/artifact_validation.py").read_text()
    assert "subprocess.Popen" not in source
    assert "_run_embedded_artifact_script" not in source
    assert "EmbeddedHookEngine" in source
