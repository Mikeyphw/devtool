from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from devtool.desktop.preferences import DesktopPreferences, preferences_path
from devtool.desktop.repository_store import DesktopRepositoryStore
from devtool.desktop.gtk_control_center import _write_desktop_run_hud_failure
from devtool.desktop.run_process import (
    DesktopRunLauncher,
    MAX_CHANNEL_LINES,
    MAX_DIAGNOSTICS,
    MAX_LOG_LINE_CHARS,
    MAX_OUTPUT_LINES,
)

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"
PACKAGE = ROOT / "desktop/gtk-control-center/package-deb.sh"


def java(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_ov13_android_logcat_is_release_gated_and_redacted() -> None:
    privacy_log = java("AndroidPrivacyLog.java")
    assert "final class AndroidPrivacyLog" in privacy_log
    assert "BuildConfig.DEBUG" in privacy_log
    assert "SupportRedactor.redact" in privacy_log
    assert "Log.e(cleanTag(tag), clean(message) + throwableSummary(error))" in privacy_log
    assert "Log.e(cleanTag(tag), clean(message), error)" not in privacy_log

    for path in JAVA.glob("*.java"):
        if path.name == "AndroidPrivacyLog.java":
            continue
        text = path.read_text(encoding="utf-8")
        assert "import android.util.Log;" not in text, path.name
        assert " Log." not in text, path.name
        assert "	Log." not in text, path.name

    for name in ("BridgeReceiver.java", "TermuxCommandRunner.java", "TermuxResultService.java", "DebugRecorder.java", "TaskerProtocol.java"):
        assert "AndroidPrivacyLog." in java(name)


def test_ov13_diagnostic_activity_rebuilds_restore_state_after_lifecycle_recreation() -> None:
    debug = java("DebugWorkbenchActivity.java")
    for token in (
        'STATE_SCROLL_Y = "debug_workbench_scroll_y"',
        "protected void onSaveInstanceState(Bundle outState)",
        "outState.putInt(STATE_SCROLL_Y",
        "activeScroll = (ScrollView) buildRoot()",
        "restoreScrollPosition()",
        "activeScroll.post(() -> activeScroll.scrollTo(0, scrollY))",
    ):
        assert token in debug

    detail = java("RepositoryDetailActivity.java")
    for token in (
        'STATE_TAB = "repository_detail_tab"',
        'STATE_SCROLL_Y = "repository_detail_scroll_y"',
        "state.getInt(STATE_TAB, tab)",
        "outState.putInt(STATE_TAB, tab)",
        "outState.putInt(STATE_SCROLL_Y",
        "activeScroll = scroll",
        "restoreScrollPosition()",
    ):
        assert token in detail


def test_ov14_desktop_preferences_save_atomically_with_private_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    prefs = DesktopPreferences(window_width=1400, window_height=800, selected_page="history", high_contrast=True)
    prefs.set_filter_value("history", "query", "token=abc123")
    path = prefs.save("control-center")

    assert path == preferences_path("control-center")
    assert DesktopPreferences.load("control-center").selected_page == "history"
    assert DesktopPreferences.load("control-center").filter_value("history", "query") == "token=abc123"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))
    assert "NamedTemporaryFile" in (ROOT / "devtool/desktop/preferences.py").read_text(encoding="utf-8")
    assert "os.replace(tmp_name, path)" in (ROOT / "devtool/desktop/preferences.py").read_text(encoding="utf-8")
    prefs_source = (ROOT / "devtool/desktop/preferences.py").read_text(encoding="utf-8")
    assert "os.fsync(handle.fileno())" in prefs_source
    assert "_fsync_directory(path.parent)" in prefs_source


def test_ov14_repository_store_persists_recent_repositories_atomically(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = tmp_path / "state"
    path = DesktopRepositoryStore(repo).remember(state_dir=state_dir)

    assert path == state_dir / "repositories.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["path"] == str(repo.resolve())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))
    store_source = (ROOT / "devtool/desktop/repository_store.py").read_text(encoding="utf-8")
    assert "_write_json_atomic(path, entries[:50])" in store_source
    assert "path.write_text" not in store_source
    assert "_fsync_directory(path.parent)" in store_source


def test_ov14_desktop_run_failure_hud_state_is_written_atomically(tmp_path: Path) -> None:
    handle = SimpleNamespace(
        diagnostics=[{"severity": "error", "message": "token=abc123 failed", "source": "desktop-run"}],
        run_id="desktop-test",
        returncode=7,
        log_path=tmp_path / "run.log",
        current_phase="phase x",
        argv=["devtool", "validate"],
        cwd=tmp_path,
        elapsed_seconds=1.25,
        failure_summary=lambda: "Desktop run failed",
        summary=lambda: "desktop-test · exit 7",
    )
    hud_dir = tmp_path / "hud"
    _write_desktop_run_hud_failure({"hud": {"directory": str(hud_dir)}}, handle)

    state_path = hud_dir / "state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["run_id"] == "desktop-test"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert not list(hud_dir.glob("*.tmp"))
    control_source = (ROOT / "devtool/desktop/gtk_control_center.py").read_text(encoding="utf-8")
    assert '_atomic_write_json(directory / "state.json", state)' in control_source
    assert '(directory / "state.json").write_text' not in control_source


def test_ov14_desktop_run_buffers_are_bounded_and_log_lines_trimmed(tmp_path: Path) -> None:
    launcher = DesktopRunLauncher(state_dir=tmp_path / "runs")
    script = "\n".join([
        "import sys",
        "[print(f'warning {i}') for i in range(260)]",
        "[print(f'error {i}', file=sys.stderr) for i in range(260)]",
        f"print('phase: ' + 'x' * {MAX_LOG_LINE_CHARS + 500})",
    ])
    callback_lines: list[str] = []
    handle = launcher.start([sys.executable, "-S", "-c", script], cwd=tmp_path, on_output=lambda _channel, line: callback_lines.append(line))
    deadline = time.monotonic() + 10
    while handle.returncode is None and time.monotonic() < deadline:
        time.sleep(0.05)

    assert handle.returncode == 0
    assert len(handle.output_lines) <= MAX_OUTPUT_LINES
    assert len(handle.stdout_lines) <= MAX_CHANNEL_LINES
    assert len(handle.stderr_lines) <= MAX_CHANNEL_LINES
    assert len(handle.diagnostics) <= MAX_DIAGNOSTICS
    assert any("<truncated>" in line for line in handle.output_lines)
    assert any("<truncated>" in line for line in handle.log_path.read_text(encoding="utf-8").splitlines())
    assert callback_lines
    assert max(len(line) for line in callback_lines) <= MAX_LOG_LINE_CHARS + len("…<truncated>")


def test_ov14_gtk_package_metadata_rejects_injection_and_path_separators() -> None:
    text = PACKAGE.read_text(encoding="utf-8")
    for token in (
        "valid_deb_version()",
        "valid_deb_arch()",
        "VERSION contains characters unsafe for Debian metadata",
        "ARCH contains characters unsafe for Debian metadata",
        "package metadata must not contain path or newline separators",
        "valid_deb_version \"$version\"",
        "valid_deb_arch \"$arch\"",
    ):
        assert token in text
    assert "deb_path=\"$out/devtool-control-center_${version}_${arch}.deb\"" in text


def test_ov13_ov14_current_source_privacy_and_packaging_contract() -> None:
    # Historical success counts in the retired report cannot certify this source.
    privacy = java("AndroidPrivacyLog.java")
    desktop = (ROOT / "devtool/desktop/preferences.py").read_text(encoding="utf-8")
    packaging = PACKAGE.read_text(encoding="utf-8")
    assert "BuildConfig.DEBUG" in privacy and "SupportRedactor.redact" in privacy
    assert "os.fsync(handle.fileno())" in desktop and "os.replace(tmp_name, path)" in desktop
    assert "valid_deb_arch()" in packaging and "valid_deb_version" in packaging
    for regression in (
        "tests/test_android_debug_phase47c5_c7_lint_cleanup.py",
        "tests/test_desktop_gtk_phase6_7_run_theming.py",
    ):
        path = ROOT / regression
        assert path.is_file() and "def test_" in path.read_text(encoding="utf-8")
