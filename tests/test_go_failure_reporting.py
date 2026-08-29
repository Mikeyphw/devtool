from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from devtool.cli import _append_run_log, _maybe_copy, _run_apply
from devtool.diagnostics import classify_failure, parse_issues
from devtool.go_test import GoTestJSONStream, parse_go_test_report
from devtool.models import Issue, OperationResult, ProjectConfig
from devtool.summary import render_summary, summarize_failure_output


PLAIN_FAILURE = """==> go-test package chrootctl/internal/contracts
=== RUN   TestFrozenPythonContractsValidate
    contracts_test.go:24: Validate: native runtime schema drift: expected 1, got 2
--- FAIL: TestFrozenPythonContractsValidate (0.01s)
FAIL
FAIL\tchrootctl/internal/contracts\t0.045s
<== go-test package chrootctl/internal/contracts fail
"""


def test_go_json_stream_renders_human_output_and_package_boundaries() -> None:
    stream = GoTestJSONStream()
    events = [
        {"Action": "start", "Package": "example.com/demo/pkg"},
        {"Action": "run", "Package": "example.com/demo/pkg", "Test": "TestDemo"},
        {"Action": "output", "Package": "example.com/demo/pkg", "Test": "TestDemo", "Output": "=== RUN   TestDemo\n"},
        {"Action": "output", "Package": "example.com/demo/pkg", "Test": "TestDemo", "Output": "    demo_test.go:7: wanted 1, got 2\n"},
        {"Action": "output", "Package": "example.com/demo/pkg", "Test": "TestDemo", "Output": "--- FAIL: TestDemo (0.00s)\n"},
        {"Action": "fail", "Package": "example.com/demo/pkg", "Test": "TestDemo"},
        {"Action": "output", "Package": "example.com/demo/pkg", "Output": "FAIL\n"},
        {"Action": "fail", "Package": "example.com/demo/pkg"},
    ]
    rendered = "".join(stream.feed(json.dumps(event) + "\n") for event in events)
    assert rendered.startswith("==> go-test package example.com/demo/pkg\n")
    assert "demo_test.go:7: wanted 1, got 2" in rendered
    assert rendered.endswith("<== go-test package example.com/demo/pkg fail\n")
    assert '"Action"' not in rendered


def test_go_report_extracts_exact_failure_package_location_and_rerun() -> None:
    report = parse_go_test_report(PLAIN_FAILURE)
    assert report.tests_failed == 1
    assert report.packages_failed == 1
    failure = report.failures[0]
    assert failure.package == "chrootctl/internal/contracts"
    assert failure.test == "TestFrozenPythonContractsValidate"
    assert failure.file == "contracts_test.go"
    assert failure.line == 24
    assert failure.message == "Validate: native runtime schema drift: expected 1, got 2"
    assert failure.rerun == "go test -run '^TestFrozenPythonContractsValidate$' chrootctl/internal/contracts"


def test_go_failure_becomes_typed_diagnostic_and_test_failure(tmp_path: Path) -> None:
    issues = parse_issues(tmp_path, "test", PLAIN_FAILURE)
    go_issues = [issue for issue in issues if issue.code == "GO_TEST"]
    assert len(go_issues) == 1
    issue = go_issues[0]
    assert issue.line == 24
    assert issue.file == "contracts_test.go"
    assert issue.message == "TestFrozenPythonContractsValidate: Validate: native runtime schema drift: expected 1, got 2"
    assert classify_failure("go-validation", PLAIN_FAILURE, 1) == "test_failure"


def test_failure_excerpt_keeps_go_root_cause_instead_of_tail_only() -> None:
    noisy = "\n".join([*(f"ok\tpackage/{index}\t(cached)" for index in range(25)), *PLAIN_FAILURE.splitlines(), "FAIL"])
    excerpt = summarize_failure_output(noisy, max_lines=12)
    assert "TestFrozenPythonContractsValidate" in excerpt
    assert "native runtime schema drift: expected 1, got 2" in excerpt
    assert "earlier line(s) omitted" not in excerpt or "failure excerpt" in excerpt


def test_summary_counts_rollback_as_recovery_and_shows_go_root_cause() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, width=100)
    go_meta = {
        "runner": "go",
        "go_test": {
            "packages": {"passed": 26, "failed": 1, "skipped": 0, "no_tests": 3, "total": 30},
            "rerun": "go test -run '^TestFrozenPythonContractsValidate$' chrootctl/internal/contracts",
            "failures": [],
        },
        "tests": {"passed": 20, "failed": 1, "skipped": 0, "total": 21},
    }
    results = [
        OperationResult("apply-overlay", ["apply"], 0, 0.5, "applied", []),
        OperationResult(
            "test",
            ["go", "test", "-json", "./..."],
            1,
            2.0,
            PLAIN_FAILURE,
            [Issue("error", "test", "contracts_test.go", "GO_TEST", "TestFrozenPythonContractsValidate: Validate: native runtime schema drift: expected 1, got 2", line=24)],
            failure_kind="test_failure",
            metadata=go_meta,
        ),
        OperationResult("apply-rollback", ["rollback"], 0, 0.2, "rolled back", []),
    ]
    render_summary(results, console_obj=console)
    text = stream.getvalue()
    assert "Stages" in text and "1 passed, 1 failed" in text
    assert "2 passed, 1 failed" not in text
    assert "Recovery" in text and "Rollback succeeded" in text
    assert "Failures" in text
    assert "native runtime schema drift" in text
    assert "26 passed, 1 failed, 3 no tests" in text
    assert "go test -run" in text


def test_clipboard_uses_go_failure_reason_and_rerun_without_unknown_failure() -> None:
    raw_output = "$ go test -json -p 2 ./...\n" + PLAIN_FAILURE + "\n$ transaction rollback tx.json\nRolled back devtool apply transaction.\n"
    summary_text = "test: failed exit=1 time=19.77s\napply-rollback: ok exit=0 time=1.51s\n"
    json_text = json.dumps({
        "warnings": 0,
        "errors": 1,
        "failed": 1,
        "skipped": 0,
        "results": [
            {
                "name": "test",
                "returncode": 1,
                "failure_kind": "test_failure",
                "output": PLAIN_FAILURE,
                "issues": [
                    {
                        "severity": "error",
                        "command": "test",
                        "file": "contracts_test.go",
                        "line": 24,
                        "code": "GO_TEST",
                        "message": "TestFrozenPythonContractsValidate: Validate: native runtime schema drift: expected 1, got 2",
                    }
                ],
                "metadata": {
                    "go_test": {
                        "packages": {"passed": 26, "failed": 1, "skipped": 0, "no_tests": 3, "total": 30},
                        "rerun": "go test -run '^TestFrozenPythonContractsValidate$' chrootctl/internal/contracts"
                    }
                },
            },
            {"name": "apply-rollback", "returncode": 0, "failure_kind": "", "issues": [], "metadata": {}},
        ],
    })
    copied: list[str] = []
    clipboard_result = SimpleNamespace(success=True, backend="test", reason="")
    args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto")
    with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or clipboard_result), patch("devtool.cli.console.print"):
        _maybe_copy(raw_output, summary_text, json_text, args)
    assert len(copied) == 1
    text = copied[0]
    assert "Go packages: 26 passed, 1 failed, 0 skipped, 3 no tests (30 total)" in text
    assert "Failure reason:" in text
    assert "TestFrozenPythonContractsValidate" in text
    assert "native runtime schema drift: expected 1, got 2" in text
    assert "chrootctl/internal/contracts" in text
    assert "contracts_test.go:24" in text
    assert "unknown failure" not in text
    assert "Rerun:" in text
    assert "go test -run '^TestFrozenPythonContractsValidate$' chrootctl/internal/contracts" in text
    assert "Recovery: rollback succeeded" in text


def test_runner_converts_go_json_to_human_log_and_structured_metadata(tmp_path: Path) -> None:
    import subprocess
    from devtool.models import ProjectConfig
    from devtool.project_runners import GoRunner
    from devtool.runners import run_specs

    subprocess.run(["go", "mod", "init", "example.com/reportdemo"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    (tmp_path / "contracts_test.go").write_text(
        """package reportdemo\nimport \"testing\"\nfunc TestContract(t *testing.T) { t.Fatalf(\"schema drift: expected 1, got 2\") }\n""",
        encoding="utf-8",
    )
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="go")
    spec = GoRunner().build_specs(config, "test")[0]
    log_path = tmp_path / "go-test.log"
    results, raw = run_specs(config, [spec], verbose=False, log_path=log_path)
    assert len(results) == 1
    result = results[0]
    assert result.returncode == 1
    assert result.failure_kind == "test_failure"
    assert any(issue.code == "GO_TEST" for issue in result.issues)
    assert result.metadata["tests"]["failed"] == 1
    assert result.metadata["go_test"]["packages"]["failed"] == 1
    assert "TestContract" in result.metadata["go_test"]["rerun"]
    assert '"Action"' not in result.output
    assert "--- FAIL: TestContract" in result.output
    log_text = log_path.read_text(encoding="utf-8")
    assert '"Action"' not in log_text
    assert "==> go-test package example.com/reportdemo" in log_text
    assert "schema drift: expected 1, got 2" in log_text
    assert "<== go-test package example.com/reportdemo fail" in log_text
    assert "Devtool Go test summary:" in log_text
    assert "Devtool Go failure: example.com/reportdemo · TestContract · contracts_test.go:3" in log_text
    assert "Devtool Go rerun: go test -run '^TestContract$' example.com/reportdemo" in log_text
    assert "--- FAIL: TestContract" in raw


def test_plain_go_failure_with_location_after_fail_marker_matches_real_log_shape() -> None:
    output = """ok  \tchrootctl/internal/config\t(cached)
--- FAIL: TestFrozenPythonContractsValidate (0.01s)
    contracts_test.go:24: Validate: native runtime schema drift: expected 1, got 2
FAIL
FAIL\tchrootctl/internal/contracts\t0.045s
"""
    report = parse_go_test_report(output)
    assert report.packages_failed == 1
    failure = report.failures[0]
    assert failure.package == "chrootctl/internal/contracts"
    assert failure.test == "TestFrozenPythonContractsValidate"
    assert failure.file == "contracts_test.go"
    assert failure.line == 24
    assert failure.message == "Validate: native runtime schema drift: expected 1, got 2"


def test_append_run_log_persists_orchestration_evidence(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    _append_run_log(log_path, "Artifact validation passed.\nValidation evidence preserved: /tmp/evidence")
    text = log_path.read_text(encoding="utf-8")
    assert "Artifact validation passed." in text
    assert "Validation evidence preserved: /tmp/evidence" in text


def test_run_apply_appends_artifact_validation_to_durable_log(tmp_path: Path) -> None:
    artifact = tmp_path / "overlay.zip"
    artifact.write_bytes(b"overlay")
    log_path = tmp_path / "run.log"
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="go")
    args = SimpleNamespace(
        rebase=False,
        validate_only=False,
        ignore_checksum=False,
        no_manifest_check=False,
        preview=False,
        diff=False,
        interactive=False,
        yes=True,
        force=True,
        dry_run=False,
    )
    validation = SimpleNamespace(
        ok=True,
        messages=["Artifact validation passed."],
        manifest={},
    )
    applied = OperationResult(
        "apply-overlay",
        ["apply-overlay", str(artifact)],
        0,
        0.01,
        "Applied overlay.",
        [],
        metadata={},
    )
    with (
        patch("devtool.cli.resolve_artifact_path", return_value=artifact),
        patch("devtool.cli.is_overlay_bundle", return_value=False),
        patch("devtool.cli.validate_artifact", return_value=validation),
        patch("devtool.cli.artifact_workspaces", return_value=[]),
        patch("devtool.cli.preview_artifact", return_value={"changes": []}),
        patch("devtool.cli._show_and_confirm_artifact_manifest"),
        patch("devtool.cli._ensure_safe_repo"),
        patch("devtool.cli.format_preview", return_value=""),
        patch("devtool.cli.apply_overlay", return_value=applied),
    ):
        results, raw = _run_apply(config, args, "apply-overlay", str(artifact), log_path=log_path)

    assert results[0].returncode == 0
    assert "Artifact validation passed." in raw
    assert "Artifact validation passed." in log_path.read_text(encoding="utf-8")


def test_custom_plain_go_test_command_gets_structured_report_metadata(tmp_path: Path) -> None:
    from devtool.models import CommandSpec
    from devtool.runners import run_specs

    script = tmp_path / "fake-go-test.sh"
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '--- FAIL: TestPlain (0.01s)'\n"
        "printf '%s\\n' '    plain_test.go:9: expected true, got false'\n"
        "printf '%s\\n' 'FAIL'\n"
        "printf 'FAIL\\texample.com/plain/contracts\\t0.01s\\n'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="go")
    spec = CommandSpec(
        "test",
        [str(script)],
        cwd=str(tmp_path),
        metadata={"runner": "go", "progress_parser": "generic"},
    )
    results, _ = run_specs(config, [spec], verbose=False)
    result = results[0]
    assert result.returncode == 1
    assert result.failure_kind == "test_failure"
    assert result.metadata["go_test"]["packages"]["failed"] == 1
    assert result.metadata["go_test"]["rerun"] == "go test -run '^TestPlain$' example.com/plain/contracts"
    assert any(issue.code == "GO_TEST" for issue in result.issues)


def test_go_json_report_keeps_package_identity_when_parallel_events_interleave() -> None:
    stream = GoTestJSONStream()
    events = [
        {"Action": "start", "Package": "example.com/a"},
        {"Action": "start", "Package": "example.com/b"},
        {"Action": "run", "Package": "example.com/a", "Test": "TestA"},
        {"Action": "run", "Package": "example.com/b", "Test": "TestB"},
        {"Action": "output", "Package": "example.com/a", "Test": "TestA", "Output": "=== RUN   TestA\n"},
        {"Action": "output", "Package": "example.com/b", "Test": "TestB", "Output": "=== RUN   TestB\n"},
        {"Action": "output", "Package": "example.com/a", "Test": "TestA", "Output": "    a_test.go:11: failure from A\n"},
        {"Action": "output", "Package": "example.com/b", "Test": "TestB", "Output": "--- PASS: TestB (0.00s)\n"},
        {"Action": "pass", "Package": "example.com/b", "Test": "TestB"},
        {"Action": "output", "Package": "example.com/a", "Test": "TestA", "Output": "--- FAIL: TestA (0.00s)\n"},
        {"Action": "fail", "Package": "example.com/a", "Test": "TestA"},
        {"Action": "pass", "Package": "example.com/b"},
        {"Action": "fail", "Package": "example.com/a"},
    ]
    for event in events:
        stream.feed(json.dumps(event) + "\n")
    report = stream.report()
    assert report.tests_passed == 1
    assert report.tests_failed == 1
    assert report.packages_passed == 1
    assert report.packages_failed == 1
    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.package == "example.com/a"
    assert failure.test == "TestA"
    assert failure.file == "a_test.go"
    assert failure.line == 11
    assert failure.message == "failure from A"


def test_go_json_report_attributes_parallel_package_compile_failure_to_its_package() -> None:
    stream = GoTestJSONStream()
    events = [
        {"Action": "start", "Package": "example.com/good"},
        {"Action": "start", "Package": "example.com/bad"},
        {"Action": "output", "Package": "example.com/good", "Output": "ok  \texample.com/good\t0.01s\n"},
        {"Action": "output", "Package": "example.com/bad", "Output": "./broken.go:9:4: undefined: missing\n"},
        {"Action": "pass", "Package": "example.com/good"},
        {"Action": "output", "Package": "example.com/bad", "Output": "FAIL\texample.com/bad [build failed]\n"},
        {"Action": "fail", "Package": "example.com/bad"},
    ]
    for event in events:
        stream.feed(json.dumps(event) + "\n")
    report = stream.report()
    assert report.packages_passed == 1
    assert report.packages_failed == 1
    failure = report.failures[0]
    assert failure.package == "example.com/bad"
    assert failure.test == ""
    assert failure.file == "./broken.go"
    assert failure.line == 9
    assert failure.column == 4
    assert failure.message == "undefined: missing"
