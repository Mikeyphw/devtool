from __future__ import annotations

import json
import os
import importlib.util
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from devtool.final_seal import final_seal_report
from devtool.release_qualification import GateResult, QualificationReport, write_report

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("devtool_build_release", ROOT / "tools" / "build-release.py")
assert _SPEC and _SPEC.loader
build_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_release)


def _zip(path: Path, members: dict[str, bytes | str], modes: dict[str, int] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            mode = (modes or {}).get(name, 0o644)
            info.external_attr = (mode & 0o777) << 16
            archive.writestr(info, payload)


def _valid_wheel(path: Path) -> None:
    dist = "devtool_demo-1.0.dist-info/"
    members = {
        "devtool_demo/__init__.py": b"",
        dist + "WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        dist + "METADATA": "Metadata-Version: 2.1\nName: devtool-demo\nVersion: 1.0\n",
        dist + "RECORD": "devtool_demo/__init__.py,,\ndevtool_demo-1.0.dist-info/WHEEL,,\ndevtool_demo-1.0.dist-info/METADATA,,\ndevtool_demo-1.0.dist-info/RECORD,,\n",
    }
    _zip(path, members)


def _valid_root_module(path: Path) -> None:
    _zip(
        path,
        {
            "module.prop": "id=devtool_bridge\nname=Devtool Bridge\nversion=1\nversionCode=1\n",
            "customize.sh": "#!/system/bin/sh\n",
            "bin/devtool-root-bridge": "#!/system/bin/sh\nexec devtool \"$@\"\n",
        },
        {"customize.sh": 0o755, "bin/devtool-root-bridge": 0o755},
    )


def test_s16_r2_01_fake_wheel_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "devtool_demo-1.0-py3-none-any.whl"
    _zip(fake, {"devtool_demo/__init__.py": b""})
    result = build_release.verify_release_artifact(fake)
    assert result["status"] == "failed"
    assert any(check["name"] == "wheel.dist-info" and check["status"] == "failed" for check in result["checks"])


def test_valid_wheel_metadata_still_passes(tmp_path: Path) -> None:
    wheel = tmp_path / "devtool_demo-1.0-py3-none-any.whl"
    _valid_wheel(wheel)
    result = build_release.verify_release_artifact(wheel)
    assert result["status"] == "passed", result
    assert any(check["name"] == "wheel.metadata" and check["status"] == "passed" for check in result["checks"])


@pytest.mark.parametrize("suffix", [".zip", ".whl", ".jar"])
def test_s16_r2_02_zip_family_archive_traversal_is_rejected(tmp_path: Path, suffix: str) -> None:
    artifact = tmp_path / f"artifact{suffix}"
    _zip(artifact, {"../escape.txt": b"owned"})
    result = build_release.verify_release_artifact(artifact)
    assert result["status"] == "failed"
    assert "unsafe zip member path" in json.dumps(result)


def test_s16_r2_02_backslash_member_path_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.zip"
    _zip(artifact, {"dir\\escape.txt": b"owned"})
    result = build_release.verify_release_artifact(artifact)
    assert result["status"] == "failed"
    assert "unsafe zip member path" in json.dumps(result)


def test_s16_r2_02_tar_traversal_and_links_are_rejected(tmp_path: Path) -> None:
    tar_path = tmp_path / "pkg.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("payload", encoding="utf-8")
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(payload, arcname="../escape.txt")
    result = build_release.verify_release_artifact(tar_path)
    assert result["status"] == "failed"
    assert "unsafe tar member path" in json.dumps(result)

    link_tar = tmp_path / "links.tar.gz"
    with tarfile.open(link_tar, "w:gz") as archive:
        info = tarfile.TarInfo("safe-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../escape"
        archive.addfile(info)
    link_result = build_release.verify_release_artifact(link_tar)
    assert link_result["status"] == "failed"
    assert "unsafe tar link member path" in json.dumps(link_result) or "unsafe tar link" in json.dumps(link_result)


def test_s16_10_root_module_verification_is_content_based_not_filename_based(tmp_path: Path) -> None:
    renamed = tmp_path / "plain-module.zip"
    _zip(renamed, {"module.prop": "id=devtool_bridge\nname=Devtool Bridge\n"})
    failed = build_release.verify_release_artifact(renamed)
    assert failed["status"] == "failed"
    assert any(check["name"] == "member:bin/devtool-root-bridge" and check["status"] == "failed" for check in failed["checks"])

    valid = tmp_path / "custom-name.zip"
    _valid_root_module(valid)
    passed = build_release.verify_release_artifact(valid)
    assert passed["status"] == "passed", passed
    assert any(check["name"] == "root_module.content" and check["status"] == "passed" for check in passed["checks"])


def test_s16_05_release_discovery_uses_current_manifest_not_stale_output(tmp_path: Path) -> None:
    out = tmp_path / "release"
    out.mkdir()
    stale = out / "old-root-module.zip"
    current = out / "current-root-module.zip"
    _valid_root_module(stale)
    _valid_root_module(current)
    manifest = out / ".devtool-current-release-artifacts.json"
    manifest.write_text(json.dumps({"schema_version": 1, "artifacts": [{"path": current.name, "sha256": build_release.sha256_file(current)}]}) + "\n", encoding="utf-8")
    records = build_release.discover_release_artifacts(out, current_artifacts_manifest=manifest)
    assert [item["path"] for item in records] == [current.name]


def test_s16_05_manifest_rejects_tampered_current_artifact(tmp_path: Path) -> None:
    out = tmp_path / "release"
    out.mkdir()
    current = out / "current-root-module.zip"
    _valid_root_module(current)
    manifest = out / ".devtool-current-release-artifacts.json"
    manifest.write_text(json.dumps({"schema_version": 1, "artifacts": [{"path": current.name, "sha256": "0" * 64}]}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        build_release.discover_release_artifacts(out, current_artifacts_manifest=manifest)


def test_s16_04_final_qualification_report_rejects_stale_existing_content(tmp_path: Path) -> None:
    report = QualificationReport(
        release="1.0.0",
        started_at="2026-09-13T00:00:00+00:00",
        finished_at="2026-09-13T00:00:01+00:00",
        repository=str(tmp_path),
        results=[GateResult(gate="versions", status="passed", required=True)],
    )
    first = write_report(report, tmp_path / "qualification.json", final=True)
    same = write_report(report, tmp_path / "qualification.json", final=True)
    assert same == first

    report.results.append(GateResult(gate="new-failure", status="failed", required=True))
    report.release_evidence_hash = ""
    with pytest.raises(RuntimeError, match="stale"):
        write_report(report, tmp_path / "qualification.json", final=True)


def test_s16_02_final_seal_rejects_placeholder_docs_and_empty_tests(tmp_path: Path) -> None:
    # Documents and test contracts are checked for structure; retired
    # historical milestone reports do not qualify or disqualify releases.
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="python"\nplatform="server"\n[workspace]\ndefault_target="devtool"\n[targets.devtool]\nrunner="python"\nplatform="server"\nroot="."\n', encoding="utf-8")
    from devtool import final_seal as seal
    for rel in seal._REQUIRED_DOCS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Placeholder\nTODO: fill\n", encoding="utf-8")
    for rel in seal._REQUIRED_TESTS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    for rel in seal._REQUIRED_EXAMPLES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nrunner="python"\nplatform="server"\n', encoding="utf-8")
    (tmp_path / "completions").mkdir()
    (tmp_path / "completions/_devtool").write_text("stale", encoding="utf-8")
    (tmp_path / "devtool").mkdir(exist_ok=True)
    (tmp_path / "devtool/validation_profiles.py").write_text('{"final-seal": {}}\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("FS03 final-seal\n", encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs/FINAL_RELEASE_SEAL.md").write_text("# FS03\nfinal-seal\n", encoding="utf-8")
    payload = final_seal_report(tmp_path)
    failed = {item["id"] for item in payload["checks"] if item["status"] == "fail"}
    assert "roadmap.reports" not in {item["id"] for item in payload["checks"]}
    assert "docs.required" in failed
    assert "tests.roadmap-contracts" in failed


def test_s16_01_completion_file_is_synchronized_with_generated_constant() -> None:
    from devtool.completion import ZSH_COMPLETION
    assert (ROOT / "completions/_devtool").read_text(encoding="utf-8") == ZSH_COMPLETION
