from __future__ import annotations

import json
import subprocess
import sys
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
    assert '--mtime="@${SOURCE_DATE_EPOCH}"' in source
    assert 'git -C "$repo_root" log -1 --format=%ct HEAD' in source
    assert 'SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}' in source
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
    # This contract exercises manifest inventory/hashing/provenance against a
    # minimal but version-consistent source root. The dedicated source-release
    # smoke covers full-repository archive reproducibility; archiving the entire
    # repository again here made this unit contract needlessly expensive.
    fixture_root = tmp_path / "repo"
    output = tmp_path / "release"
    required = [
        "release/versions.json",
        "pyproject.toml",
        "devtool/__init__.py",
        "desktop/gtk-control-center/packaging/debian/control",
        "android/devtool-live-hud/app/build.gradle",
    ]
    for relative in required:
        source = ROOT / relative
        destination = fixture_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    (fixture_root / "README.md").write_text("release fixture\n", encoding="utf-8")
    output.mkdir(parents=True)

    # Pre-seed two valid distributable classes so the final release manifest is
    # proven to be an authoritative multi-asset inventory, not source-only.
    import zipfile
    module = output / "devtool-root-bridge-module.zip"
    with zipfile.ZipFile(module, "w") as archive:
        archive.writestr("module.prop", b"id=devtool_bridge\nname=Devtool Bridge\nversion=1\nversionCode=1\n")
        archive.writestr("customize.sh", b"#!/system/bin/sh\n")
        archive.writestr("bin/devtool-root-bridge", b"#!/system/bin/sh\nexec devtool \"$@\"\n")
    wheel = output / "devtool_demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("devtool_demo/__init__.py", b"")
        archive.writestr("devtool_demo-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("devtool_demo-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: devtool-demo\nVersion: 1.0\n")
        archive.writestr("devtool_demo-1.0.dist-info/RECORD", "devtool_demo/__init__.py,,\ndevtool_demo-1.0.dist-info/WHEEL,,\ndevtool_demo-1.0.dist-info/METADATA,,\ndevtool_demo-1.0.dist-info/RECORD,,\n")
    manifest_input = output / ".devtool-current-release-artifacts.json"
    import hashlib
    manifest_input.write_text(json.dumps({"schema_version": 1, "artifacts": [
        {"path": module.name, "sha256": hashlib.sha256(module.read_bytes()).hexdigest()},
        {"path": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
    ]}) + "\n", encoding="utf-8")

    command = [
        "python3", "-S", str(ROOT / "tools" / "build-release.py"),
        "--root", str(fixture_root), "--out-dir", str(output),
        "--current-artifacts-manifest", str(output / ".devtool-current-release-artifacts.json"),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    manifest = json.loads((output / "release-artifacts.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["hash_algorithm"] == "sha256"
    assert len(manifest["source_tree_sha256"]) == 64
    assert isinstance(manifest["artifacts"], list) and len(manifest["artifacts"]) >= 3
    artifact = manifest["artifacts"][0]
    archive = output / artifact["path"]
    assert archive.is_file() and archive.stat().st_size > 0
    names = {item["path"] for item in manifest["artifacts"]}
    assert "devtool-root-bridge-module.zip" in names
    assert wheel.name in names
    for item in manifest["artifacts"]:
        assert item["verification"]["status"] == "passed"
        assert item["provenance"]["producer"]
        assert item["provenance"]["git_head"] == manifest["git_head"]
        assert item["provenance"]["source_date_epoch"] == manifest["source_date_epoch"]
    root_module = next(item for item in manifest["artifacts"] if item["path"] == "devtool-root-bridge-module.zip")
    assert root_module["provenance"]["producer"] == "build-root-bridge-module.sh"
    assert artifact["sha256"] in (output / "SHA256SUMS").read_text(encoding="utf-8")
    assert (output / "SHA256SUMS.all").read_text(encoding="utf-8") == (output / "SHA256SUMS").read_text(encoding="utf-8")
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
    report.release_evidence_hash = ""
    import pytest
    with pytest.raises(RuntimeError, match="stale"):
        write_report(report, tmp_path / "qualification.json", final=True)
    assert first.read_bytes() == original


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
    assert "resolve_python_build_frontend" in make_release
    assert '"${python_build[@]}" --outdir "$out_dir" "$root"' in make_release
    assert 'build-root-bridge-module.sh" "$out_dir/devtool-root-bridge-module.zip"' in make_release
    assert 'tools/build-release.py" --root "$root" --out-dir "$out_dir" --verify-reproducible' in make_release
    assert "SHA256SUMS.all.tmp" not in make_release


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
