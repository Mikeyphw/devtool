"""ACTIONS-05.3: regression for real GitHub run 38170413590 / javac basename(String)."""
from pathlib import Path
import re
import shutil
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud/RootLogIndexer.java"


def _helper(source: str) -> str:
    # Exact method is used by both the production source and the JVM smoke test.
    match = re.search(r"(?m)^    private static String basename\(String path\) \{", source)
    assert match, "RootLogIndexer missing basename(String) for three Java call sites"
    opening = source.index("{", match.start())
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():pos + 1]
    raise AssertionError("Unterminated basename(String) method")


def test_log_indexer_restores_basename_for_all_three_live_calls():
    source = JAVA.read_text(encoding="utf-8")
    helper = _helper(source)
    assert source.count("basename(") == 4  # 1 declaration + 3 observed Java usages
    assert "run.title = first(event.optString" in source
    assert "basename(run.logPath)" in source
    assert "run.title = basename(file.path)" in source
    assert "repository.displayName = basename(location.path)" in source
    assert r"replace('\\', '/')" in helper
    assert "lastIndexOf('/')" in helper
    # V4 removed the phone-log mirror. Do not accidentally reintroduce it.
    for removed in ("phone-log-mirror", "phone-mirror", "DEVTOOL_PHONE_LOG_MIRROR_DIR"):
        assert removed not in source


def test_log_indexer_basename_jvm_semantics(tmp_path: Path):
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK unavailable; source contract still verified")
    helper = _helper(JAVA.read_text(encoding="utf-8"))
    java_source = textwrap.dedent(r'''
        public final class BasenameContract {
            private static String clean(String value) {
                return value == null ? "" : value.trim();
            }
            public static void main(String[] args) {
                check("index.log", basename("/home/termux/index.log"));
                check("repository", basename("/home/termux/repository/"));
                check("snapshot", basename("C:\\temp\\snapshot"));
                check("events.jsonl", basename("events.jsonl"));
                check("", basename(null));
                check("", basename("/"));
            }
            private static void check(String expected, String got) {
                if (!expected.equals(got)) throw new AssertionError(expected + " != " + got);
            }
        }
    ''').replace("    private static void check", helper + "\n    private static void check")
    filename = tmp_path / "BasenameContract.java"
    filename.write_text(java_source, encoding="utf-8")
    subprocess.run([javac, str(filename)], check=True, capture_output=True, text=True, timeout=30)
    subprocess.run([java, "-cp", str(tmp_path), "BasenameContract"], check=True,
                   capture_output=True, text=True, timeout=30)
