from __future__ import annotations
import subprocess
from pathlib import Path
from devtool.repository_pack import _status, plan_repository_pack, RepositoryPackRequest, RepositoryPackMode

def run(repo,*args):
    subprocess.run(["git","-C",str(repo),*args],capture_output=True,text=True,check=True)

def repository(tmp_path):
    repo=tmp_path / "source"
    repo.mkdir()
    run(repo,"init","-q")
    run(repo,"config","user.email","audit@example.invalid")
    run(repo,"config","user.name","Audit")
    (repo / "source.txt").write_text("stable\n")
    run(repo,"add","source.txt")
    run(repo,"commit","-qm","initial")
    return repo

def test_private_untracked_names_never_change_status_or_plan(tmp_path):
    repo=repository(tmp_path)
    first=_status(repo)
    pack=lambda: plan_repository_pack(RepositoryPackRequest(repositories=(str(repo),),output=str(tmp_path/"pack.zip"),mode=RepositoryPackMode.WORKTREE))
    before=pack()
    private=repo/".devtool"/"private"/"run-v3";private.mkdir(parents=True)
    for name in ("receipt with spaces.json", "receipt\twith-tab.json", 'receipt "quoted".json', "receipt-à.json"):
        (private/name).write_text("ephemeral")
    assert _status(repo)==first
    assert pack().plan_id==before.plan_id

def test_real_nonprivate_changes_affect_status_and_fingerprint(tmp_path):
    repo=repository(tmp_path)
    before=_status(repo)
    (repo/"source notes with spaces.txt").write_text("changed")
    after=_status(repo)
    assert after != before
    assert "source notes with spaces.txt" in after
