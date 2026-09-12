from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud"
JAVA = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"


def read(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_phase4_exposes_full_guarded_git_surface() -> None:
    detail = read("RepositoryDetailActivity.java")
    broker = read("RootCommandBroker.java")
    workspace = read("RepositoryWorkspaceStore.java")
    assert 'String[] labels = {"Overview", "Changes", "Commits", "Branches", "Sync", "Activity", "Runs", "Metadata"}' in detail
    for action in (
        "Commit staged changes",
        "Fetch all + prune",
        "Pull · fast-forward only",
        "Push + set upstream",
        "Create branch",
        "Checkout as tracking branch",
        "Stash including untracked",
        "Pop latest stash",
        "Hard reset tracked changes",
    ):
        assert action in detail
    for method in (
        "commit(", "fetchAll(", "pullFastForward(", "push(", "checkoutBranch(",
        "createBranch(", "stashIncludingUntracked(", "popStash(", "hardResetTracked(",
    ):
        assert method in broker
    for operation in ("COMMIT", "FETCH", "PULL_FF", "PUSH", "CHECKOUT", "CREATE_BRANCH", "STASH", "POP_STASH", "HARD_RESET"):
        assert operation in workspace


def test_phase4_network_and_destructive_safety_contract() -> None:
    detail = read("RepositoryDetailActivity.java")
    broker = read("RootCommandBroker.java")
    assert "GIT_TERMINAL_PROMPT=0" in broker
    assert "fetch --all --prune --tags" in broker
    assert "pull --ff-only" in broker
    assert "push --set-upstream" in broker
    assert "git push --force" not in broker
    assert "git push -f" not in broker
    assert 'git -C \\"$repo\\" reset --hard HEAD' in broker
    assert "git clean" not in broker
    assert "safeCommitMessage" in broker
    assert "safeRefName" in broker
    assert "safeStashReference" in broker
    assert "git check-ref-format --branch" in broker
    assert "Type DISCARD" in detail
    assert '"DISCARD".contentEquals' in detail
    assert "Untracked files will not be deleted" in detail
    assert "Only a fast-forward update is allowed" in detail
    assert "Force push is never used" in detail


@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
@pytest.mark.slow_external
def test_phase4_branch_and_stash_parsers_consume_git_nul_contract(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable")

    # The parser contract is about the NUL-delimited bytes emitted by Git, not
    # Git repository setup. Representative records avoid ten real Git processes
    # per test and remain portable on native Termux. Real Git integration is
    # covered by the repository/transaction suites.
    branches = b"\0".join([
        b"refs/heads/main", b"main", b"a" * 40, b"origin/main",
        b"ahead 1, behind 2", b"*", b"local",
        b"refs/remotes/origin/feature/mobile-logs", b"origin/feature/mobile-logs",
        b"b" * 40, b"", b"", b"", b"remote",
    ]) + b"\0"
    stashes = b"\0".join([
        b"stash@{0}", b"c" * 40, b"2026-09-11T18:00:00+00:00",
        b"On main: mobile stash",
    ]) + b"\0"
    (tmp_path / "branches.bin").write_bytes(branches)
    (tmp_path / "stashes.bin").write_bytes(stashes)

    package = tmp_path / "classes-src" / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(JAVA / "GitBranchRecord.java", package / "GitBranchRecord.java")
    shutil.copy2(JAVA / "GitStashRecord.java", package / "GitStashRecord.java")
    (package / "Phase4ParserHarness.java").write_text(textwrap.dedent("""
        package io.github.mikeceead.devtoolhud;
        import java.nio.file.Files;
        import java.nio.file.Path;
        import java.util.List;
        public final class Phase4ParserHarness {
            public static void main(String[] args) throws Exception {
                List<GitBranchRecord> branches = GitBranchRecord.parse(Files.readAllBytes(Path.of(args[0])));
                boolean current = false;
                boolean remote = false;
                for (GitBranchRecord branch : branches) {
                    if (branch.current && branch.name.equals("main") && branch.upstream.equals("origin/main")
                            && branch.ahead == 1 && branch.behind == 2) current = true;
                    if (branch.remote && branch.name.equals("origin/feature/mobile-logs")) remote = true;
                }
                if (!current || !remote) throw new AssertionError("branch parse failed: " + branches.size());
                List<GitStashRecord> stashes = GitStashRecord.parse(Files.readAllBytes(Path.of(args[1])));
                if (stashes.size() != 1 || !stashes.get(0).reference.equals("stash@{0}")
                        || !stashes.get(0).subject.contains("mobile stash")) {
                    throw new AssertionError("stash parse failed");
                }
            }
        }
    """), encoding="utf-8")
    classes = tmp_path / "classes"
    subprocess.run([javac, "-d", str(classes), *map(str, package.glob("*.java"))],
                   check=True, capture_output=True, text=True)
    subprocess.run([java, "-cp", str(classes), "io.github.mikeceead.devtoolhud.Phase4ParserHarness",
                    str(tmp_path / "branches.bin"), str(tmp_path / "stashes.bin")],
                   check=True, capture_output=True, text=True)


def test_phase4_version_contract() -> None:
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "versionCode 31" in build
    assert "versionName '1.17.0'" in build
