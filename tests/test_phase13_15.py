from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from devtool.artifacts import artifact_validation_policy, _validate_validation_policy
from devtool.cli import _apply_artifact_validation_defaults, build_parser
from devtool.config import load_project_config
from devtool.gradle_progress import GradleProgressTracker
from devtool.logstore import redact_sensitive_text, write_log
from devtool.models import OperationResult, ProjectConfig
from devtool.validation_evidence import preserve_validation_evidence


def _config(root: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=root, kind="python", project_root=str(root))


def test_cli_project_type_override_is_first_class() -> None:
    args = build_parser().parse_args(["--project-type", "python", "status"])
    assert args.project_type == "python"
    with tempfile.TemporaryDirectory() as tmp:
        config = load_project_config(tmp, None, kind_override="python")
        assert config.kind == "python"


def test_artifact_validation_accepts_profile_and_environment() -> None:
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "validation": {
            "required": True,
            "test_profile": "quick",
            "execution_environment": "native-termux",
        },
    }
    messages: list[str] = []
    ok = _validate_validation_policy(manifest, messages, bundle=False)
    assert ok, messages
    policy = artifact_validation_policy(manifest)
    assert policy["test_profile"] == "quick"
    assert policy["execution_environment"] == "native-termux"


def test_artifact_validation_defaults_do_not_override_cli() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = _config(Path(tmp))
        args = build_parser().parse_args(["--execution-environment", "desktop-linux", "validate", "--test-profile", "full"])
        _apply_artifact_validation_defaults(config, args, {
            "validation": {"test_profile": "quick", "execution_environment": "native-termux"}
        })
        assert args.test_profile == "full"
        assert args.execution_environment == "desktop-linux"


def test_log_redaction_covers_environment_cli_and_bearer_tokens() -> None:
    text = "API_TOKEN=abc123 --password hunter2 Authorization: Bearer secret.jwt.value"
    redacted = redact_sensitive_text(text)
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "secret.jwt.value" not in redacted
    assert redacted.count("<redacted>") == 3


def test_write_log_persists_redacted_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.log"
        write_log("DEVTOOL_ANDROID_HUD_TOKEN=private", path)
        assert path.read_text(encoding="utf-8") == "DEVTOOL_ANDROID_HUD_TOKEN=<redacted>"


def test_gradle_events_reject_other_runs_and_invalid_counters() -> None:
    console = Console(file=None, force_terminal=False)
    with patch.dict("os.environ", {"DEVTOOL_RUN_ID": "run-a"}, clear=False):
        tracker = GradleProgressTracker(["./gradlew", "test"], ".", "test", console)
    tracker.update_from_line('DEVTOOL_EVENT {"sequence":1,"kind":"task-graph","run_id":"run-b","total":2}')
    assert tracker.total is None
    assert tracker.rejected_events == 1
    tracker.update_from_line('DEVTOOL_EVENT {"sequence":2,"kind":"task-start","run_id":"run-a","completed":3,"total":2}')
    assert tracker.total is None
    tracker.update_from_line('DEVTOOL_EVENT {"sequence":3,"kind":"task-graph","total":2}')
    assert tracker.total is None
    tracker.update_from_line('DEVTOOL_EVENT {"sequence":4,"kind":"task-graph","run_id":"run-a","total":2}')
    assert tracker.total == 2
    assert tracker.rejected_events == 3


def test_validation_evidence_is_copied_outside_repo() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch("devtool.validation_evidence.state_dir", return_value=Path(tmp) / "state"):
        repo = Path(tmp) / "repo"
        repo.mkdir()
        report = repo / "build" / "TEST-result.xml"
        report.parent.mkdir()
        report.write_text("<testsuite/>", encoding="utf-8")
        log = repo / "run.log"
        log.write_text("failure", encoding="utf-8")
        result = OperationResult("test", ["pytest"], 1, 0.1, "failed", [], log_path=str(log), metadata={"tests": {"report_files": [str(report)]}})
        destination = preserve_validation_evidence(repo, [result], run_id="run-123")
        assert destination is not None
        assert (destination / "evidence.json").is_file()
        payload = json.loads((destination / "evidence.json").read_text(encoding="utf-8"))
        assert len(payload["copied"]) == 2
