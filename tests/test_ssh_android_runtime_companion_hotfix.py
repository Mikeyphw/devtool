from __future__ import annotations

from pathlib import Path

import pytest

from devtool.ssh_tmux_runner import SSHRunnerConfig, _stage_support_files_for_spec


def test_ssh_android_builder_stages_toolchain_companion_beside_remote_builder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    builder = runtime / "build-apk"
    companion = runtime / "termux_toolchains.py"
    builder.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    companion.write_text("# companion\n", encoding="utf-8")

    cfg = SSHRunnerConfig(host="host", session="android")
    rewritten, staged = _stage_support_files_for_spec(
        cfg,
        ["python3", str(builder), str(repo)],
        local_root=repo,
        remote_root="/srv/repo",
        session="android",
        dry_run=True,
        metadata={"runner": "android", "android_builder": str(builder)},
    )

    assert rewritten[1] != str(builder)
    assert rewritten[1].endswith("-build-apk")
    remote_support_dir = rewritten[1].rsplit("/", 1)[0]
    assert (companion, f"{remote_support_dir}/termux_toolchains.py") in staged


def test_ssh_android_builder_missing_companion_fails_before_remote_launch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    builder = tmp_path / "build-apk"
    builder.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    cfg = SSHRunnerConfig(host="host", session="android")
    with pytest.raises(ValueError, match="missing local toolchain companion"):
        _stage_support_files_for_spec(
            cfg,
            ["python3", str(builder), str(repo)],
            local_root=repo,
            remote_root="/srv/repo",
            session="android",
            dry_run=True,
            metadata={"runner": "android", "android_builder": str(builder)},
        )


def test_ssh_android_builder_uploads_companion_to_exact_sibling_remote_name(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    builder = runtime / "build-apk"
    companion = runtime / "termux_toolchains.py"
    builder.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    companion.write_text("# companion\n", encoding="utf-8")

    cfg = SSHRunnerConfig(host="host", session="android")
    uploads: list[tuple[Path, str]] = []
    monkeypatch.setattr("devtool.ssh_tmux_runner._assert_session_inactive", lambda *a, **k: None)
    monkeypatch.setattr("devtool.ssh_tmux_runner._ensure_remote_dir", lambda *a, **k: None)
    monkeypatch.setattr(
        "devtool.ssh_tmux_runner._copy_local_file_to_remote",
        lambda _cfg, local, remote: uploads.append((local, remote)),
    )

    rewritten, staged = _stage_support_files_for_spec(
        cfg,
        ["python3", str(builder), str(repo)],
        local_root=repo,
        remote_root="/srv/repo",
        session="android",
        dry_run=False,
        metadata={"runner": "android", "android_builder": str(builder)},
    )

    remote_support_dir = rewritten[1].rsplit("/", 1)[0]
    expected_companion = (companion, f"{remote_support_dir}/termux_toolchains.py")
    assert expected_companion in staged
    assert expected_companion in uploads
    assert uploads == staged
