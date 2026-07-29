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


def test_phase5_persists_and_recovers_repository_operations() -> None:
    database = read("DevtoolDatabase.java")
    workspace = read("RepositoryWorkspaceStore.java")
    assert "private static final int VERSION = 10" in database
    assert "CREATE TABLE repository_operations" in database
    for method in (
        "upsertRepositoryOperation", "repositoryOperationForRun", "repositoryOperations",
        "activeRepositoryOperations", "recoverInterruptedRepositoryOperations",
    ):
        assert method in database
    assert 'db.update("repository_operations"' in database
    assert "ArrayDeque<QueuedOperation>" in workspace
    assert "ACTIVE" in workspace and "QUEUES" in workspace
    assert "CancellationToken" in workspace
    assert "operationProcessAlive" in workspace and "terminateOperationProcess" in workspace
    assert "GIT_" in workspace and "_INTERRUPTED" in workspace and "_ORPHANED" in workspace


def test_phase5_git_actions_are_synthetic_structured_runs() -> None:
    workspace = read("RepositoryWorkspaceStore.java")
    notification = read("RepositoryNotificationController.java")
    viewer = read("LogViewerActivity.java")
    assert 'record.runId = "git-run-"' in workspace
    assert 'run.projectType = "git"' in workspace
    assert 'addLogEntry(run.runId' in workspace
    assert '"git",' in workspace
    assert "RepositoryNotificationController" in workspace
    assert "repositoryOperationForRun" in viewer
    assert "Show all logs for this repository" in viewer
    assert "EXTRA_TAB" in notification


def test_phase5_sync_preview_and_passive_remote_diagnostics() -> None:
    broker = read("RootCommandBroker.java")
    detail = read("RepositoryDetailActivity.java")
    for token in (
        "rev-list --left-right --count HEAD...@{upstream}",
        '"@{upstream}..HEAD"',
        '"HEAD..@{upstream}"',
        "credential.helper",
        "SSH_AUTH_SOCK",
        "known_hosts",
    ):
        assert token in broker
    assert "ssh -T" not in broker
    assert "ls-remote" not in broker
    assert "DEVTOOL_ANDROID_OPERATION_ID" in broker
    assert "/proc/[0-9]*/environ" in broker
    assert "This is a passive local check" in detail
    assert "Incoming and outgoing commits" in detail
    assert "Credential and SSH diagnostics" in detail
    assert "Cancel running operation" in detail
    assert "Remove from queue" in detail


def test_phase5_repository_log_filter_is_saved_and_navigable() -> None:
    main = read("MainActivity.java")
    settings = read("AppSettings.java")
    detail = read("RepositoryDetailActivity.java")
    assert '"logs_repository_id"' in main
    assert "historyRepositoryId" in main
    assert "AppSettings.setLogView" in main
    assert "KEY_LOG_REPOSITORY" in settings
    assert "resetLogView" in settings
    assert "View this repository in Logs" in detail
    assert 'String[] labels = {"Overview", "Changes", "Commits", "Branches", "Sync", "Activity", "Runs", "Metadata"}' in detail


def test_git_sync_commit_parser_handles_nul_records(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable")
    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(JAVA / "GitSyncCommit.java", package / "GitSyncCommit.java")
    (package / "Phase5ParserHarness.java").write_text(textwrap.dedent(r'''
        package io.github.mikeceead.devtoolhud;
        import java.nio.charset.StandardCharsets;
        import java.util.List;
        public final class Phase5ParserHarness {
            public static void main(String[] args) {
                String z = "\u0000";
                String payload = "abcdef" + z + "abcdef" + z + "Alice" + z
                    + "2026-07-20T00:00:00Z" + z + "Subject with spaces" + z
                    + "123456" + z + "123456" + z + "Bob" + z
                    + "2026-07-20T01:00:00Z" + z + "" + z;
                List<GitSyncCommit> commits = GitSyncCommit.parse(payload.getBytes(StandardCharsets.UTF_8));
                if (commits.size() != 2) throw new AssertionError("count");
                if (!commits.get(0).subject.equals("Subject with spaces")) throw new AssertionError("subject");
                if (!commits.get(1).subject.isEmpty()) throw new AssertionError("empty subject");
            }
        }
    '''), encoding="utf-8")
    subprocess.run([javac, "-Xlint:all", "-Werror", "-d", str(tmp_path), *map(str, package.glob("*.java"))],
                   check=True, capture_output=True, text=True)
    subprocess.run([java, "-cp", str(tmp_path), "io.github.mikeceead.devtoolhud.Phase5ParserHarness"],
                   check=True, capture_output=True, text=True)


def test_phase5_version_contract() -> None:
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "versionCode 31" in build
    assert "versionName '1.17.0'" in build
