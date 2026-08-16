from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devtool.release_qualification import GateResult, QualificationReport, write_report

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ov15_pack_repo_is_deterministic_checked_and_repository_relative() -> None:
    source = read("pack_repo.sh")
    assert 'default_output="/tmp/$(basename "$repo_root")-$(date +%Y%m%d-%H%M%S).tar.zst"' in source
    assert "--compression zstd|gzip" in source
    assert "validate_repo_relative_input" in source
    assert '|*"/../"*|../*|*/..|' in source
    assert "LC_ALL=C sort -z" in source
    assert '--mtime="@${SOURCE_DATE_EPOCH:-1784030400}"' in source
    assert "--owner=0 --group=0 --numeric-owner" in source
    assert "--manifest" in source
    assert 'sha256sum "$output_path" > "$output_path.sha256"' in source


def test_ov15_installer_state_stamps_are_atomic_private_and_fsynced() -> None:
    source = read("install.sh")
    assert "atomic write stamp" in source
    assert 'temporary=$(mktemp "$directory/.${path##*/}.XXXXXX")' in source
    assert "chmod 0600 \"$temporary\"" in source
    assert "os.fsync(fd)" in source
    assert "mv -f -- \"$temporary\" \"$path\"" in source


def test_ov16_build_release_manifest_is_hash_checked_and_deterministic(tmp_path: Path) -> None:
    command = ["python3", str(ROOT / "tools" / "build-release.py"), "--root", str(ROOT), "--out-dir", str(tmp_path), "--verify-reproducible"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    manifest = json.loads((tmp_path / "release-artifacts.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["hash_algorithm"] == "sha256"
    assert len(manifest["source_tree_sha256"]) == 64
    assert isinstance(manifest["artifacts"], list) and manifest["artifacts"]
    artifact = manifest["artifacts"][0]
    archive = tmp_path / artifact["path"]
    assert archive.is_file() and archive.stat().st_size > 0
    assert artifact["sha256"] in (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    builder = read("tools/build-release.py")
    assert "verify_release_manifest(out_dir, manifest)" in builder
    assert "atomic_write_text(manifest_path" in builder
    assert "_safe_relative_path" in builder


def test_ov16_release_qualification_final_report_is_hash_sealed(tmp_path: Path) -> None:
    report = QualificationReport(
        release="1.1.0-rc.3",
        started_at="2026-08-16T00:00:00+00:00",
        finished_at="2026-08-16T00:00:01+00:00",
        repository=str(tmp_path),
        git_initial_head="abc",
        git_final_head="abc",
        git_initial_status="",
        git_final_status="",
        results=[GateResult(gate="versions", status="passed", required=True)],
    )
    first = write_report(report, tmp_path / "qualification.json", final=True)
    original = first.read_bytes()
    assert first.name == "qualification.final.json"
    payload = json.loads(original)
    assert payload["schema_version"] == 2
    assert payload["report_kind"] == "devtool-release-qualification"
    assert len(payload["release_evidence_hash"]) == 64

    report.results.append(GateResult(gate="must-not-overwrite", status="failed", required=True))
    second = write_report(report, tmp_path / "qualification.json", final=True)
    assert second == first
    assert second.read_bytes() == original


def test_ov16_release_qualification_tracks_git_invariants_and_final_cli() -> None:
    source = read("devtool/release_qualification.py")
    cli = read("tools/release-qualify.py")
    make_release = read("tools/make-release-candidate.sh")
    assert "git-worktree-invariants" in source
    assert "git_initial_head" in source
    assert "git_final_head" in source
    assert "--untracked-files=no" in source
    assert "release_evidence_hash" in source
    assert "test_phase33_validation_release_seal.py" not in source
    assert "test_phase33_validation_release_seal.py" not in read("tools/validate-validation-release.py")
    assert "--final" in cli
    assert "Evidence SHA-256" in cli
    assert '--output "$out_dir/qualification.json" --final' in make_release
    assert "SHA256SUMS.all.tmp" in make_release


def test_ov16_ci_provisions_toolchains_signing_and_uploads_evidence() -> None:
    workflow = read(".github/workflows/release-qualification.yml")
    for token in (
        "permissions:",
        "contents: read",
        "actions/setup-python@v6",
        "python-version: ['3.11', '3.12', '3.13']",
        "group: [1, 2, 3, 4]",
        "actions/setup-java@v5",
        "java-version: '17'",
        "sdkmanager\" \"platforms;android-36\" \"build-tools;36.0.0\" \"platform-tools\"",
        "keytool -genkeypair",
        "DEVTOOL_RELEASE_STORE_FILE=$RUNNER_TEMP/devtool-ci-release.jks",
        "lintRelease testReleaseUnitTest verifyReleaseApk",
        "connectedDebugAndroidTest",
        "--final --output dist/release-qualification.json",
        "dist/release-qualification.final.json",
        "actions/upload-artifact@v5",
    ):
        assert token in workflow


def test_ov16_copied_summary_counts_raw_warning_details() -> None:
    cli = read("devtool/cli.py")
    report = read("DEVTOOL_OV15_OV16_FINAL_SEAL_REPORT.md")
    assert "effective_warning_count = max(warnings, unique_warning_count)" in cli
    assert "overlay_warning_count = max(overlay_warning_count, unique_warning_count)" in cli
    assert '_structured_issue_details("warning")' in cli
    assert "_raw_warning_details(raw_output)" in cli
    assert "raw warning" in report


def test_ov16_copied_summary_includes_all_errors_warnings_and_real_failure_reason() -> None:
    cli = read("devtool/cli.py")
    report = read("DEVTOOL_OV15_OV16_FINAL_SEAL_REPORT.md")
    assert "Failure reason:" in cli
    assert 'lines.extend(f"- {error}" for error in error_details)' in cli
    assert "failure_warning_details" in cli
    assert "_raw_error_details(raw_error_source)" in cli
    assert "complete deduplicated diagnostic detail set" in report
    assert "actual failing action" in report


def test_ov16_successful_stdout_diagnostics_are_runner_agnostic_false_failure_guard() -> None:
    runners = read("devtool/runners.py")
    report = read("DEVTOOL_OV15_OV16_FINAL_SEAL_REPORT.md")
    assert "def _trust_successful_stdout_diagnostics" in runners
    assert "successful process boundary captured non-authoritative stdout diagnostics" in runners
    assert "runner-agnostic" in report
    assert "pytest-specific" not in report
