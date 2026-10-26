from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from devtool.gradle_reports import (
    collect_dotnet_trx_report,
    collect_maven_test_reports,
    collect_node_json_report,
    collect_node_junit_report,
    parse_junit_reports,
)
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import DotnetRunner, GoRunner, MavenRunner, NodeRunner, RustRunner
from devtool.runners import run_specs
from devtool.test_adapters import (
    EvidenceTestRunner,
    compatibility_counts,
    reconcile_results,
    result_from_counts,
    result_from_go_report,
    result_from_stdout,
    register_polyglot_test_runners,
)
from devtool.test_counts import TestCounts as Counts
from devtool.test_execution import TestExecutionContext as ExecutionContext, TestExecutor as Executor, TestRunnerRegistry as RunnerRegistry
from devtool.test_model import TestSpec as Spec, TestStatus as Status
from devtool.go_test import GoTestJSONStream


def test_junit_report_produces_canonical_cases_and_preserves_error_status(tmp_path: Path) -> None:
    report = tmp_path / "TEST-demo.xml"
    report.write_text(
        '<testsuite name="demo">'
        '<testcase classname="C" name="ok" time="0.1"/>'
        '<testcase classname="C" name="bad"><error message="boom">trace</error></testcase>'
        '<testcase classname="C" name="skip"><skipped/></testcase>'
        '</testsuite>',
        encoding="utf-8",
    )
    collection = parse_junit_reports(tmp_path, [report], framework="gradle-junit")
    assert collection.test_result is not None
    assert compatibility_counts(collection.test_result) == {"passed": 1, "failed": 1, "skipped": 1, "total": 3}
    statuses = [case.status for suite in collection.test_result.suites for case in suite.cases]
    assert statuses == [Status.PASSED, Status.ERROR, Status.SKIPPED]


def test_maven_surefire_structured_collection_uses_reports(tmp_path: Path) -> None:
    report = tmp_path / "module" / "target" / "surefire-reports" / "TEST-demo.xml"
    report.parent.mkdir(parents=True)
    report.write_text('<testsuite name="demo"><testcase classname="C" name="ok"/></testsuite>', encoding="utf-8")
    collection = collect_maven_test_reports(tmp_path, started_at=time.time() - 2)
    assert collection.test_result is not None
    assert collection.metadata["framework"] == "maven-surefire"
    assert collection.test_result.tests_passed == 1


def test_dotnet_trx_structured_collection(tmp_path: Path) -> None:
    report_dir = tmp_path / ".devtool" / "reports" / "dotnet"
    report_dir.mkdir(parents=True)
    report = report_dir / "devtool.trx"
    report.write_text(
        '<TestRun><Results>'
        '<UnitTestResult testId="1" testName="Ok" outcome="Passed" duration="00:00:00.250"/>'
        '<UnitTestResult testId="2" testName="Bad" outcome="Failed"><Output><ErrorInfo><Message>boom</Message></ErrorInfo></Output></UnitTestResult>'
        '<UnitTestResult testId="3" testName="Skip" outcome="NotExecuted"/>'
        '</Results></TestRun>',
        encoding="utf-8",
    )
    collection = collect_dotnet_trx_report(tmp_path, report_path=str(report_dir), started_at=time.time() - 2)
    assert collection.test_result is not None
    assert compatibility_counts(collection.test_result) == {"passed": 1, "failed": 1, "skipped": 1, "total": 3}
    first = collection.test_result.suites[0].cases[0]
    assert first.duration_seconds == 0.25


def test_node_json_understands_jest_vitest_shape(tmp_path: Path) -> None:
    report = tmp_path / "node.json"
    report.write_text(json.dumps({
        "testResults": [{
            "name": "demo.test.js",
            "assertionResults": [
                {"fullName": "demo passes", "status": "passed", "duration": 4},
                {"fullName": "demo fails", "status": "failed", "failureMessages": ["boom"]},
                {"fullName": "demo todo", "status": "todo"},
            ],
        }]
    }), encoding="utf-8")
    collection = collect_node_json_report(tmp_path, report_path=str(report), started_at=time.time() - 2)
    assert collection.test_result is not None
    assert compatibility_counts(collection.test_result) == {"passed": 1, "failed": 1, "skipped": 1, "total": 3}


def test_node_junit_has_node_framework_not_pytest(tmp_path: Path) -> None:
    report = tmp_path / "node.xml"
    report.write_text('<testsuite name="node"><testcase classname="x" name="a"/></testsuite>', encoding="utf-8")
    collection = collect_node_junit_report(tmp_path, report_path=str(report))
    assert collection.test_result is not None
    assert collection.metadata["framework"] == "node-junit"
    assert collection.test_result.metadata["framework"] == "node-junit"


def test_go_protocol_keeps_stable_package_test_identity() -> None:
    stream = GoTestJSONStream()
    for event in (
        {"Action": "run", "Package": "example/p", "Test": "TestA"},
        {"Action": "pass", "Package": "example/p", "Test": "TestA", "Elapsed": 0.12},
        {"Action": "run", "Package": "example/p", "Test": "TestB"},
        {"Action": "skip", "Package": "example/p", "Test": "TestB", "Elapsed": 0.01},
    ):
        stream.feed(json.dumps(event))
    canonical = result_from_go_report("test", stream.report())
    ids = [case.id for suite in canonical.suites for case in suite.cases]
    assert ids == ["example/p#TestA", "example/p#TestB"]
    assert canonical.tests_passed == 1 and canonical.tests_skipped == 1


def test_stdout_compatibility_parsers_feed_canonical_model() -> None:
    cargo = result_from_stdout("test", ["cargo", "test"], "test result: ok. 7 passed; 1 failed; 2 ignored; 0 measured; 0 filtered out")
    maven = result_from_stdout("test", ["mvn", "test"], "Results:\nTests run: 5, Failures: 1, Errors: 1, Skipped: 1")
    node = result_from_stdout("test", ["npm", "test"], "Tests: 2 failed, 1 skipped, 9 passed, 12 total")
    assert cargo and compatibility_counts(cargo) == {"passed": 7, "failed": 1, "skipped": 2, "total": 10}
    assert maven and compatibility_counts(maven) == {"passed": 2, "failed": 2, "skipped": 1, "total": 5}
    assert node and compatibility_counts(node) == {"passed": 9, "failed": 2, "skipped": 1, "total": 12}


def test_structured_evidence_drops_aggregate_stdout_duplicate() -> None:
    stdout = result_from_counts("test", framework="maven", counts=Counts(99, 0, 0, 99), evidence="stdout")
    report = result_from_counts("test", framework="maven-surefire", counts=Counts(1, 0, 0, 1), evidence="report")
    merged = reconcile_results("test", [stdout, report])
    assert merged is not None
    assert merged.tests_total == 1
    assert merged.metadata["evidence"] == "report"


def test_runner_adapters_are_real_test_runners_not_marker_classes() -> None:
    outcome = SimpleNamespace(stdout="test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out", exit_code=0)
    runner = EvidenceTestRunner(lambda *a, **k: outcome, framework="cargo")
    result = runner.run(Spec("unit", "Unit", "cargo", ("cargo", "test")), ExecutionContext("."))
    assert compatibility_counts(result) == {"passed": 2, "failed": 0, "skipped": 1, "total": 3}


def test_project_runner_structured_metadata_without_breaking_dotnet_argv(tmp_path: Path) -> None:
    dotnet = ProjectConfig(repo_root=tmp_path, kind="dotnet", test_target="Demo.sln")
    spec = DotnetRunner().build_specs(dotnet, "test")[0]
    assert spec.argv == ["dotnet", "test", "Demo.sln"]
    assert spec.metadata["test_framework"] == "dotnet"

    configured = ProjectConfig(repo_root=tmp_path, kind="dotnet", test_target="Demo.sln", runner_options={"dotnet": {"test_report": ".devtool/reports/dotnet"}})
    configured_spec = DotnetRunner().build_specs(configured, "test")[0]
    assert configured_spec.argv == ["dotnet", "test", "Demo.sln"]
    assert configured_spec.metadata["report_parser"] == "dotnet-trx"


def test_project_runner_metadata_declares_maven_node_go_and_cargo_tests(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")
    maven = MavenRunner().build_specs(ProjectConfig(repo_root=tmp_path, kind="maven"), "test")[0]
    assert maven.metadata["report_parser"] == "maven-surefire"

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    node_cfg = ProjectConfig(repo_root=tmp_path, kind="node", runner_options={"node": {"test_report": "reports/vitest.json", "test_report_format": "json"}})
    node = NodeRunner().build_specs(node_cfg, "test")[0]
    assert node.metadata["test_framework"] == "node" and node.metadata["report_parser"] == "node-json"

    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion="0.1.0"\n')
    rust = RustRunner().build_specs(ProjectConfig(repo_root=tmp_path, kind="rust"), "test")[0]
    assert rust.metadata["test_framework"] == "cargo"

    (tmp_path / "go.mod").write_text("module example/x\n\ngo 1.23\n")
    go = GoRunner().build_specs(ProjectConfig(repo_root=tmp_path, kind="go"), "test")[0]
    assert go.metadata["test_framework"] == "go" and go.metadata["go_test_json"] is True


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_run_specs_prefers_maven_report_over_conflicting_stdout(tmp_path: Path) -> None:
    script = _script(tmp_path, "maven.sh", "mkdir -p target/surefire-reports\nprintf '%s\\n' '<testsuite name=\"s\"><testcase classname=\"C\" name=\"one\"/></testsuite>' > target/surefire-reports/TEST-s.xml\nprintf '%s\\n' 'Results:' 'Tests run: 99, Failures: 0, Errors: 0, Skipped: 0'\n")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"maven","test_framework":"maven","report_parser":"maven-surefire","report_parsers":["maven-surefire"],"project_root":str(tmp_path)})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "report"
    assert result.metadata["tests"]["total"] == 1
    assert result.metadata["test_result"]["metadata"]["evidence"] == "report"


def test_run_specs_node_fallback_still_produces_canonical_result(tmp_path: Path) -> None:
    script = _script(tmp_path, "node.sh", "printf '%s\\n' 'Tests: 1 failed, 2 skipped, 7 passed, 10 total'\n")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="node")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"node","test_framework":"node","progress_parser":"generic"})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "stdout"
    assert result.metadata["tests"] == {"passed": 7, "failed": 1, "skipped": 2, "total": 10}


def test_builtin_polyglot_registry_executes_through_test_executor() -> None:
    outcome = SimpleNamespace(stdout="Tests: 1 failed, 1 skipped, 3 passed, 5 total", exit_code=1)
    registry = register_polyglot_test_runners(RunnerRegistry(), lambda *a, **k: outcome)
    assert registry.names() == ("cargo", "dotnet", "go", "gradle", "maven", "node", "pytest")
    result = Executor(registry).execute(Spec("node-unit", "Node unit", "node", ("npm", "test")), ExecutionContext("."))
    assert compatibility_counts(result) == {"passed": 3, "failed": 1, "skipped": 1, "total": 5}


def test_test_executor_adapter_prefers_structured_node_json(tmp_path: Path) -> None:
    report = tmp_path / "node.json"
    report.write_text(json.dumps({"suites":[{"id":"s","cases":[{"id":"s#a","name":"a","status":"passed"}]}]}), encoding="utf-8")
    outcome = SimpleNamespace(stdout="Tests: 99 passed, 99 total", exit_code=0)
    registry = register_polyglot_test_runners(RunnerRegistry(), lambda *a, **k: outcome)
    spec = Spec("node-unit", "Node unit", "node", ("npm", "test"), cwd=str(tmp_path), metadata={"test_framework":"node","report_parser":"node-json","test_report":str(report)})
    result = Executor(registry).execute(spec, ExecutionContext(str(tmp_path)))
    assert compatibility_counts(result) == {"passed": 1, "failed": 0, "skipped": 0, "total": 1}
    assert result.metadata["evidence"] == "report"

def test_pytest_stdout_fallback_counts_without_xml() -> None:
    result = result_from_stdout("unit", ["python", "-m", "pytest"], "===== 2 failed, 7 passed, 1 skipped, 1 xfailed in 0.10s =====", framework="pytest")
    assert result is not None
    assert compatibility_counts(result) == {"passed": 7, "failed": 2, "skipped": 2, "total": 11}


def test_dotnet_stdout_fallback_accumulates_assemblies() -> None:
    output = "\n".join([
        "Passed! - Failed: 0, Passed: 4, Skipped: 1, Total: 5, Duration: 1 s",
        "Failed! - Failed: 2, Passed: 3, Skipped: 0, Total: 5, Duration: 1 s",
    ])
    result = result_from_stdout("unit", ["dotnet", "test"], output, framework="dotnet")
    assert result is not None
    assert compatibility_counts(result) == {"passed": 7, "failed": 2, "skipped": 1, "total": 10}


def test_plain_go_fallback_is_stdout_evidence_not_protocol(tmp_path: Path) -> None:
    script = _script(tmp_path, "go.sh", "printf '%s\\n' '--- FAIL: TestPlain (0.01s)' '    plain_test.go:9: boom' 'FAIL'\nprintf 'FAIL\\texample/p\\t0.01s\\n'\nexit 1\n")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="go")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"go","progress_parser":"generic"})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "stdout"
    cases = result.metadata["test_result"]["suites"][0]["cases"]
    assert cases[0]["id"] == "example/p#TestPlain"


def test_node_json_failure_is_authoritative_even_when_process_exits_zero(tmp_path: Path) -> None:
    report = tmp_path / "node.json"
    script = _script(tmp_path, "node-report.sh", f"cat > {report} <<'EOF'\n{{\"testResults\":[{{\"name\":\"demo.test.js\",\"assertionResults\":[{{\"fullName\":\"demo fails\",\"status\":\"failed\",\"duration\":5,\"failureMessages\":[\"boom\"]}}]}}]}}\nEOF\nexit 0\n")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="node")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"node","test_framework":"node","report_parser":"node-json","test_report":str(report),"project_root":str(tmp_path)})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.returncode == 1
    assert result.failure_kind == "test_failure"
    assert result.metadata["tests"]["failed"] == 1
    case = result.metadata["test_result"]["suites"][0]["cases"][0]
    assert case["duration_seconds"] == 0.005


def test_fc08_maven_report_with_old_mtime_still_beats_stdout(tmp_path: Path) -> None:
    """Android clock/granularity drift must not suppress new report evidence."""
    script = _script(tmp_path, "old-clock.sh", '''mkdir -p target/surefire-reports
printf '%s\\n' '<testsuite name="s"><testcase classname="C" name="current"/></testsuite>' > target/surefire-reports/TEST-clock.xml
# Source-date/clock skew is intentional. This was previously treated as stale.
touch -t 202001010000 target/surefire-reports/TEST-clock.xml
printf '%s\\n' 'Results:' 'Tests run: 99, Failures: 0, Errors: 0, Skipped: 0'
''')
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"maven", "report_parser":"maven-surefire", "report_parsers":["maven-surefire"], "project_root":str(tmp_path)})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "report"
    assert result.metadata["tests"]["total"] == 1


def test_fc08_unchanged_preexisting_maven_report_is_not_current(tmp_path: Path) -> None:
    target = tmp_path / "target" / "surefire-reports"
    target.mkdir(parents=True)
    (target / "TEST-stale.xml").write_text('<testsuite name="s"><testcase name="stale"/></testsuite>')
    script = _script(tmp_path, "no-new-report.sh", "printf '%s\\n' 'Results:' 'Tests run: 7, Failures: 0, Errors: 0, Skipped: 0'\n")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"maven", "report_parser":"maven-surefire", "report_parsers":["maven-surefire"], "project_root":str(tmp_path)})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "stdout"
    assert result.metadata["tests"]["total"] == 7


def test_fc08_rewritten_maven_report_must_not_use_previous_contents(tmp_path: Path) -> None:
    target = tmp_path / "target" / "surefire-reports"
    target.mkdir(parents=True)
    (target / "TEST-replaced.xml").write_text('<testsuite name="s"><testcase name="old"/></testsuite>')
    script = _script(tmp_path, "rewrite.sh", '''printf '%s\\n' '<testsuite name="s"><testcase name="new"/></testsuite>' > target/surefire-reports/TEST-replaced.xml
printf '%s\\n' 'Results:' 'Tests run: 99, Failures: 0, Errors: 0, Skipped: 0'
''')
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    spec = CommandSpec("test", [str(script)], cwd=str(tmp_path), metadata={"runner":"maven", "report_parser":"maven-surefire", "report_parsers":["maven-surefire"], "project_root":str(tmp_path)})
    result = run_specs(config, [spec], verbose=False)[0][0]
    assert result.metadata["test_evidence"] == "report"
    assert result.metadata["tests"]["total"] == 1
