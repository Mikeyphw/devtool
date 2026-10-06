from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.textual_apply_host import (
    ArtifactApplyHostSource,
    apply_phase_text,
    child_argv,
    select_apply_textual_host,
)


class TTY:
    def isatty(self) -> bool:
        return True


class NotTTY:
    def isatty(self) -> bool:
        return False


def args(**overrides):
    values = dict(
        command="apply-overlay",
        normalized_command="",
        quiet=False,
        verbose=False,
        trace=False,
        stdout_payload="auto",
        json=False,
        _devtool_json=False,
        yes=True,
        force=False,
        artifact="demo.zip",
        target="devtool",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_apply_host_selects_only_human_interactive_artifact_apply() -> None:
    chosen = select_apply_textual_host(
        args(), env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    )
    assert chosen.enabled is True

    nested = select_apply_textual_host(
        args(command="apply", normalized_command="apply-overlay"),
        env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True,
    )
    assert nested.enabled is True

    assert select_apply_textual_host(
        args(command="invoke"), env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    ).enabled is False
    assert select_apply_textual_host(
        args(), universal_json=True, env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    ).enabled is False
    assert select_apply_textual_host(
        args(), env={}, stdin=TTY(), stdout=NotTTY(), textual_probe=lambda: True
    ).enabled is False
    assert select_apply_textual_host(
        args(verbose=True), env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    ).enabled is False
    assert select_apply_textual_host(
        args(), env={"DEVTOOL_TEXTUAL_APPLY_CHILD": "1"}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    ).enabled is False
    assert select_apply_textual_host(
        args(), env={"DEVTOOL_EXECUTION_PRESENTATION": "plain"}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    ).enabled is False



def test_nested_apply_overlay_parser_routes_to_textual_host() -> None:
    from devtool.cli import build_parser

    parsed = build_parser().parse_args([
        "--yes", "--copy", "-r", "/repo", "--target", "devtool",
        "apply", "overlay", "demo.zip",
    ])
    assert parsed.command == "apply"
    assert parsed.normalized_command == "apply-overlay"
    chosen = select_apply_textual_host(
        parsed, env={}, stdin=TTY(), stdout=TTY(), textual_probe=lambda: True
    )
    assert chosen.enabled is True


def test_confirmed_parent_forces_yes_only_in_managed_child() -> None:
    assert child_argv(["-r", "/repo", "apply-overlay", "a.zip"], force_yes=True) == [
        "--yes", "-r", "/repo", "apply-overlay", "a.zip"
    ]
    assert child_argv(["--yes", "-r", "/repo", "apply-overlay", "a.zip"], force_yes=True) == [
        "--yes", "-r", "/repo", "apply-overlay", "a.zip"
    ]
    assert child_argv(["-r", "/repo", "apply-overlay", "a.zip"], force_yes=False) == [
        "-r", "/repo", "apply-overlay", "a.zip"
    ]


def test_source_managed_child_preserves_canonical_devtool_authority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeStdout:
        def __iter__(self):
            yield "qualification ok\n"
            yield "validation running\n"

        def close(self):
            return None

    class FakeProc:
        pid = 4321
        stdout = FakeStdout()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            calls["signal"] = sig

        def terminate(self):
            calls["terminate"] = True

    def fake_popen(argv, **kwargs):
        calls["argv"] = list(argv)
        calls["kwargs"] = kwargs
        return FakeProc()

    source = ArtifactApplyHostSource(
        ["-r", str(tmp_path), "apply-overlay", "demo.zip"],
        args(artifact="demo.zip"),
        env={"HOME": str(tmp_path)},
        cwd=tmp_path,
        popen_factory=fake_popen,
        live_state_dir=tmp_path / "live",
    )
    source.start(force_yes=True)
    reader = source._reader
    if reader is not None:
        reader.join(timeout=1)

    argv = calls["argv"]
    assert argv[:3] == [__import__("sys").executable, "-m", "devtool"]
    assert "--yes" in argv
    env = calls["kwargs"]["env"]
    assert env["DEVTOOL_TEXTUAL_APPLY_CHILD"] == "1"
    assert env["DEVTOOL_AUTO_HUD_CHILD"] == "1"
    assert env["DEVTOOL_EXECUTION_PRESENTATION"] == "plain"
    assert env["DEVTOOL_RUN_ID"] == source.run_id
    assert calls["kwargs"]["stdin"] == __import__("subprocess").DEVNULL
    assert calls["kwargs"]["start_new_session"] is True
    assert source.drain_lines() == ["qualification ok", "validation running"]


def test_source_reads_existing_tasker_run_truth_without_own_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = ArtifactApplyHostSource([], args(), env={}, cwd=tmp_path, live_state_dir=tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    payload = {
        "run_id": source.run_id,
        "status": "running",
        "message": "Artifact validation",
        "progress": {"percent": 42.0, "detail": "pytest"},
        "phase": {"name": "artifact validation"},
    }
    source.run_state_file.write_text(json.dumps(payload), encoding="utf-8")
    assert source.read_state() == payload
    # The host only reads the canonical child-owned run file; it creates no
    # alternate TUI state database or sibling state file.
    assert not (tmp_path / "tui-state.json").exists()
    assert not (tmp_path / "state.json").exists()


def test_compact_terminal_state_does_not_erase_last_meaningful_phase() -> None:
    assert apply_phase_text({
        "phase": {"name": "artifact validation"},
        "progress": {"percent": 50.0, "detail": "tests"},
    }) == "artifact validation · tests"
    assert apply_phase_text({"status": "ok", "message": "done", "progress": {"percent": 100.0}}) is None


def test_apply_host_uses_runtime_responsive_class_not_unsupported_media_query() -> None:
    source = Path(__file__).parents[1] / "devtool" / "textual_apply_host.py"
    text = source.read_text(encoding="utf-8")
    assert "@media" not in text
    assert '.narrow #apply-review' in text
    assert 'screen.add_class("narrow")' in text
    assert 'screen.remove_class("narrow")' in text


def test_real_apply_host_app_runs_on_textual_main_thread_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    required = __import__("os").environ.get("DEVTOOL_PRES_H02_REQUIRE_TEXTUAL") == "1"
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError:
        if required:
            pytest.fail("PRES-H02 native qualification requires Textual")
        pytest.skip("Textual unavailable on construction host")

    from devtool.textual_apply_host import build_apply_textual_app

    class FakeSource:
        max_log_lines = 50
        review = {
            "artifact": "demo",
            "target": "devtool",
            "changes": "2 files · modify",
            "validation": "required",
            "lifecycle": "validator",
            "apply": "atomic · fail · commit",
        }
        running = False
        returncode = None
        cancel_requested = False

        def __init__(self):
            self.started = False
            self._polls = 0

        def start(self, *, force_yes: bool):
            assert force_yes is True
            self.started = True

        def drain_lines(self, *, limit=256):
            if self._polls == 0:
                return ["canonical child output"]
            return []

        def read_state(self):
            self._polls += 1
            if self._polls == 1:
                return {
                    "status": "running",
                    "message": "validating",
                    "phase": {"name": "artifact validation"},
                    "progress": {"percent": 50.0, "detail": "tests"},
                }
            self.returncode = 0
            return {"status": "ok", "message": "done", "progress": {"percent": 100.0}}

        def request_cancel(self):
            self.cancel_requested = True

        def terminate(self):
            return None

    async def exercise() -> None:
        source = FakeSource()
        app = build_apply_textual_app(source, confirm_required=False)
        async with app.run_test(size=(64, 22)) as pilot:
            await pilot.pause(0.25)
            assert source.started is True
            assert "narrow" in app.screen.classes
            assert "demo" in str(app.query_one("#apply-review").render())
            assert "artifact validation" in str(app.query_one("#apply-phase").render()).lower()
            # A real Textual App is active in the test runner; no helper-thread
            # terminal driver is involved.
            assert app.screen.__class__.__name__ == "Screen"
            await pilot.resize_terminal(100, 30)
            await pilot.pause(0.05)
            assert "narrow" not in app.screen.classes

    import asyncio
    asyncio.run(exercise())
