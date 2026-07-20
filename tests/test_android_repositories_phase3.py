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


def test_phase3_exposes_changes_commits_diffs_and_guarded_mutations() -> None:
    detail = read("RepositoryDetailActivity.java")
    broker = read("RootCommandBroker.java")
    workspace = read("RepositoryWorkspaceStore.java")
    manifest = (APP / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'String[] labels = {"Overview", "Changes", "Commits", "Branches", "Sync", "Activity", "Runs", "Metadata"}' in detail
    for action in ("Stage all", "Unstage all", "View working diff", "View staged diff",
                   "Discard working-tree changes", "Commit details"):
        assert action in detail
    assert "Any staged version is preserved" in detail
    assert 'git -C \\"$repo\\" status --porcelain=v2 -z' in broker
    assert 'git -C \\"$repo\\" add --' in broker
    assert "restore --staged --" in broker
    assert "restore --worktree --" in broker
    assert 'git -C \\"$repo\\" log -n' in broker
    assert "safeRelativePath" in broker and "pathArguments" in broker
    assert "ACTIVE" in workspace and "QUEUES" in workspace and "GIT_" in workspace
    assert 'android:name=".GitDiffActivity"' in manifest
    assert 'android:name=".GitCommitActivity"' in manifest


def test_phase3_mutations_remain_path_safe_after_phase4_expansion() -> None:
    broker = read("RootCommandBroker.java")
    assert "safeRelativePath" in broker and "pathArguments" in broker
    assert 'git -C \\\"$repo\\\" add --' in broker
    assert "restore --staged --" in broker
    assert "restore --worktree --" in broker
    assert "git push --force" not in broker and "git push -f" not in broker


def test_commit_correlation_uses_repository_and_prefix_safe_hash_matching() -> None:
    database = read("DevtoolDatabase.java")
    detail = read("RepositoryDetailActivity.java")
    commit = read("GitCommitActivity.java")
    assert "runsForCommit" in database
    assert "actual.startsWith(wanted) || wanted.startsWith(actual)" in database
    assert "commit_hash<>'' AND (repository_id=? OR repo=? OR workdir=?)" in database
    assert "runsForCommit(repository.repositoryId, commit.fullHash)" in detail
    assert "runsForCommit(repository.repositoryId, hash)" in commit
    assert "Copy artifact path" in commit


def test_porcelain_v2_parser_handles_spaces_renames_conflicts_and_empty_subjects(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable")
    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    for name in ("GitWorkingTree.java", "GitCommitRecord.java"):
        shutil.copy2(JAVA / name, package / name)
    (package / "GitParserHarness.java").write_text(textwrap.dedent(r'''
        package io.github.mikeceead.devtoolhud;
        import java.nio.charset.StandardCharsets;
        import java.util.List;
        public final class GitParserHarness {
            public static void main(String[] args) {
                String z = "\u0000";
                String status = "# branch.oid abcdef" + z
                    + "# branch.head main" + z
                    + "# branch.upstream origin/main" + z
                    + "# branch.ab +2 -1" + z
                    + "1 M. N... 100644 100644 100644 a b file with space.txt" + z
                    + "2 R. N... 100644 100644 100644 a b R100 new name.txt" + z
                    + "old name.txt" + z
                    + "u UU N... 100644 100644 100644 100644 a b c conflict.txt" + z
                    + "? untracked file.txt" + z;
                GitWorkingTree tree = GitWorkingTree.parse(status.getBytes(StandardCharsets.UTF_8), 0, false);
                if (!"main".equals(tree.branch) || tree.ahead != 2 || tree.behind != 1) throw new AssertionError();
                if (tree.changes.size() != 4 || tree.conflictedCount() != 1) throw new AssertionError();
                boolean rename = false;
                for (GitWorkingTree.GitChange change : tree.changes) {
                    if ("old name.txt".equals(change.originalPath) && "new name.txt".equals(change.path)) rename = true;
                }
                if (!rename) throw new AssertionError("rename source lost");
                String commits = "abcdef" + z + "abcdef" + z + "abcdef1" + z + "parent" + z
                    + "Alice" + z + "alice@example.com" + z + "2026-07-20T00:00:00Z" + z
                    + "HEAD -> main" + z + "" + z;
                List<GitCommitRecord> parsed = GitCommitRecord.parse(commits.getBytes(StandardCharsets.UTF_8));
                if (parsed.size() != 1 || !parsed.get(0).subject.isEmpty() || !parsed.get(0).head) throw new AssertionError();
            }
        }
    '''), encoding="utf-8")
    subprocess.run([javac, "-d", str(tmp_path), *map(str, package.glob("*.java"))],
                   check=True, capture_output=True, text=True)
    subprocess.run([java, "-cp", str(tmp_path),
                    "io.github.mikeceead.devtoolhud.GitParserHarness"],
                   check=True, capture_output=True, text=True)


def test_phase3_version_contract() -> None:
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "versionCode 27" in build
    assert "versionName '1.14.0'" in build
