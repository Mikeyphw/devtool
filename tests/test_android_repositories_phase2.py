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


def test_phase2_has_real_five_destination_repository_surface() -> None:
    main = read("MainActivity.java")
    detail = read("RepositoryDetailActivity.java")
    picker = read("RepositoryPickerActivity.java")
    assert 'private static final String[] PAGES = {"Overview", "Run", "Logs", "Repos", "Settings"}' in main
    assert "buildRepositoriesPage" in main
    assert 'String[] labels = {"Overview", "Changes", "Commits", "Branches", "Sync", "Activity", "Runs", "Metadata"}' in detail
    for action in ("Use in Run", "Open shell here", "Browse files as root", "Copy path"):
        assert action in detail
    for environment in ('"Termux"', '"Chroot"', '"Root"'):
        assert environment in picker
    assert "RepositoryStore.addAndInspectAsync" in picker


def test_repository_storage_is_relational_correlated_and_non_destructive() -> None:
    database = read("DevtoolDatabase.java")
    store = read("RepositoryStore.java")
    run_store = read("RunStore.java")
    for table in ("runs", "repositories", "repository_events", "log_entries", "log_sources", "file_cursors"):
        assert f"CREATE TABLE {table}" in database
    assert "CREATE VIRTUAL TABLE log_entries_fts USING fts4" in database
    assert "rekeyRepository" in database
    assert "db.update(\"repository_events\"" in database
    repository_upsert = database.split("synchronized void upsertRepository", 1)[1].split(
        "synchronized void rekeyRepository", 1
    )[0]
    assert "CONFLICT_REPLACE" not in repository_upsert
    assert "persistInspection" in store
    assert "RepositoryLocation.fromRun" in run_store
    assert "addRepositoryEvent" in run_store


def test_root_broker_is_bounded_and_owner_aware() -> None:
    broker = read("RootCommandBroker.java")
    detail = read("RepositoryDetailActivity.java")
    assert "MAX_OUTPUT" in broker and "TIMEOUT_SECONDS" in broker
    assert 'git -C \\"$repo\\" status --porcelain=v2 --branch --untracked-files=normal' in broker
    assert 'git -C \\"$repo\\" remote get-url origin' in broker
    assert "node_modules" in broker and ".gradle" in broker
    assert "runuser -u" in broker
    assert "runuser -u" in detail
    assert "GIT_TERMINAL_PROMPT=0" in broker
    assert "pull --ff-only" in broker
    assert "git push --force" not in broker and "git push -f" not in broker


def test_logs_searches_structured_entries_and_deep_links_exact_runs() -> None:
    main = read("MainActivity.java")
    database = read("DevtoolDatabase.java")
    viewer = read("LogViewerActivity.java")
    notification = read("NotificationController.java")
    manifest = (APP / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert "matchingRunIds" in database
    assert "runIdsWithSeverity" in database
    assert "structuredMatches.contains(record.runId)" in main
    for mode in ("Timeline", "Problems", "Raw", "Metadata"):
        assert f'"{mode}"' in viewer
    assert "LogViewerActivity.EXTRA_RUN_ID" in notification
    assert 'android:scheme="devtool"' in manifest
    assert 'android:host="logs"' in manifest


@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
@pytest.mark.slow_external
def test_environment_aware_repository_identity_jvm(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable")

    package = tmp_path / "io" / "github" / "mikeceead" / "devtoolhud"
    package.mkdir(parents=True)
    shutil.copy2(JAVA / "RepositoryLocation.java", package / "RepositoryLocation.java")
    annotation = tmp_path / "android" / "annotation" / "SuppressLint.java"
    annotation.parent.mkdir(parents=True)
    annotation.write_text(
        "package android.annotation; public @interface SuppressLint { String[] value(); }\n",
        encoding="utf-8",
    )
    (package / "RunRecord.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            final class RunRecord {
                String executionEnvironment = "";
                String repo = "";
                String workdir = "";
                String command = "";
            }
            """
        ),
        encoding="utf-8",
    )
    (package / "RepositoryIdentityHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;
            public final class RepositoryIdentityHarness {
                public static void main(String[] args) {
                    RepositoryLocation termuxHost = RepositoryLocation.termux(
                        "/data/data/com.termux/files/home/Code/xdm");
                    RepositoryLocation termuxGuest = RepositoryLocation.termux("~/Code/xdm");
                    RepositoryLocation chroot = RepositoryLocation.chroot(
                        "androidos", "mike", "/home/mike/Code/xdm");
                    RepositoryLocation otherChroot = RepositoryLocation.chroot(
                        "ubuntu", "mike", "/home/mike/Code/xdm");
                    if (!termuxHost.id().equals(termuxGuest.id())) throw new AssertionError("Termux canonicalization");
                    if (termuxHost.id().equals(chroot.id())) throw new AssertionError("Environment collision");
                    if (chroot.id().equals(otherChroot.id())) throw new AssertionError("Chroot collision");
                    if (!"mike".equals(RepositoryLocation.inferUser("/home/mike/Code/xdm")))
                        throw new AssertionError("User inference");
                    if (!"~/Code/xdm".equals(termuxHost.path)) throw new AssertionError(termuxHost.path);
                    RunRecord run = new RunRecord();
                    run.executionEnvironment = "chroot";
                    run.repo = "~/";
                    run.workdir = "~/";
                    run.command = "devtool --chroot androidos -r '/home/mike/Code/space repo' status";
                    RepositoryLocation fromRun = RepositoryLocation.fromRun(run);
                    if (!"chroot".equals(fromRun.environment)) throw new AssertionError(fromRun.environment);
                    if (!"androidos".equals(fromRun.chrootName)) throw new AssertionError(fromRun.chrootName);
                    if (!"mike".equals(fromRun.linuxUser)) throw new AssertionError(fromRun.linuxUser);
                    if (!"/home/mike/Code/space repo".equals(fromRun.path)) throw new AssertionError(fromRun.path);
                    RunRecord rootRun = new RunRecord();
                    rootRun.executionEnvironment = "android-device";
                    rootRun.command = "devtool -r /data/local/tmp/project status";
                    RepositoryLocation rooted = RepositoryLocation.fromRun(rootRun);
                    if (!"root".equals(rooted.environment)) throw new AssertionError(rooted.environment);
                }
            }
            """
        ),
        encoding="utf-8",
    )
    sources = [str(path) for path in tmp_path.rglob("*.java")]
    subprocess.run([javac, "-d", str(tmp_path), *sources], check=True, capture_output=True, text=True)
    subprocess.run(
        [java, "-cp", str(tmp_path), "io.github.mikeceead.devtoolhud.RepositoryIdentityHarness"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_root_log_indexer_is_incremental_bounded_and_zero_count_safe() -> None:
    indexer = read("RootLogIndexer.java")
    database = read("DevtoolDatabase.java")
    main = read("MainActivity.java")
    run = read("RunRecord.java")
    assert "Root access is unavailable" in indexer
    assert "fileCursor(source.id, file.path)" in indexer
    assert "file.size < cursor.byteOffset" in indexer
    assert "cursor.partialLine" in indexer
    assert "CHUNK_BYTES = 512 * 1024" in indexer
    assert "MAX_CHUNKS_PER_FILE" in indexer
    assert "dd if=" in indexer and "skip=" in indexer and "count=" in indexer
    assert '"termux-xdg"' in indexer
    assert '"termux-tasker"' in indexer
    assert '"phone-mirror"' in indexer
    assert '"chroot-" + safeId(name)' in indexer
    assert 'clean.matches(".*\\\\b0\\\\s+(errors?|failed)\\\\b.*")' in indexer
    assert "upsertFileCursor" in database
    assert "addIndexedLogEntry" in database
    assert 'String logPath = ""' in run
    assert "RootLogIndexer.refreshIfStale" in main
    assert '"Refresh root logs"' in main
