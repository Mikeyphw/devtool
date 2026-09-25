from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import subprocess

import pytest

from devtool import cli
from devtool.git_state import GitStateError, collect_git_state


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _init_repo(root: Path, *, commit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.name", "Devtool Test")
    _git(root, "config", "user.email", "devtool@example.invalid")
    if commit:
        (root / "tracked.txt").write_text("base\n", encoding="utf-8")
        _git(root, "add", "tracked.txt")
        _git(root, "commit", "-qm", "initial")
    return root


def test_git_state_clean_repository_has_authoritative_identity_and_index(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    state = collect_git_state(root)

    assert state.available is True
    assert state.root == str(root.resolve())
    assert state.requested_root == str(root.resolve())
    assert len(state.repository_id) == 64
    assert len(state.repository_identity["fingerprint"]) == 64
    assert state.head["sha"] == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert state.head["branch"]
    assert state.head["detached"] is False
    assert state.head["unborn"] is False
    assert state.upstream["configured"] is False
    assert len(state.index["tree_sha"]) in {40, 64}
    assert len(state.index["entries_fingerprint"]) == 64
    assert state.staged == ()
    assert state.unstaged == ()
    assert state.untracked == ()
    assert state.unmerged == ()
    assert state.operations["active"] == []
    assert state.dirty is False
    assert state.object_format in {"sha1", "sha256"}
    assert state.capabilities["git_version"].startswith("git version ")
    assert state.worktree["is_main"] is True
    assert state.worktree["is_linked"] is False
    assert len(state.worktrees) == 1
    assert state.worktrees[0].current is True


def test_git_state_represents_unborn_head_truthfully(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo", commit=False)
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    _git(root, "add", "new.txt")

    state = collect_git_state(root)

    assert state.head["sha"] == ""
    assert state.head["branch"]
    assert state.head["unborn"] is True
    assert state.head["detached"] is False
    assert state.upstream["configured"] is False
    assert [item.path for item in state.staged] == ["new.txt"]
    assert state.dirty is True


def test_git_state_represents_detached_head_truthfully(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    _git(root, "checkout", "--detach", "-q", sha)

    state = collect_git_state(root)

    assert state.head["sha"] == sha
    assert state.head["branch"] == ""
    assert state.head["symbolic_ref"] == ""
    assert state.head["detached"] is True
    assert state.head["unborn"] is False
    assert state.upstream["configured"] is False


def test_git_state_preserves_staged_unstaged_untracked_and_rename_truth(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    _git(root, "mv", "tracked.txt", "renamed.txt")
    _git(root, "add", "renamed.txt")
    (root / "renamed.txt").write_text("working copy\n", encoding="utf-8")
    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    state = collect_git_state(root)

    assert len(state.staged) == 1
    assert state.staged[0].status.startswith("R")
    assert state.staged[0].old_path == "tracked.txt"
    assert state.staged[0].path == "renamed.txt"
    assert [(item.status, item.path) for item in state.unstaged] == [("M", "renamed.txt")]
    assert state.untracked == ("untracked.txt",)
    assert state.dirty is True


def test_linked_worktrees_share_repository_id_but_have_distinct_worktree_ids(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "main")
    linked = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "linked-branch", str(linked))

    main_state = collect_git_state(root)
    linked_state = collect_git_state(linked)

    assert main_state.repository_id == linked_state.repository_id
    assert main_state.worktree["id"] != linked_state.worktree["id"]
    assert main_state.worktree["is_main"] is True
    assert linked_state.worktree["is_linked"] is True
    assert {item.root for item in main_state.worktrees} == {str(root.resolve()), str(linked.resolve())}
    assert sum(1 for item in linked_state.worktrees if item.current) == 1
    assert next(item for item in linked_state.worktrees if item.current).root == str(linked.resolve())


def test_nested_git_repository_resolves_to_its_own_root_and_id(tmp_path: Path) -> None:
    parent = _init_repo(tmp_path / "parent")
    child = _init_repo(parent / "nested")

    parent_state = collect_git_state(parent)
    child_state = collect_git_state(child)

    assert parent_state.root == str(parent.resolve())
    assert child_state.root == str(child.resolve())
    assert parent_state.repository_id != child_state.repository_id
    assert parent_state.git_common_dir != child_state.git_common_dir


def test_operation_state_reports_all_supported_in_progress_markers(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    (git_dir / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    (git_dir / "rebase-merge").mkdir()
    (git_dir / "CHERRY_PICK_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    (git_dir / "REVERT_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    (git_dir / "BISECT_LOG").write_text("test\n", encoding="utf-8")

    state = collect_git_state(root)

    assert set(state.operations["active"]) == {"merge", "rebase", "cherry_pick", "revert", "bisect"}
    for name in state.operations["active"]:
        assert state.operations[name]["active"] is True
        assert state.operations[name]["paths"]


def test_upstream_and_remote_refs_containing_head_are_explicit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = _init_repo(tmp_path / "repo")
    _git(root, "remote", "add", "origin", str(remote))
    branch = _git(root, "branch", "--show-current").stdout.strip()
    _git(root, "push", "-qu", "origin", branch)

    state = collect_git_state(root)

    assert state.upstream["configured"] is True
    assert state.upstream["remote"] == "origin"
    assert state.upstream["sha"] == state.head["sha"]
    names = {item.name for item in state.protected_remote_refs}
    assert state.upstream["name"] in names
    assert any(name.startswith("refs/remotes/origin/") for name in names)
    assert all(item.contains_head for item in state.protected_remote_refs)


def test_partial_clone_promisor_configuration_is_represented(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    _git(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _git(root, "config", "remote.origin.promisor", "true")

    state = collect_git_state(root)

    assert state.partial_clone is True
    assert state.promisor_remotes == ("origin",)


def test_shallow_clone_state_is_represented(tmp_path: Path) -> None:
    source = _init_repo(tmp_path / "source")
    (source / "second.txt").write_text("second\n", encoding="utf-8")
    _git(source, "add", "second.txt")
    _git(source, "commit", "-qm", "second")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", source.resolve().as_uri(), str(clone)],
        check=True,
    )

    state = collect_git_state(clone)

    assert state.shallow is True


def test_submodule_relationships_are_reported_without_collapsing_identity(tmp_path: Path) -> None:
    child = _init_repo(tmp_path / "child")
    parent = _init_repo(tmp_path / "parent")
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), "deps/child")
    _git(parent, "commit", "-qm", "add child submodule")

    state = collect_git_state(parent)

    assert len(state.submodules) == 1
    assert state.submodules[0].path == "deps/child"
    assert state.submodules[0].status == "initialized"
    assert len(state.submodules[0].sha) in {40, 64}


def test_cli_git_state_text_and_machine_output(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")

    text_stream = io.StringIO()
    with redirect_stdout(text_stream):
        rc = cli.main(["--repo", str(root), "git", "state"])
    assert rc == 0
    assert "repository_id" in text_stream.getvalue()
    assert "root" in text_stream.getvalue()
    assert root.name in text_stream.getvalue()

    json_stream = io.StringIO()
    with redirect_stdout(json_stream):
        rc = cli.main(["--repo", str(root), "git", "state", "--json"])
    assert rc == 0
    envelope = json.loads(json_stream.getvalue())
    assert envelope["ok"] is True
    assert envelope["data"]["kind"] == "git-state"
    assert envelope["data"]["repository_id"] == collect_git_state(root).repository_id
    assert envelope["data"]["root"] == str(root.resolve())


def test_cli_git_state_non_repository_fails_closed_with_machine_error(tmp_path: Path) -> None:
    stream = io.StringIO()
    with redirect_stdout(stream):
        rc = cli.main(["--repo", str(tmp_path), "git", "state", "--json"])
    assert rc == 1
    envelope = json.loads(stream.getvalue())
    assert envelope["ok"] is False
    assert envelope["data"]["kind"] == "git-state"
    assert envelope["data"]["available"] is False
    assert "repository unavailable" in envelope["data"]["error"].lower()


def test_collect_git_state_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(GitStateError, match="Git repository unavailable"):
        collect_git_state(tmp_path)


def test_git_state_canonicalizes_symlink_without_changing_repository_id(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    link = tmp_path / "repo-link"
    link.symlink_to(root, target_is_directory=True)

    direct = collect_git_state(root)
    through_link = collect_git_state(link)

    assert through_link.root == str(root.resolve())
    assert through_link.requested_root == str(root.resolve())
    assert through_link.repository_id == direct.repository_id


def test_unmerged_index_is_explicit_and_index_tree_failure_is_preserved(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    base_branch = _git(root, "branch", "--show-current").stdout.strip()
    _git(root, "checkout", "-qb", "other")
    (root / "tracked.txt").write_text("other\n", encoding="utf-8")
    _git(root, "commit", "-qam", "other change")
    _git(root, "checkout", "-q", base_branch)
    (root / "tracked.txt").write_text("main\n", encoding="utf-8")
    _git(root, "commit", "-qam", "main change")
    merge = _git(root, "merge", "other", check=False)
    assert merge.returncode != 0

    state = collect_git_state(root)

    assert state.unmerged == ("tracked.txt",)
    assert state.index["unmerged"] is True
    assert state.index["tree_sha"] == ""
    assert state.index["tree_error"]
    assert state.operations["merge"]["active"] is True
    assert state.dirty is True


def test_sha256_object_format_is_reported_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "sha256-repo"
    result = subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", str(root)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git(root, "config", "user.name", "Devtool Test")
    _git(root, "config", "user.email", "devtool@example.invalid")
    (root / "tracked.txt").write_text("sha256\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "initial")

    state = collect_git_state(root)

    assert state.object_format == "sha256"
    assert len(state.head["sha"]) == 64
    assert len(state.index["tree_sha"]) == 64


def test_index_tree_probe_does_not_add_repository_objects(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    nested = root / "nested"
    nested.mkdir()
    (nested / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(root, "add", "nested/staged.txt")
    object_dir = Path(_git(root, "rev-parse", "--git-common-dir").stdout.strip())
    if not object_dir.is_absolute():
        object_dir = root / object_dir
    object_dir = object_dir.resolve() / "objects"

    before = {
        path.relative_to(object_dir).as_posix()
        for path in object_dir.rglob("*")
        if path.is_file()
    }
    state = collect_git_state(root)
    after = {
        path.relative_to(object_dir).as_posix()
        for path in object_dir.rglob("*")
        if path.is_file()
    }

    assert len(state.index["tree_sha"]) in {40, 64}
    assert after == before


def test_configured_but_unresolved_upstream_is_not_reported_as_missing_configuration(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    branch = _git(root, "branch", "--show-current").stdout.strip()
    _git(root, "config", f"branch.{branch}.remote", "origin")
    _git(root, "config", f"branch.{branch}.merge", "refs/heads/not-fetched")

    state = collect_git_state(root)

    assert state.upstream["configured"] is True
    assert state.upstream["resolved"] is False
    assert state.upstream["remote"] == "origin"
    assert state.upstream["merge_ref"] == "refs/heads/not-fetched"
    assert state.upstream["name"] == ""
    assert state.upstream["sha"] == ""


def test_submodule_path_with_spaces_is_preserved(tmp_path: Path) -> None:
    child = _init_repo(tmp_path / "child")
    parent = _init_repo(tmp_path / "parent")
    path = "deps/child module"
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), path)
    _git(parent, "commit", "-qm", "add spaced submodule")

    state = collect_git_state(parent)

    assert [item.path for item in state.submodules] == [path]


def test_cli_git_state_from_unenrolled_nested_repo_does_not_borrow_parent_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _init_repo(tmp_path / "parent")
    (parent / ".devtool.toml").write_text("[project]\nrunner='command'\n", encoding="utf-8")
    child = _init_repo(parent / "nested")
    monkeypatch.chdir(child)

    stream = io.StringIO()
    with redirect_stdout(stream):
        rc = cli.main(["git", "state", "--json"])

    assert rc == 0
    envelope = json.loads(stream.getvalue())
    assert envelope["data"]["root"] == str(child.resolve())
    assert envelope["data"]["root"] != str(parent.resolve())


def test_git_state_machine_payload_does_not_expose_remote_url_or_credentials(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    secret_url = "https://user:super-secret-token@example.invalid/private/repo.git"
    _git(root, "remote", "add", "origin", secret_url)

    encoded = json.dumps(collect_git_state(root).as_dict(), sort_keys=True)

    assert secret_url not in encoded
    assert "super-secret-token" not in encoded
    assert "git_remote_fingerprint" in encoded


def test_linked_worktree_identity_survives_git_worktree_move(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "main")
    linked = tmp_path / "linked"
    moved = tmp_path / "moved-linked"
    _git(root, "worktree", "add", "-q", "-b", "movable", str(linked))
    before = collect_git_state(linked)

    _git(root, "worktree", "move", str(linked), str(moved))
    after = collect_git_state(moved)

    assert before.repository_id == after.repository_id
    assert before.worktree["id"] == after.worktree["id"]
    assert before.root != after.root


def test_submodule_checkout_is_its_own_main_worktree(tmp_path: Path) -> None:
    child = _init_repo(tmp_path / "child")
    parent = _init_repo(tmp_path / "parent")
    submodule_root = parent / "deps/child"
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), "deps/child")
    _git(parent, "commit", "-qm", "add child")

    state = collect_git_state(submodule_root)

    assert state.root == str(submodule_root.resolve())
    assert state.worktree["is_main"] is True
    assert state.worktree["is_linked"] is False
    assert state.worktrees[0].worktree_id == state.worktree["id"]


def test_repository_id_is_stable_across_head_and_remote_metadata_changes(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    before = collect_git_state(root).repository_id
    (root / "second.txt").write_text("second\n", encoding="utf-8")
    _git(root, "add", "second.txt")
    _git(root, "commit", "-qm", "second")
    _git(root, "remote", "add", "origin", "https://example.invalid/one.git")
    middle = collect_git_state(root).repository_id
    _git(root, "remote", "set-url", "origin", "https://example.invalid/two.git")
    after = collect_git_state(root).repository_id

    assert before == middle == after


def test_partial_upstream_configuration_is_explicit(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    branch = _git(root, "branch", "--show-current").stdout.strip()
    _git(root, "config", f"branch.{branch}.remote", "origin")

    state = collect_git_state(root)

    assert state.upstream["configured"] is False
    assert state.upstream["partial_configuration"] is True
    assert state.upstream["remote"] == "origin"
    assert state.upstream["merge_ref"] == ""


def test_corrupt_symbolic_head_ref_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    branch = _git(root, "branch", "--show-current").stdout.strip()
    ref_path = root / ".git" / "refs" / "heads" / branch
    ref_path.write_text("not-a-valid-object-id\n", encoding="utf-8")

    with pytest.raises(GitStateError, match="Git HEAD"):
        collect_git_state(root)


def test_git_state_probe_does_not_mutate_head_refs_or_index(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    (root / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(root, "add", "staged.txt")
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    head_path = git_dir / "HEAD"
    index_path = git_dir / "index"
    refs_root = git_dir / "refs"

    def snapshot_files(base: Path) -> dict[str, bytes]:
        if not base.exists():
            return {}
        return {
            path.relative_to(base).as_posix(): path.read_bytes()
            for path in base.rglob("*")
            if path.is_file()
        }

    before_head = head_path.read_bytes()
    before_index = index_path.read_bytes()
    before_refs = snapshot_files(refs_root)

    collect_git_state(root)

    assert head_path.read_bytes() == before_head
    assert index_path.read_bytes() == before_index
    assert snapshot_files(refs_root) == before_refs
