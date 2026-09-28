from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

from tests._compat import host_is_termux, shared_tool_cache_env

HOST_IS_TERMUX = host_is_termux(dict(os.environ))


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("devtool-test-runtime")
    group.addoption(
        "--devtool-slow-threshold",
        action="store",
        type=float,
        default=float(os.environ.get("DEVTOOL_PYTEST_SLOW_THRESHOLD", "2.0") or "2.0"),
        help="Report Devtool tests whose call phase exceeds this many seconds (default: 2.0).",
    )
    group.addoption(
        "--devtool-strict-slow",
        action="store_true",
        default=False,
        help="Fail the test session when an unmarked test exceeds --devtool-slow-threshold.",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Marker/warning policy is statically declared in pyproject.toml so
    # collection fails closed before test modules can mutate policy.
    config._devtool_slow_calls = []  # type: ignore[attr-defined]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers(name="requires_tool"):
            if not marker.args:
                continue
            tool = str(marker.args[0])
            if shutil.which(tool) is None:
                item.add_marker(pytest.mark.skip(reason=f"required external tool is unavailable: {tool}"))
        if item.get_closest_marker("requires_posix") is not None and os.name != "posix":
            item.add_marker(pytest.mark.skip(reason="requires POSIX process semantics"))
        marker = item.get_closest_marker("not_termux")
        if marker is not None and HOST_IS_TERMUX:
            reason = str(marker.args[0]) if marker.args else "host integration is unavailable on native Termux"
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item._devtool_call_report = report  # type: ignore[attr-defined]
    if report.when != "call":
        return
    threshold = float(item.config.getoption("--devtool-slow-threshold"))
    duration = float(getattr(report, "duration", 0.0) or 0.0)
    if duration < threshold:
        return
    if item.config.getoption("--devtool-strict-slow") and item.get_closest_marker("slow_external") is None and report.passed:
        report.outcome = "failed"
        report.longrepr = (
            str(item.fspath),
            0,
            f"Devtool slow-test budget exceeded: {duration:.2f}s >= {threshold:.2f}s; "
            "optimize the test or mark an intentional external-tool integration with @pytest.mark.slow_external",
        )


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    threshold = float(config.getoption("--devtool-slow-threshold"))
    reports = [report for report in terminalreporter.stats.get("passed", []) if getattr(report, "when", "") == "call"]
    reports += [report for report in terminalreporter.stats.get("failed", []) if getattr(report, "when", "") == "call"]
    slow = sorted(
        (report for report in reports if float(getattr(report, "duration", 0.0) or 0.0) >= threshold),
        key=lambda report: float(getattr(report, "duration", 0.0) or 0.0),
        reverse=True,
    )
    if slow:
        terminalreporter.section(f"Devtool slow tests (>= {threshold:g}s)")
        for report in slow[:30]:
            terminalreporter.write_line(f"{float(report.duration):7.2f}s  {report.nodeid}")
        if len(slow) > 30:
            terminalreporter.write_line(f"... and {len(slow) - 30} more")


@pytest.fixture(scope="session")
def devtool_test_tool_cache_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("devtool-tool-cache")
    shared_tool_cache_env(root)
    return root


@pytest.fixture
def shared_tool_cache(monkeypatch: pytest.MonkeyPatch, devtool_test_tool_cache_root: Path) -> dict[str, str]:
    env = shared_tool_cache_env(devtool_test_tool_cache_root)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env



HUD_TEST_FILES = {
    "test_android_live_hud.py",
    "test_hud.py",
    "test_qt_control_center.py",
    "test_tasker.py",
}


@pytest.fixture(autouse=True)
def devtool_process_state_guard(request: pytest.FixtureRequest):
    """Attribute process-global leaks to the test that created them, then restore."""

    from devtool.process_state import ProcessStateGuard, format_process_state_differences

    guard = ProcessStateGuard.capture()
    yield guard
    differences = guard.verify_and_restore()
    if differences:
        details = format_process_state_differences(differences)
        call_report = getattr(request.node, "_devtool_call_report", None)
        if call_report is not None and bool(getattr(call_report, "failed", False)):
            # Preserve the original product failure as the primary outcome while
            # still attributing/restoring any process-state drift caused by it.
            request.node.add_report_section(
                "teardown",
                "Devtool process-state leak (restored)",
                f"{request.node.nodeid} leaked process state:\n{details}",
            )
        else:
            pytest.fail(
                f"{request.node.nodeid} leaked process state:\n" + details,
                pytrace=False,
            )


@dataclass(frozen=True)
class DevtoolCliInvocation:
    returncode: int
    stdout: str
    stderr: str


@pytest.fixture
def devtool_cli(capsys: pytest.CaptureFixture[str]) -> Callable[[Sequence[str]], DevtoolCliInvocation]:
    """Canonical isolated harness for repeated in-process Devtool CLI calls."""

    from devtool.cli import main
    from devtool.process_state import ProcessStateGuard, format_process_state_differences

    def invoke(argv: Sequence[str]) -> DevtoolCliInvocation:
        guard = ProcessStateGuard.capture()
        raised: BaseException | None = None
        returncode = 1
        captured = None
        try:
            returncode = int(main([str(item) for item in argv]))
            captured = capsys.readouterr()
        except BaseException as exc:  # preserve attribution before re-raising
            raised = exc
        finally:
            differences = guard.verify_and_restore()
        if differences:
            pytest.fail(
                "in-process Devtool CLI invocation leaked process state:\n"
                + format_process_state_differences(differences),
                pytrace=False,
            )
        if raised is not None:
            raise raised
        assert captured is not None
        return DevtoolCliInvocation(returncode, captured.out, captured.err)

    return invoke


@pytest.fixture(autouse=True)
def isolate_devtool_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    devtool_process_state_guard,
) -> None:
    """Keep ordinary tests from reusing a developer's live HUD/log state."""

    # Tests must never inherit the developer's live Termux/chroot session,
    # global config, clipboard bridge, or active HUD run identity. Individual
    # environment-specific tests opt back in explicitly with patch.dict.
    for name in (
        "TERMUX_VERSION",
        "ANDROID_ROOT",
        "ANDROID_DATA",
        "DEVTOOL_EXECUTION_CONTEXT",
        "DEVTOOL_INSIDE_TERMUX_CHROOT",
        "DEVTOOL_CHROOT_CONTEXT",
        "DEVTOOL_AUTO_CHROOT_ACTIVE",
        "DEVTOOL_RUN_ID",
        # Pytest can itself be launched by Devtool while an artifact transaction
        # is active.  Those parent-process roots are valid for the outer apply,
        # but nested tests must not inherit them or tmp-repo validation/session
        # state is redirected into the live repository.
        "DEVTOOL_TRANSACTION_REPO_ROOT",
        "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT",
        "DEVTOOL_VALIDATION_STATE_REPO_ROOT",
        "DEVTOOL_TRANSACTION_COMMIT_MODE",
        "DEVTOOL_TRANSACTION_ISOLATION",
        "DEVTOOL_PRESERVE_DIRTY_REQUESTED",
        "DEVTOOL_ANDROID_HUD_STATE_DIR",
        "DEVTOOL_HUD_BRIDGE_DIR",
        "DEVTOOL_TASKER_DIR",
        "DEVTOOL_CLIPBOARD_SOCKET",
        "DEVTOOL_CLIPBOARD_FIFO",
        "DEVTOOL_PHONE_LOG_MIRROR_DIR",
        "DEVTOOL_GLOBAL_CONFIG",
        "DEVTOOL_LOG_DIR",
        "DEVTOOL_XDG_DIR",
        "DEVTOOL_TERMUX_CHROOT",
        "DEVTOOL_TERMUX_HOME",
        "DEVTOOL_BUILD_ENVIRONMENT",
        "DEVTOOL_BUILD_CACHE_ROOT",
        "DEVTOOL_UI_PROFILE",
        "DEVTOOL_UI_THEME",
        "DEVTOOL_UI_DENSITY",
        "DEVTOOL_UI_ICONS",
        "DEVTOOL_UI_MOTION",
        "DEVTOOL_UI_VERBOSITY",
        "DEVTOOL_MAX_WORKERS",
        "DEVTOOL_VALIDATION_MAX_WORKERS",
        "DEVTOOL_VALIDATION_CPU_LIMIT",
        "DEVTOOL_VALIDATION_MEMORY_GUARD_MB",
        "DEVTOOL_VALIDATION_RESOURCE_PROFILE",
        "DEVTOOL_ANDROID_SAFE_VALIDATION",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)

    for name in tuple(os.environ):
        if name.startswith(("DEVTOOL_PROJECT_", "DEVTOOL_ENV_")):
            monkeypatch.delenv(name, raising=False)

    from devtool import logstore

    # Several regression tests intentionally invoke Devtool commands or lower-level
    # runners.  The real validation process may itself run under Termux with
    # XDG_STATE_HOME and TZ inherited from a Python runner.  Without isolated XDG
    # roots those nested test invocations can create user-visible logs in
    # ~/.local/state/devtool/logs or the phone mirror, often stamped in UTC.
    # Keep all test-created default logs inside pytest's tmp tree and reset the
    # runtime override before/after each test so one test cannot leak its chosen
    # log root into the next.
    logstore.set_log_dir_override(None)
    request.addfinalizer(lambda: logstore.set_log_dir_override(None))

    test_home = tmp_path / "home"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("PREFIX", "/usr")

    path = Path(str(request.node.fspath))
    if path.name not in HUD_TEST_FILES:
        monkeypatch.setenv("DEVTOOL_NO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_NO_AUTO_HUD", "1")
        monkeypatch.setenv("DEVTOOL_HUD_SINGLETON", "off")
