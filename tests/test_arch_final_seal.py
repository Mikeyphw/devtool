from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

from rich.console import Console

from devtool.api_output import run_json_invocation
from devtool.cli import build_parser
from devtool.core import CommandResult
from devtool.core.events import TaskStarted, get_event_bus, publish_event
from devtool.features import resolve_feature_flags
from devtool.models import CommandSpec, Issue, OperationResult, ProjectConfig
from devtool.overlay import check_overlay
from devtool.plugins import PLUGIN_API_VERSION
from devtool.plugins.api import PluginDescriptor
from devtool.plugins.registry import discover_plugins
from devtool.repository_cleanup import discover_backup_candidates
from devtool.runners import run_specs
from devtool.summary import build_runner_json, render_summary
from devtool.validation_profiles import BUILTIN_PROFILES

GOLDEN = Path(__file__).parent / "golden"


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Test"], cwd=root, check=True)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def _render_summary_fixture() -> str:
    result = OperationResult(
        "python-test",
        ["python3", "-m", "pytest"],
        0,
        1.25,
        "2 passed\n",
        [Issue("warning", "python-test", "demo.py", "W001", "fixture warning", 7)],
    )
    stream = io.StringIO()
    console = Console(file=stream, width=80, color_system=None, force_terminal=False)
    render_summary([result], console_obj=console)
    return stream.getvalue()


def _runner_json_fixture() -> str:
    from datetime import datetime, timezone

    result = OperationResult("build", ["make"], 0, 2.5, "ok\n", [])
    payload = build_runner_json(
        [result],
        repo_root="/repo",
        command="build",
        raw_output="$ make\nok\n",
        summary_text="build ok",
        started_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 25, 12, 0, 3, tzinfo=timezone.utc),
    )
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay_fixture(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    current = b"current\n"
    (repo / "file.txt").write_bytes(current)
    artifact = tmp_path / "overlay.zip"
    incoming = b"new\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "golden-overlay",
        "inventory": {"format": 1, "entries": [{
            "path": "file.txt",
            "action": "modify",
            "source_sha256": _sha(b"old\n"),
            "sha256": _sha(incoming),
            "size": len(incoming),
        }]},
        "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative", "requires_clean_worktree": False, "commit": {"enabled": False, "strategy": "single", "message": ""}},
        "validation": {"required": False, "mode": "disabled"},
    }
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(".devtool-artifact.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("file.txt", incoming)
    result = check_overlay(repo, artifact)
    payload = {
        "ok": result.ok,
        "structural_ok": result.structural_ok,
        "source_state_ok": result.source_state_ok,
        "rebaseable": result.rebaseable,
        "conflicts": [item.to_dict() for item in result.conflicts],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _cli_help_body(text: str) -> str:
    # CPython's argparse usage renderer has changed punctuation/grouping across
    # supported Python releases (notably mutually-exclusive option groups).
    # The documented help body is the stable Devtool-owned contract; the usage
    # synopsis remains argparse-owned and is checked for the expected program.
    parts = text.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else text


def test_golden_cli_help_contract(monkeypatch) -> None:
    # argparse wraps help using the ambient terminal width. Golden output must
    # be stable in narrow Termux panes, tmux, CI, and wide desktop terminals,
    # while tolerating Python-version-specific usage grouping punctuation.
    monkeypatch.setenv("COLUMNS", "80")
    actual = build_parser().format_help()
    expected = (GOLDEN / "cli-help.txt").read_text(encoding="utf-8")
    assert actual.startswith("usage: devtool ")
    assert _cli_help_body(actual) == _cli_help_body(expected)


def test_golden_validation_summary_contract() -> None:
    assert _render_summary_fixture() == (GOLDEN / "validation-summary.txt").read_text(encoding="utf-8")


def test_golden_build_report_contract() -> None:
    assert _runner_json_fixture() == (GOLDEN / "build-report.json").read_text(encoding="utf-8")


def test_golden_overlay_diagnostics_contract(tmp_path: Path) -> None:
    assert _overlay_fixture(tmp_path) == (GOLDEN / "overlay-diagnostics.json").read_text(encoding="utf-8")


def test_feature_flags_support_project_and_environment_overrides(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text("[features]\nevent_bus = false\nplugin_system = true\n", encoding="utf-8")
    flags = resolve_feature_flags(tmp_path, environ={"DEVTOOL_FEATURE_EVENT_BUS": "true", "DEVTOOL_FEATURE_STRUCTURED_RESULTS": "0"})
    assert flags.event_bus is True
    assert flags.structured_results is False
    assert flags.plugin_system is True


def test_operation_result_converts_to_canonical_command_result() -> None:
    operation = OperationResult("lint", ["lint"], 2, 0.5, "bad", [Issue("error", "lint", "x.py", "E1", "broken", 1)])
    result = operation.to_command_result()
    assert isinstance(result, CommandResult)
    assert result.success is False
    assert result.exit_code == 2
    assert result.errors == ["broken"]
    assert result.metadata["name"] == "lint"


def test_runner_publishes_task_events_on_real_execution_boundary(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="command")
    captured = []
    listener = captured.append
    bus = get_event_bus()
    bus.subscribe_all(listener)
    try:
        results, _raw = run_specs(config, [CommandSpec("demo", ["echo", "hello"])], verbose=False, dry_run=True)
    finally:
        bus.unsubscribe_all(listener)
    names = [type(item).__name__ for item in captured]
    assert results[0].to_command_result().success is True
    assert "TaskStarted" in names
    assert "TaskFinished" in names


def test_json_api_collects_architecture_events(capsys) -> None:
    def invoke() -> int:
        publish_event(TaskStarted(task="fixture"))
        return 0

    assert run_json_invocation(invoke, command="build", repo="/repo") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["success"] is True
    assert payload["events"][-1]["type"] == "TaskStarted"
    assert payload["events"][-1]["task"] == "fixture"


def test_android_build_is_thin_compatibility_facade() -> None:
    import devtool.android_build as facade
    import devtool.android._standalone as engine

    facade_source = Path(facade.__spec__.origin or "").read_text(encoding="utf-8")
    engine_source = Path(engine.__file__).read_text(encoding="utf-8")
    assert len(facade_source.splitlines()) < 80
    assert len(engine_source.splitlines()) > 5000
    assert facade.main is engine.main
    assert facade.standalone_source_path() == Path(engine.__file__).resolve()


def test_domain_modules_own_android_runtime_boundary() -> None:
    root = Path(__file__).parents[1] / "devtool" / "android"
    for name in ("gradle.py", "sdk.py", "ndk.py", "cmake.py", "aapt2.py", "tasks.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "._standalone import" in source
        assert "android_build import" not in source


def test_plugin_api_version_mismatch_is_isolated(monkeypatch) -> None:
    class Point:
        name = "future"
        value = "example:plugin"
        def load(self):
            return PluginDescriptor("future", "future plugin", api_version=PLUGIN_API_VERSION + 1)

    class Points(list):
        def select(self, *, group):
            assert group == "devtool.plugins"
            return self

    monkeypatch.setattr("devtool.plugins.registry.metadata.entry_points", lambda: Points([Point()]))
    plugins, errors = discover_plugins()
    assert {item.name for item in plugins}.issuperset({"android", "python", "rust", "magisk"})
    assert errors and errors[0]["plugin"] == "future"
    assert "unsupported plugin API version" in errors[0]["error"]




def test_cleanup_recognizes_all_historical_top_level_backup_shapes(tmp_path: Path) -> None:
    for name in (
        ".transitive-connected-skip-backup-20260821-162845",
        ".future-migration-backup-20260825",
        ".no-device-config-cache-fix-20260821-163601",
    ):
        (tmp_path / name).mkdir()
    regular = tmp_path / ".github"
    regular.mkdir()
    found = {path.name for path in discover_backup_candidates(tmp_path)}
    assert ".transitive-connected-skip-backup-20260821-162845" in found
    assert ".future-migration-backup-20260825" in found
    assert ".no-device-config-cache-fix-20260821-163601" in found
    assert ".github" not in found


def test_architecture_final_profile_is_first_class() -> None:
    profile = BUILTIN_PROFILES["architecture-final"]
    assert profile["validation"]["architecture_contracts"] is True
    assert profile["android"]["instrumentation"] == "when-device-present"


def test_cli_has_quiet_and_trace_without_deprecation_bridge() -> None:
    args = build_parser().parse_args(["--quiet", "--trace", "plugin", "list"])
    assert args.quiet is True
    assert args.trace is True
    assert not hasattr(args, "validation_profile")


def test_universal_json_preserves_native_validation_api_envelopes(capsys) -> None:
    native = {
        "api_version": 1,
        "accepted": True,
        "action": "status",
        "data": {"state": "running"},
    }

    def invoke() -> int:
        print(json.dumps(native))
        return 0

    assert run_json_invocation(invoke, command="validate", repo="/repo") == 0
    assert json.loads(capsys.readouterr().out) == native
