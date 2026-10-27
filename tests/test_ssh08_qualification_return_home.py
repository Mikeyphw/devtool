from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from devtool import ssh_qualification
from devtool.ssh_tmux_runner import SSHRunnerConfig


@pytest.mark.parametrize("value, expected", [
    ("~/.cache/devtool/ssh-runs/qualification/q-abc", "/data/data/com.termux/files/home/.cache/devtool/ssh-runs/qualification/q-abc"),
    ("$HOME/.cache/devtool/ssh-runs/qualification/q-abc", "/data/data/com.termux/files/home/.cache/devtool/ssh-runs/qualification/q-abc"),
    ("/var/tmp/devtool/qualification/q-abc", "/var/tmp/devtool/qualification/q-abc"),
])
def test_qualification_rsync_return_uses_literal_remote_absolute_path(value, expected):
    got = ssh_qualification._qualification_return_absolute_root(value, remote_home="/data/data/com.termux/files/home")
    assert got == expected
    assert "/~/" not in got


@pytest.mark.parametrize("root, home", [
    ("~/qualify/q", ""),
    ("~/qualify/q", "relative/home"),
    ("~/../secrets", "/home/a"),
    ("/data/a/../b", "/home/a"),
    ("$HOME/../x", "/home/a"),
    ("~other/secret", "/home/a"),
    ("relative/data", "/home/a"),
    ("~/bad\npath", "/home/a"),
])
def test_qualification_return_rejects_unsafe_paths(root,home):
    with pytest.raises(ValueError):
        ssh_qualification._qualification_return_absolute_root(root, remote_home=home)


def test_return_calls_rsync_with_absolute_root_and_verifies_nonce(monkeypatch,tmp_path):
    cfg=SSHRunnerConfig(host="mom",profile="mom")
    seen={}
    def fake_build(config, *, remote_root, local_repo_root, patterns, remote_rsync):
        seen["root"]=remote_root
        assert remote_root.startswith("/data/data/com.termux/files/home/")
        return ["rsync", "-fake"]
    def fake_run(argv, **kwargs):
        assert argv == ["rsync", "-fake"]
        (tmp_path/"artifact.txt").write_text("q-abc\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv,0,stdout="",stderr="")
    monkeypatch.setattr(ssh_qualification, "build_return_rsync_argv",fake_build)
    monkeypatch.setattr(ssh_qualification.subprocess,"run",fake_run)
    result=ssh_qualification._retrieve_probe_artifact(
        cfg, remote_root="~/.cache/devtool/ssh-runs/qualification/q-abc",
        local_root=tmp_path,remote_rsync="rsync",nonce="q-abc",
        remote_home="/data/data/com.termux/files/home",
    )
    assert result["ok"] is True
    assert seen["root"] == "/data/data/com.termux/files/home/.cache/devtool/ssh-runs/qualification/q-abc"


def test_missing_remote_home_stops_before_transfer(monkeypatch,tmp_path):
    monkeypatch.setattr(ssh_qualification,"build_return_rsync_argv",lambda *a,**kw: (_ for _ in ()).throw(AssertionError("rsync called")))
    with pytest.raises(ValueError):
        ssh_qualification._retrieve_probe_artifact(
            SSHRunnerConfig(host="mom"),remote_root="~/qualify/q",local_root=tmp_path,
            remote_rsync="rsync",nonce="q",remote_home="",
        )
    assert not (tmp_path / "artifact.txt").exists()
