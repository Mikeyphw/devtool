from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from devtool.artifacts import SUPPORTED_ARCHIVE_SUFFIXES
from devtool.cli import build_parser, main
from devtool.final_seal import final_seal_report
from devtool.pytest_profile import build_pytest_profile_command, parse_pytest_duration_output, run_pytest_profile

ROOT = Path(__file__).resolve().parents[1]


def test_final_seal_tracks_all_post_handoff_roadmap_reports_and_tests() -> None:
    payload = final_seal_report(ROOT)
    assert payload["status"] == "pass", payload
    assert payload["summary"]["checks"] >= 15
    assert payload["summary"]["roadmap_reports"] >= 19
    assert payload["summary"]["roadmap_test_files"] >= 21
    check_ids = {item["id"] for item in payload["checks"]}
    assert "artifacts.archive-formats" in check_ids

    reports = {
        "HROPS01_COMPLETE_EMBEDDED_HOOK_RUNTIME_REPORT.md",
        "AOPS02_ANDROID_EXECUTION_DEVICE_FABRIC_REPORT.md",
        "AOPS03_ANDROID_SIGNING_INSTRUMENTATION_OPERATIONS_REPORT.md",
        "AOPS04_ANDROID_RELEASE_EVIDENCE_FINAL_SEAL_REPORT.md",
        "PYTEST_REVITALIZATION_REPORT.md",
        "GRADLE_ANDROID_TASK_OPTIMIZATION_REPORT.md",
        "ROADMAP_DELIVERY_AUDIT_REPORT.md",
    }
    for report in reports:
        assert (ROOT / report).is_file(), report

    tests = {
        "tests/test_hrops01_hook_runtime.py",
        "tests/test_aops02_android_execution_device_fabric.py",
        "tests/test_aops03_android_signing_instrumentation_operations.py",
        "tests/test_aops04_android_release_evidence_final_seal.py",
        "tests/test_pytest_revitalization.py",
        "tests/test_gradle_android_task_optimization.py",
        "tests/test_roadmap_delivery_audit.py",
    }
    for rel in tests:
        assert (ROOT / rel).is_file(), rel


def test_archive_format_promise_includes_zip_and_tar_families() -> None:
    required = {".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst"}
    assert required.issubset(set(SUPPORTED_ARCHIVE_SUFFIXES))


def test_pytest_profile_command_is_registered() -> None:
    args = build_parser().parse_args(["pytest", "profile", "tests/test_cli.py", "--threshold", "1.5", "--top", "7", "--strict", "--json"])
    assert args.command == "pytest"
    assert args.pytest_command == "profile"
    assert args.pytest_args == ["tests/test_cli.py"]
    assert args.threshold == 1.5
    assert args.top == 7
    assert args.strict is True
    assert args.json is True


def test_pytest_profile_builds_strict_slow_command() -> None:
    command = build_pytest_profile_command(["tests/test_cli.py"], threshold_seconds=1.25, top=5, strict_slow=True)
    assert command[:3][-2:] == ["-m", "pytest"]
    assert "tests/test_cli.py" in command
    assert "--devtool-slow-threshold=1.25" in command
    assert "--durations=5" in command
    assert "--durations-min=1.25" in command
    assert "--devtool-strict-slow" in command


def test_pytest_profile_parses_pytest_and_devtool_slow_output() -> None:
    output = """
============================= slowest durations =============================
3.21s call     tests/test_go.py::test_real_build
1.40s setup    tests/test_setup.py::test_fixture

----------------------- Devtool slow tests (>= 1s) -----------------------
   2.03s  tests/test_android.py::test_adb_probe
"""
    entries = parse_pytest_duration_output(output, threshold_seconds=1.0, limit=3)
    assert [entry.nodeid for entry in entries] == [
        "tests/test_go.py::test_real_build",
        "tests/test_android.py::test_adb_probe",
        "tests/test_setup.py::test_fixture",
    ]


def test_pytest_profile_json_surface_uses_structured_result(tmp_path: Path) -> None:
    fake = Mock()
    fake.returncode = 0
    fake.stdout = "2.50s call tests/test_demo.py::test_slow\n"
    fake.stderr = ""
    with patch("devtool.pytest_profile.subprocess.run", return_value=fake) as run:
        result = run_pytest_profile(tmp_path, ["tests/test_demo.py"], threshold_seconds=2.0, top=10, strict_slow=True, env={})
    assert result.ok is True
    assert result.entries[0].nodeid == "tests/test_demo.py::test_slow"
    assert "--devtool-strict-slow" in run.call_args.args[0]
    assert run.call_args.kwargs["cwd"] == tmp_path.resolve()


def test_pytest_profile_cli_runs_and_emits_json(tmp_path: Path) -> None:
    fake_result = Mock()
    fake_result.returncode = 0
    fake_result.as_dict.return_value = {"schema_version": 1, "ok": True, "slow_tests": []}
    with patch("devtool.pytest_profile.run_pytest_profile", return_value=fake_result):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--repo", str(ROOT), "pytest", "profile", "tests/test_cli.py", "--json"])
    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
