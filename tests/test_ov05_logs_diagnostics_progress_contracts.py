from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from rich.cells import cell_len
from rich.console import Console

from devtool import log_commands, logstore, watch
from devtool.diagnostics import classify_failure, parse_issues
from devtool.dotnet_progress import DotnetProgressTracker, _first_dotnet_target, _looks_like_test_project, dotnet_progress_plan
from devtool.generic_progress import GenericProgressTracker
from devtool.gradle_reports import collect_android_lint_reports, collect_gradle_test_reports, parse_junit_reports
from devtool.models import CommandSpec, ProjectConfig
from devtool.npm_progress import NpmProgressTracker, npm_progress_plan
from devtool.proot_distro_runner import ProotRunnerConfig, _guest_env
from devtool.rich_progress import ThreeLineProgressState, render_three_line_progress
from devtool.runners import run_specs
from devtool.ssh_tmux_runner import SSHRunnerConfig, _translate_env_for_repo, build_remote_tmux_script


def test_watch_keeps_nested_src_bin_and_detects_preserved_mtime_content(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "bin"
    nested.mkdir(parents=True)
    source = nested / "tool.py"
    source.write_text("one\n", encoding="utf-8")
    generated = tmp_path / ".devtool" / "generated.txt"
    generated.parent.mkdir()
    generated.write_text("ignore\n", encoding="utf-8")
    first = watch.snapshot_mtimes(tmp_path)
    stat = source.stat()
    source.write_text("two\n", encoding="utf-8")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = watch.snapshot_mtimes(tmp_path)
    assert "src/bin/tool.py" in first
    assert ".devtool/generated.txt" not in first
    assert first["src/bin/tool.py"] != second["src/bin/tool.py"]


def test_watch_interrupted_rerun_is_authoritative(monkeypatch, tmp_path: Path) -> None:
    snapshots = iter([{"a": (1, 1, 1, "a")}, {"a": (1, 1, 2, "b")}])
    monkeypatch.setattr(watch, "snapshot_mtimes", lambda _root: next(snapshots))
    monkeypatch.setattr(watch.time, "sleep", lambda _seconds: None)
    runs = iter([0, 130])
    assert watch.watch_loop(tmp_path, lambda: next(runs), interval=0.2) == 130


def test_log_rotation_and_live_finalize_mirror(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    mirror = tmp_path / "mirror"
    monkeypatch.setattr(logstore, "state_dir", lambda: state)
    monkeypatch.setenv("DEVTOOL_LOG_MAX_FILES", "2")
    monkeypatch.setenv("DEVTOOL_LOG_MAX_FILE_MB", "0.01")
    monkeypatch.setenv("DEVTOOL_LOG_MAX_TOTAL_MB", "1")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setenv("DEVTOOL_PHONE_LOG_MIRROR_DIR", str(mirror))
    paths = []
    for index in range(3):
        path = state / "logs" / f"20260815-12000{index}-000000.log"
        logstore.write_log("header\n" + ("x" * 30000), path)
        paths.append(path)
    assert len(list((state / "logs").glob("*.log"))) <= 2
    newest = paths[-1]
    with newest.open("a", encoding="utf-8") as handle:
        handle.write("\nfinal-output")
    logstore.finalize_log(newest)
    mirrored = mirror / newest.name
    assert mirrored.is_file()
    assert "final-output" in mirrored.read_text(encoding="utf-8")
    assert mirrored.stat().st_size <= 11000


def test_latest_log_searches_all_configured_roots_deterministically(monkeypatch, tmp_path: Path) -> None:
    first_root = tmp_path / "first"; second_root = tmp_path / "second"
    for root in (first_root, second_root): (root / "logs").mkdir(parents=True)
    old = first_root / "logs/20260815-100000-000001.log"; old.write_text("old", encoding="utf-8")
    new = second_root / "logs/20260815-110000-000001.log"; new.write_text("new", encoding="utf-8")
    os.utime(old, (10, 10)); os.utime(new, (20, 20))
    monkeypatch.setattr(log_commands, "_candidate_state_roots", lambda: [first_root, second_root])
    monkeypatch.setattr(log_commands, "_phone_log_mirror_dir", lambda: None)
    assert log_commands.resolve_log_path("latest") == new


def test_two_active_runs_only_resolve_their_own_log_identity(monkeypatch, tmp_path: Path) -> None:
    a = tmp_path / "a.log"; b = tmp_path / "b.log"
    a.write_text("a", encoding="utf-8"); b.write_text("b", encoding="utf-8")
    runs = [
        log_commands.ActiveRun("run-a", os.getpid(), "test", str(tmp_path), "running", "test", "2", a),
        log_commands.ActiveRun("run-b", os.getpid(), "test", str(tmp_path), "running", "test", "1", b),
    ]
    monkeypatch.setattr(log_commands, "list_active_runs", lambda: runs)
    monkeypatch.setattr(log_commands, "newest_logs", lambda _limit: [b, a])
    monkeypatch.setattr(log_commands, "choose_index", lambda _lines, use_fzf=True: 2)
    assert log_commands.choose_active_log(use_fzf=False) == b


def test_active_log_selection_never_guesses_missing_run_log(monkeypatch, tmp_path: Path) -> None:
    recent = tmp_path / "recent.log"
    recent.write_text("other run", encoding="utf-8")
    run = log_commands.ActiveRun("run-1", os.getpid(), "test", str(tmp_path), "running", "test", "", None)
    monkeypatch.setattr(log_commands, "list_active_runs", lambda: [run])
    monkeypatch.setattr(log_commands, "_all_log_paths", lambda: [recent])
    assert log_commands.choose_active_log(use_fzf=False) is None


def test_redaction_is_bounded_on_multimegabyte_near_match_and_quotes() -> None:
    payload = ("A" * (3 * 1024 * 1024)) + "\nPASSWORD = \"secret value\"\n--token 'quoted token'\n"
    started = time.perf_counter()
    redacted = logstore.redact_sensitive_text(payload)
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0
    assert "secret value" not in redacted
    assert "quoted token" not in redacted
    assert redacted.count("<redacted>") == 2


def test_oversized_unclosed_secret_is_conservatively_redacted() -> None:
    payload = 'TOKEN="' + ('s' * (2 * 1024 * 1024))
    redacted = logstore.redact_sensitive_text(payload)
    assert "ssssssss" not in redacted
    assert "oversized-log-record-redacted" in redacted


def _write_junit(path: Path, *, failed: bool, name: str = "case") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    failure = '<failure message="boom" />' if failed else ""
    path.write_text(
        f'<testsuite tests="1" failures="{1 if failed else 0}"><testcase classname="pkg.Test" name="{name}">{failure}</testcase></testsuite>',
        encoding="utf-8",
    )


def test_gradle_report_selection_is_module_task_and_invocation_fresh(tmp_path: Path) -> None:
    stale = tmp_path / "app/build/test-results/testDebugUnitTest/TEST-stale.xml"
    current = tmp_path / "lib/build/test-results/testDebugUnitTest/TEST-current.xml"
    _write_junit(stale, failed=True)
    _write_junit(current, failed=False)
    now = time.time()
    os.utime(stale, (now - 100, now - 100))
    os.utime(current, (now, now))
    result = collect_gradle_test_reports(tmp_path, tasks=[":lib:testDebugUnitTest"], started_at=now - 1)
    assert result.metadata["failed"] == 0
    assert result.metadata["total"] == 1
    assert result.metadata["report_files"] == ["lib/build/test-results/testDebugUnitTest/TEST-current.xml"]


def test_stale_lint_is_ignored_but_current_lint_failure_is_detected(tmp_path: Path) -> None:
    report = tmp_path / "app/build/reports/lint-results-debug.xml"
    report.parent.mkdir(parents=True)
    report.write_text('<issues><issue id="Rule" severity="Error" message="boom"><location file="src/A.kt" line="3"/></issue></issues>', encoding="utf-8")
    now = time.time()
    os.utime(report, (now - 100, now - 100))
    stale = collect_android_lint_reports(tmp_path, tasks=[":app:lintDebug"], started_at=now - 1)
    assert stale.metadata["errors"] == 0 and stale.metadata["report_files"] == []
    os.utime(report, (now, now))
    current = collect_android_lint_reports(tmp_path, tasks=[":app:lintDebug"], started_at=now - 1)
    assert current.metadata["errors"] == 1 and len(current.issues) == 1


def test_same_named_gradle_reports_from_requested_modules_both_survive(tmp_path: Path) -> None:
    now = time.time()
    for module, failed in (("app", False), ("lib", True)):
        path = tmp_path / f"{module}/build/test-results/testDebugUnitTest/TEST-unit.xml"
        _write_junit(path, failed=failed, name=module)
        os.utime(path, (now, now))
    result = collect_gradle_test_reports(
        tmp_path, tasks=[":app:testDebugUnitTest", ":lib:testDebugUnitTest"], started_at=now - 1
    )
    assert result.metadata["total"] == 2
    assert result.metadata["failed"] == 1
    assert len(result.metadata["report_files"]) == 2


def test_lint_dedupe_keeps_distinct_locations_and_decodes_sarif_uri(tmp_path: Path) -> None:
    reports = tmp_path / "app/build/reports"
    reports.mkdir(parents=True)
    sarif = reports / "lint-results-debug.sarif"
    sarif.write_text(json.dumps({"runs": [{"results": [
        {"ruleId": "Rule", "level": "warning", "message": {"text": "same"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/My%20File.kt"}, "region": {"startLine": 1}}}]},
        {"ruleId": "Rule", "level": "warning", "message": {"text": "same"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/My%20File.kt"}, "region": {"startLine": 2}}}]},
    ]}]}), encoding="utf-8")
    now = time.time(); os.utime(sarif, (now, now))
    result = collect_android_lint_reports(tmp_path, tasks=[":app:lintDebug"], started_at=now - 1)
    assert result.metadata["warnings"] == 2
    assert {issue.line for issue in result.issues} == {1, 2}
    assert all("My File.kt" in issue.file for issue in result.issues)


def test_nested_junit_does_not_double_count_and_preserves_test_identity(tmp_path: Path) -> None:
    report = tmp_path / "nested.xml"
    report.write_text(
        '<testsuites tests="1"><testsuite name="parent" tests="1"><testsuite name="child" tests="1"><testcase classname="pkg.Display" name="works"><failure message="boom"/></testcase></testsuite></testsuite></testsuites>',
        encoding="utf-8",
    )
    result = parse_junit_reports(tmp_path, [report])
    assert result.metadata["total"] == 1
    assert result.metadata["failed"] == 1
    assert result.issues[0].file == "-"
    assert result.issues[0].message.startswith("pkg.Display.works:")


def test_ruff_formats_and_unittest_identity_are_typed(tmp_path: Path) -> None:
    output = "src/a.py:2:3: F401 unused import\nE501 line too long\n  --> src/b.py:9:2\nFAIL: label (pkg.tests.TestThing.test_case)\n"
    issues = parse_issues(tmp_path, "test", output)
    assert {(i.code, i.file, i.line) for i in issues} >= {
        ("F401", "src/a.py", 2), ("E501", "src/b.py", 9), ("UNITTEST", "-", None)
    }
    assert any("pkg.tests.TestThing.test_case" in i.message for i in issues if i.code == "UNITTEST")


def test_successful_generic_error_text_does_not_override_exit_zero(tmp_path: Path, capsys) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="python")
    spec = CommandSpec("build", [sys.executable, "-c", "print('error: cache probe miss but recovered')"], cwd=str(tmp_path))
    results, _ = run_specs(config, [spec], verbose=False)
    assert results[0].returncode == 0
    assert any(issue.code == "GENERIC" for issue in results[0].issues)
    assert "error: cache probe miss but recovered" in capsys.readouterr().out


def test_postprocess_failure_and_partial_success_publish_authoritative_final_progress(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="python")
    events: list[dict] = []
    bad = CommandSpec("build", [sys.executable, "-c", "print('x.py:1:1: F821 undefined name')"], cwd=str(tmp_path))
    results, _ = run_specs(config, [bad], verbose=False, progress_callback=events.append)
    assert results[0].returncode == 1
    assert events[-1]["final"] is True and events[-1]["returncode"] == 1 and events[-1]["errors"] >= 1

    events.clear()
    good = CommandSpec("build", [sys.executable, "-c", "print('1/4')"], cwd=str(tmp_path))
    results, _ = run_specs(config, [good], verbose=False, progress_callback=events.append)
    assert results[0].returncode == 0
    assert events[-1]["completed"] == 4 and events[-1]["total"] == 4 and events[-1]["percent"] == 100.0


def test_final_progress_publishes_typed_warning_and_error_counts(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="python")
    events: list[dict] = []
    spec = CommandSpec(
        "build",
        [sys.executable, "-c", "print('src/a.py:2:1: warning: fallback used [warn-code]'); print('src/a.py:1:1: error: boom')"],
        cwd=str(tmp_path),
    )
    results, _ = run_specs(config, [spec], verbose=False, progress_callback=events.append)
    assert results[0].returncode == 1
    assert events[-1]["final"] is True
    assert events[-1]["warnings"] >= 1 and events[-1]["errors"] >= 1


def test_fraction_then_percent_keeps_original_units() -> None:
    tracker = GenericProgressTracker(["tool"], ".", "build", Console(force_terminal=False))
    tracker.update_callback = lambda _snapshot: None
    tracker.update_from_line("2/8 work")
    tracker.update_from_line("50% work")
    assert tracker.total == 8
    assert tracker.completed == 4
    assert tracker.unit_label == "tasks"


def test_three_line_layout_respects_display_cells_for_wide_unicode() -> None:
    state = ThreeLineProgressState(total=10, completed=3, detail="進捗🚀 very long detail", detail_prefix_full="試験")
    for line in render_three_line_progress(state, 18, now=1.0):
        assert cell_len(line) <= 18


def test_npm_lifecycle_repeated_workspace_and_finish_semantics(tmp_path: Path) -> None:
    app = tmp_path / "packages" / "app"
    app.mkdir(parents=True)
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}), encoding="utf-8")
    (app / "package.json").write_text(json.dumps({
        "name": "@demo/app",
        "scripts": {"prebuild": "echo pre", "build": "npm run unit && npm run unit", "postbuild": "echo post", "unit": "echo unit"},
    }), encoding="utf-8")
    plan = npm_progress_plan(["npm", "--workspace", "@demo/app", "run", "build"], str(tmp_path))
    assert plan is not None
    labels = [step.script for step in plan.steps]
    assert labels == ["prebuild", "build", "unit", "unit", "postbuild"]
    tracker = NpmProgressTracker(["npm", "run", "build"], str(app), "test", Console(force_terminal=False))
    tracker.update_callback = lambda _snapshot: None
    tracker.update_from_line("> @demo/app@1.0.0 prebuild")
    assert tracker.completed == 0
    tracker.update_from_line("> @demo/app@1.0.0 build")
    assert tracker.completed == 1


def test_dotnet_filter_is_not_target_and_project_property_is_authoritative(tmp_path: Path) -> None:
    project = tmp_path / "OddName.csproj"
    project.write_text("<Project><PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup></Project>", encoding="utf-8")
    false_named = tmp_path / "LooksLikeTests.csproj"
    false_named.write_text("<Project><PropertyGroup><IsTestProject>false</IsTestProject></PropertyGroup></Project>", encoding="utf-8")
    assert _first_dotnet_target(["--filter", "Category=Fast", str(project)]) == str(project)
    assert _looks_like_test_project(project)
    assert not _looks_like_test_project(false_named)
    plan = dotnet_progress_plan(["dotnet", "test", "--filter", "Category=Fast", str(project)], str(tmp_path))
    assert plan is not None and plan.target == str(project)


def test_same_lint_finding_in_two_requested_modules_is_not_cross_module_deduped(tmp_path: Path) -> None:
    now = time.time()
    xml = """<issues><issue id="SharedRule" severity="Warning" message="same"><location file="src/Common.kt" line="7"/></issue></issues>"""
    for module in ("app", "lib"):
        report = tmp_path / module / "build/reports/lint-results-debug.xml"
        report.parent.mkdir(parents=True)
        report.write_text(xml, encoding="utf-8")
        os.utime(report, (now, now))
    result = collect_android_lint_reports(
        tmp_path, tasks=[":app:lintDebug", ":lib:lintDebug"], started_at=now - 1
    )
    assert result.metadata["warnings"] == 2
    assert len(result.issues) == 2


def test_dotnet_identical_result_summaries_from_two_assemblies_both_count(tmp_path: Path) -> None:
    tracker = DotnetProgressTracker(["dotnet", "test"], str(tmp_path), "test", Console(force_terminal=False))
    tracker.update_callback = lambda _snapshot: None
    tracker.update_from_line("Test run for /tmp/A.dll (.NETCoreApp,Version=v8.0)")
    tracker.update_from_line("Passed! - Failed: 0, Passed: 1, Skipped: 0, Total: 1, Duration: 5 ms")
    tracker.update_from_line("Test run for /tmp/B.dll (.NETCoreApp,Version=v8.0)")
    tracker.update_from_line("Passed! - Failed: 0, Passed: 1, Skipped: 0, Total: 1, Duration: 5 ms")
    snapshot = tracker.snapshot()
    assert snapshot["tests_total"] == 2
    assert snapshot["tests_passed"] == 2
    assert snapshot["test_assemblies"] == 2


def test_generic_failed_word_does_not_masquerade_as_test_failure() -> None:
    assert classify_failure("package", "hardlink optimization failed; copied instead", 1) != "test_failure"
    assert classify_failure("run", "optional cache probe failed", 1) == "runtime_failure"


def test_outer_run_id_propagates_to_ssh_and_proot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEVTOOL_RUN_ID", "run-auth-123")
    ssh_env = _translate_env_for_repo({}, tmp_path, "/srv/repo")
    assert ssh_env["DEVTOOL_RUN_ID"] == "run-auth-123"
    script = build_remote_tmux_script(SSHRunnerConfig(host="host"), ["test"])
    assert "DEVTOOL_RUN_ID=run-auth-123" in script
    proot = ProotRunnerConfig(distro="debian")
    guest = _guest_env(proot, CommandSpec("test", ["python3"]), mappings=[], cwd=str(tmp_path), stage=tmp_path)
    assert "DEVTOOL_RUN_ID=run-auth-123" in guest


def test_dense_subrecord_near_matches_are_linear_and_desktop_uses_same_bound() -> None:
    from devtool.desktop.gtk_runtime import redact_text

    payload = ("=" * 300_000) + " PASSWORD='secret value'"
    started = time.perf_counter()
    redacted = logstore.redact_sensitive_text(payload)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5
    assert "secret value" not in redacted
    assert "secret value" not in redact_text(payload)


def test_gradle_report_freshness_is_strictly_after_task_start(tmp_path: Path) -> None:
    path = tmp_path / "app/build/test-results/testDebugUnitTest/TEST-unit.xml"
    _write_junit(path, failed=True)
    now = time.time()
    os.utime(path, (now - 0.5, now - 0.5))
    result = collect_gradle_test_reports(tmp_path, tasks=[":app:testDebugUnitTest"], started_at=now)
    assert result.metadata["total"] == 0
    assert result.metadata["report_files"] == []


def test_lint_module_variant_pairs_do_not_cross_match(tmp_path: Path) -> None:
    now = time.time()
    app_wrong = tmp_path / "app/build/reports/lint-results-release.xml"
    app_wrong.parent.mkdir(parents=True)
    app_wrong.write_text('<issues><issue id="Wrong" severity="Error" message="stale"><location file="src/A.kt" line="1"/></issue></issues>', encoding="utf-8")
    lib_right = tmp_path / "lib/build/reports/lint-results-release.xml"
    lib_right.parent.mkdir(parents=True)
    lib_right.write_text('<issues><issue id="Right" severity="Warning" message="current"><location file="src/B.kt" line="2"/></issue></issues>', encoding="utf-8")
    for path in (app_wrong, lib_right):
        os.utime(path, (now, now))
    result = collect_android_lint_reports(
        tmp_path,
        tasks=[":app:lintDebug", ":lib:lintRelease"],
        started_at=now - 1,
    )
    assert [issue.code for issue in result.issues] == ["Right"]
    assert result.metadata["report_files"] == ["lib/build/reports/lint-results-release.xml"]


def test_gradle_test_diagnostics_keep_module_identity(tmp_path: Path) -> None:
    now = time.time()
    for module in ("app", "lib"):
        path = tmp_path / f"{module}/build/test-results/testDebugUnitTest/TEST-unit.xml"
        _write_junit(path, failed=True, name="same")
        os.utime(path, (now, now))
    result = collect_gradle_test_reports(
        tmp_path,
        tasks=[":app:testDebugUnitTest", ":lib:testDebugUnitTest"],
        started_at=now - 1,
    )
    assert {issue.command for issue in result.issues} == {"test[app]", "test[lib]"}
    assert set(result.metadata["report_modules"].values()) == {"app", "lib"}


def test_partial_success_declares_process_exit_progress_finalization(tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="python")
    events: list[dict] = []
    spec = CommandSpec("build", [sys.executable, "-c", "print('1/4')"], cwd=str(tmp_path))
    results, _ = run_specs(config, [spec], verbose=False, progress_callback=events.append)
    assert results[0].returncode == 0
    final = events[-1]
    assert final["progress_finalization"] == "process-exit"
    assert final["measured_completed"] == 1
    assert final["completed"] == 4 and final["percent"] == 100.0


def test_dotnet_test_property_with_xml_attributes_is_authoritative(tmp_path: Path) -> None:
    project = tmp_path / "OddName.csproj"
    project.write_text(
        '<Project><PropertyGroup><IsTestProject Condition="\'$(Configuration)\' == \'Debug\'">true</IsTestProject></PropertyGroup></Project>',
        encoding="utf-8",
    )
    assert _looks_like_test_project(project)
