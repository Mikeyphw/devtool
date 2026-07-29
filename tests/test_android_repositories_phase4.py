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


def test_phase4_branch_and_stash_parsers_consume_real_git_nul_output(tmp_path: Path) -> None:
    git = shutil.which("git")
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not git or not javac or not java:
        pytest.skip("Git or JDK unavailable")

    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Phase Four"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "phase4@example.invalid"], check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run([git, "-C", str(repo), "branch", "feature/mobile logs".replace(" ", "-")], check=True)
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run([git, "-C", str(repo), "push", "-u", "origin", "main"], check=True, capture_output=True)
    subprocess.run([git, "-C", str(repo), "push", "origin", "feature/mobile-logs"], check=True, capture_output=True)
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    (repo / "untracked file.txt").write_text("loose\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "stash", "push", "--include-untracked", "-m", "mobile stash"],
                   check=True, capture_output=True)

    fmt = "%(refname)%00%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track,nobracket)%00%(HEAD)%00"
    branches = subprocess.check_output([git, "-C", str(repo), "for-each-ref", "--sort=refname",
                                        f"--format={fmt}local%00", "refs/heads"])
    branches += subprocess.check_output([git, "-C", str(repo), "for-each-ref", "--sort=refname",
                                         f"--format={fmt}remote%00", "refs/remotes"])
    stashes = subprocess.check_output([git, "-C", str(repo), "stash", "list",
                                       "--format=%gd%x00%H%x00%aI%x00%gs%x00"])
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
                    if (branch.current && branch.name.equals("main") && branch.upstream.equals("origin/main")) current = true;
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
