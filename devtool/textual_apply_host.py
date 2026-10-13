"""Main-thread Textual host for interactive artifact application.

PRES-H02 closes the runtime hole exposed by native Termux after PRES-H01:
Textual's Linux driver must own the main interpreter thread, while Devtool's
artifact apply/validation lifecycle may contain blocking work and subprocesses.
The host therefore keeps Textual in the parent/main process and executes the
canonical CLI invocation in a managed child process. The child remains the sole
authority for transactions, validation, rollback, commit, RUN evidence and live
Tasker state; this module is only a presentation/control projection.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from types import SimpleNamespace
import uuid

from .tasker import DEFAULT_TASKER_DIR, resolve_cli_path
from .job_ui import job_ui_mode, run_job_app, styled_job_line, job_icons, readable_job_line
from .invocation_origin import create_managed_run_handoff
from .textual_ux import (
    APPLY_STAGES, ApplyStageTracker, ArtifactActivityTracker, TEXTUAL_SHARED_CSS, artifact_display_progress, artifact_stage,
    artifact_review_text, artifact_running_summary, artifact_stage_compact_line,
    artifact_stage_line, fit_middle, responsive_class_names, status_label,
)

_TRUE = {"1", "true", "yes", "on"}
_APPLY_COMMANDS = {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as"}
_CHILD_ENV = "DEVTOOL_TEXTUAL_APPLY_CHILD"
_DISABLE_ENV = "DEVTOOL_TEXTUAL_APPLY_HOST"


def _effective_apply_command(args: Any) -> str:
    """Resolve parser aliases early enough for presentation routing.

    Nested CLI forms such as ``devtool apply overlay ...`` are parsed first as
    ``command="apply"`` with ``normalized_command="apply-overlay"``.  The
    canonical CLI normalizes that later, but presentation-host selection happens
    before the canonical normalization boundary.  Resolve only the command name
    here so nested and flat spellings choose the same apply host without mutating
    execution arguments or validation intent.
    """

    command = str(getattr(args, "command", "") or "").strip()
    normalized = str(getattr(args, "normalized_command", "") or "").strip()
    if command == "apply" and normalized in _APPLY_COMMANDS:
        return normalized
    return command


@dataclass(frozen=True, slots=True)
class ApplyTextualHostDecision:
    enabled: bool
    reason: str


def _isatty(stream: object) -> bool:
    try:
        return bool(getattr(stream, "isatty")())
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def textual_available() -> bool:
    try:
        return importlib.util.find_spec("textual") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def select_apply_textual_host(
    args: Any,
    *,
    universal_json: bool = False,
    env: Mapping[str, str] | None = None,
    stdin: object | None = None,
    stdout: object | None = None,
    textual_probe: Callable[[], bool] | None = None,
) -> ApplyTextualHostDecision:
    """Decide whether this invocation should be hosted by Textual.

    The host is intentionally human/TTY-only. Machine output, redirected output,
    CI, verbose/trace debugging and an explicit plain presentation request remain
    ordinary CLI execution paths.
    """

    environment = env if env is not None else os.environ
    command = _effective_apply_command(args)
    if command not in _APPLY_COMMANDS:
        return ApplyTextualHostDecision(False, "not an artifact apply command")
    if str(environment.get(_CHILD_ENV, "")).strip().lower() in _TRUE:
        return ApplyTextualHostDecision(False, "managed apply child process")
    host_setting = str(environment.get(_DISABLE_ENV, "auto") or "auto").strip().lower()
    if host_setting in {"0", "false", "no", "off", "plain", "disabled"}:
        return ApplyTextualHostDecision(False, "Textual apply host disabled")
    if universal_json or bool(getattr(args, "_devtool_json", False)) or bool(getattr(args, "json", False)):
        return ApplyTextualHostDecision(False, "machine JSON output requested")
    if bool(getattr(args, "quiet", False)):
        return ApplyTextualHostDecision(False, "quiet output requested")
    if bool(getattr(args, "verbose", False)) or bool(getattr(args, "trace", False)):
        return ApplyTextualHostDecision(False, "verbose/trace output requested")
    if str(getattr(args, "stdout_payload", "auto") or "auto").strip().lower() != "auto":
        return ApplyTextualHostDecision(False, "explicit stdout payload requested")
    if str(environment.get("CI", "")).strip().lower() in _TRUE:
        return ApplyTextualHostDecision(False, "CI environment")
    if str(environment.get("DEVTOOL_EXECUTION_PRESENTATION", "auto") or "auto").strip().lower() == "plain" or job_ui_mode(environment) == "plain":
        return ApplyTextualHostDecision(False, "plain execution presentation requested")
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    if not _isatty(in_stream) or not _isatty(out_stream):
        return ApplyTextualHostDecision(False, "non-interactive terminal")
    probe = textual_probe or textual_available
    if not probe():
        return ApplyTextualHostDecision(False, "Textual package unavailable")
    return ApplyTextualHostDecision(True, "interactive artifact apply with Textual available")


def _raw_has_flag(raw_argv: Sequence[str], *names: str) -> bool:
    wanted = set(names)
    return any(str(token).split("=", 1)[0] in wanted for token in raw_argv)


def child_argv(raw_argv: Sequence[str], *, force_yes: bool) -> list[str]:
    """Return argv for the canonical managed child invocation."""

    argv = [str(token) for token in raw_argv]
    if force_yes and not _raw_has_flag(argv, "--yes", "-y", "--force"):
        # Global options may precede every command and avoid worker-thread stdin.
        argv.insert(0, "--yes")
    return argv


def _artifact_path_from_args(args: Any) -> str:
    raw = getattr(args, "artifact", "")
    if isinstance(raw, (list, tuple)):
        return " ".join(str(item) for item in raw)
    return str(raw or "")


def artifact_review_snapshot(args: Any) -> dict[str, Any]:
    """Best-effort, read-only review used before the child starts.

    Canonical qualification still happens exclusively in the managed child.
    """

    path_text = _artifact_path_from_args(args)
    review: dict[str, Any] = {
        "artifact": Path(path_text).name if path_text and " " not in path_text else path_text,
        "path": path_text,
        "command": _effective_apply_command(args) or "apply-overlay",
        "target": str(getattr(args, "target", "") or ""),
        "changes": "qualification pending",
        "validation": "qualification pending",
        "lifecycle": "qualification pending",
        "apply": "atomic lifecycle",
    }
    if not path_text or " " in path_text:
        return review
    try:
        from .artifacts import read_artifact_manifest, resolve_artifact_path

        artifact_path = resolve_artifact_path(path_text)
        manifest = read_artifact_manifest(artifact_path)
        if not isinstance(manifest, dict):
            return review
        inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
        entries = inventory.get("entries") if isinstance(inventory.get("entries"), list) else []
        actions = sorted({str(item.get("action") or "change") for item in entries if isinstance(item, dict)})
        validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
        apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
        target = str(manifest.get("target") or review["target"] or "")
        lifecycle_bits: list[str] = []
        script = validation.get("script") if isinstance(validation.get("script"), dict) else {}
        if script:
            lifecycle_bits.append("validator")
        tests = validation.get("tests") if isinstance(validation.get("tests"), list) else []
        if tests:
            lifecycle_bits.append(f"{len(tests)} tests")
        apply_bits: list[str] = []
        if "atomic" in apply:
            apply_bits.append("atomic" if bool(apply.get("atomic")) else "non-atomic")
        conflict = str(apply.get("conflict_policy") or "").strip()
        if conflict:
            apply_bits.append(conflict)
        if bool(apply.get("commit")):
            apply_bits.append("commit")
        review.update({
            "artifact": str(manifest.get("name") or review["artifact"]),
            "target": target,
            "changes": f"{len(entries)} files" + (f" · {', '.join(actions)}" if actions else ""),
            "validation": "required" if bool(validation.get("required")) else "optional",
            "lifecycle": " · ".join(lifecycle_bits) or "standard",
            "apply": " · ".join(apply_bits) or "standard",
        })
    except Exception:
        # The managed child owns authoritative validation and will report errors.
        pass
    return review


def apply_phase_text(state: Mapping[str, Any]) -> str | None:
    """Return meaningful canonical phase text, or None when state omits it.

    Final Tasker/RUN updates may intentionally carry only status/progress.  The
    presentation must preserve the previous detailed phase in that case rather
    than replace it with a generic placeholder.
    """

    phase = state.get("phase")
    if isinstance(phase, Mapping):
        phase_name = str(phase.get("name") or phase.get("label") or "")
        detail = str(phase.get("detail") or "")
    else:
        phase_name = str(phase or "")
        detail = ""
    progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
    if not detail:
        detail = str(progress.get("detail") or "")
    text = " · ".join(value for value in (phase_name, detail) if value)
    return text or None


class ArtifactApplyHostSource:
    """Managed-child source consumed by the main-thread Textual app."""

    def __init__(
        self,
        raw_argv: Sequence[str],
        args: Any,
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        max_log_lines: int = 1200,
        live_state_dir: Path | None = None,
    ) -> None:
        self.raw_argv = tuple(str(item) for item in raw_argv)
        self.args = args
        self.env = dict(env if env is not None else os.environ)
        self.cwd = Path(cwd or Path.cwd())
        self.popen_factory = popen_factory
        self.max_log_lines = max(100, int(max_log_lines))
        self._live_state_dir = Path(live_state_dir) if live_state_dir is not None else None
        self.run_id = uuid.uuid4().hex
        self.review = artifact_review_snapshot(args)
        self._lines: queue.Queue[str] = queue.Queue(maxsize=max(512, self.max_log_lines * 2))
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._started = False
        self._cancel_requested = False
        self._last_state: dict[str, Any] = {}
        self._returncode: int | None = None
        self._started_at = 0.0

    @property
    def state_dir(self) -> Path:
        return self._live_state_dir or resolve_cli_path(DEFAULT_TASKER_DIR)

    @property
    def run_state_file(self) -> Path:
        return self.state_dir / "runs" / f"{self.run_id}.json"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def returncode(self) -> int | None:
        proc = self._process
        if proc is not None and self._returncode is None:
            value = proc.poll()
            if value is not None:
                self._returncode = int(value)
        return self._returncode

    @property
    def running(self) -> bool:
        return bool(self._started and self.returncode is None)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def _offer_line(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        try:
            self._lines.put_nowait(text)
        except queue.Full:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                pass
            try:
                self._lines.put_nowait(text)
            except queue.Full:
                pass

    def start(self, *, force_yes: bool) -> None:
        if self._started:
            return
        child_env = dict(self.env)
        child_env[_CHILD_ENV] = "1"
        child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        child_env["DEVTOOL_RUN_ID"] = self.run_id
        child_env["DEVTOOL_MANAGED_RUN_HANDOFF"] = create_managed_run_handoff(self.run_id)
        # Nested runner/hook presentation remains plain; the parent owns the TTY.
        child_env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"
        if "NO_COLOR" not in child_env and "FORCE_COLOR" not in child_env:
            child_env["FORCE_COLOR"] = "1"
        argv = [sys.executable, "-m", "devtool", *child_argv(self.raw_argv, force_yes=force_yes)]
        self._process = self.popen_factory(
            argv,
            cwd=str(self.cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._started = True
        self._started_at = time.monotonic()

        def _reader_loop() -> None:
            proc = self._process
            if proc is None or proc.stdout is None:
                return
            try:
                for line in proc.stdout:
                    self._offer_line(line)
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        self._reader = threading.Thread(target=_reader_loop, name=f"devtool-apply-host-reader-{self.run_id[:8]}", daemon=True)
        self._reader.start()

    def drain_lines(self, *, limit: int = 256) -> list[str]:
        lines: list[str] = []
        for _ in range(max(1, int(limit))):
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return lines

    def read_state(self) -> dict[str, Any]:
        path = self.run_state_file
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._last_state = payload
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return dict(self._last_state)

    def request_cancel(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        self._cancel_requested = True
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except Exception:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                pass

    def terminate(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def wait(self, timeout: float | None = None) -> int | None:
        proc = self._process
        if proc is None:
            return self._returncode
        try:
            self._returncode = int(proc.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            return None
        return self._returncode

    def final_summary(self) -> tuple[str, str]:
        state = self.read_state()
        log_path = str(state.get("log_path") or "")
        status = str(state.get("status") or "")
        return status, log_path



def create_overlay_apply_source(
    repo_root: str | Path,
    artifact: str,
    *,
    target: str = "",
    env: Mapping[str, str] | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    live_state_dir: Path | None = None,
) -> ArtifactApplyHostSource:
    """Create the canonical managed-child source used by in-TUI overlay apply.

    The returned object still runs the ordinary ``devtool apply-overlay`` CLI in
    a child process.  This helper only makes that H02 host reusable by the
    control center without creating a second lifecycle implementation.
    """

    root = Path(repo_root).expanduser().resolve(strict=False)
    artifact_text = str(artifact or "").strip()
    args = SimpleNamespace(
        command="apply-overlay", artifact=artifact_text, target=str(target or ""),
        yes=True, force=False, quiet=False, verbose=False, trace=False,
        stdout_payload="auto", json=False, _devtool_json=False,
    )
    return ArtifactApplyHostSource(
        ["--repo", str(root), "--yes", "apply-overlay", artifact_text],
        args, env=env, cwd=root, popen_factory=popen_factory, live_state_dir=live_state_dir,
    )

def build_apply_textual_app(source: ArtifactApplyHostSource, *, confirm_required: bool, compact: bool = False) -> Any:
    """Build the real main-thread Textual artifact workspace.

    The workspace is intentionally mobile-first. Once execution starts the large
    review card collapses, canonical/semantic activity occupies only a few rows,
    and the log flexes into every remaining terminal row. Raw child output is
    retained locally and can be toggled without making pytest dots/heartbeats the
    primary UI. The child process remains the sole mutation/validation authority.
    """

    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.css.query import NoMatches
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, ProgressBar, RichLog, Static

    class ApplyConfirmationModal(ModalScreen[bool]):
        # Keep confirmation controls reachable even on 32×10 Termux screens.
        DEFAULT_CSS = """
        ApplyConfirmationModal { align: center middle; }
        #apply-confirm-box {
            width: 96%; max-width: 72; height: 85%; max-height: 92%;
            min-height: 7; border: round $warning; padding: 0 1; background: $surface;
        }
        #apply-confirm-title { height: 1; overflow-x: hidden; text-style: bold; }
        #apply-confirm-body { height: 1fr; min-height: 1; }
        #apply-confirm-actions { height: 3; min-height: 3; align-horizontal: right; }
        #apply-confirm-actions Button { min-width: 8; margin-left: 1; }
        """

        def compose(self) -> ComposeResult:
            with Vertical(id="apply-confirm-box"):
                yield Static("Apply this artifact?", id="apply-confirm-title", classes="ux-title")
                with VerticalScroll(id="apply-confirm-body"):
                    yield Static(artifact_review_text(source.review))
                with Horizontal(id="apply-confirm-actions"):
                    yield Button("Cancel", id="apply-confirm-no")
                    yield Button("Apply", variant="warning", id="apply-confirm-yes")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply-confirm-yes")

    class ApplyCancelModal(ModalScreen[bool]):
        DEFAULT_CSS = ApplyConfirmationModal.DEFAULT_CSS

        def compose(self) -> ComposeResult:
            with Vertical(id="apply-confirm-box"):
                yield Static("Cancel the active artifact apply?", id="apply-confirm-title")
                with VerticalScroll(id="apply-confirm-body"):
                    yield Static("Devtool will signal the managed apply process. Rollback and recovery remain controlled by the canonical transaction.")
                with Horizontal(id="apply-confirm-actions"):
                    yield Button("Keep", id="apply-confirm-no")
                    yield Button("Cancel", variant="error", id="apply-confirm-yes")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply-confirm-yes")

    icons = job_icons()

    class ArtifactApplyApp(App[int]):
        TITLE = "Devtool"
        SUB_TITLE = "Artifact apply"
        BINDINGS = [
            ("ctrl+c", "request_cancel", "Cancel"),
            ("l", "toggle_logs", "Logs"),
            ("d", "toggle_details", "Details"),
            ("r", "toggle_raw", "Raw log"),
            ("q", "close_finished", "Close"),
            ("enter", "close_finished", "Close"),
        ]
        CSS = TEXTUAL_SHARED_CSS + """
        /* Compact jobs use the full clean screen, not a 5-row inline strip. */
        .compact-job Header, .compact-job Footer { display: none; }
        .compact-job #apply-root { padding: 1 2; }
        .compact-job #apply-context { min-height: 3; border-bottom: solid $accent; margin-bottom: 1; }
        .compact-job #apply-review { display: none; }
        .compact-job #apply-stages { height: 6; min-height: 0; color: $text; }
        .compact-job #apply-stage { color: $accent; text-style: bold; }
        .compact-job #apply-log-toolbar { height: 1; min-height: 1; border-top: none; }
        .compact-job #apply-log-toggle, .compact-job #apply-raw-toggle { display: none; }
        .compact-job #apply-activity { max-height: 2; }
        .compact-job #apply-log { height: 1fr; min-height: 2; max-height: 100%; border: round $panel; }
        .compact-job #apply-help { height: 1; color: $text-muted; }
        .compact-job.compact #apply-root { padding: 0 1; }
        .compact-job.short #apply-root { padding-top: 0; padding-bottom: 0; }
        .compact-job.short #apply-context { min-height: 2; margin-bottom: 0; }
        .compact-job.short #apply-stages { height: 2; }
        .compact-job.short #apply-activity { display: none; }
        .compact-job.tiny #apply-stages { height: 1; }
        .compact-job.tiny #apply-phase { display: none; }
        .compact-job.tiny #apply-stage { display: none; }
        .compact-job.tiny #apply-log-toolbar { display: none; }
        #apply-root { height: 1fr; min-height: 0; }
        #apply-context { height: auto; min-height: 3; }
        #apply-status { height: 1; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #apply-meta { height: 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #apply-review { height: auto; max-height: 8; margin: 0 1; }
        #apply-stages { display: none; }
        .compact-job #apply-stages { display: block; }
        #apply-help { display: none; }
        .compact-job #apply-help { display: block; }
        #apply-stage { height: 1; padding: 0 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #apply-progress-row { height: 1; min-height: 1; padding: 0 1; }
        #apply-progress { width: 1fr; height: 1; margin-right: 1; }
        #apply-progress-meta { width: auto; min-width: 8; height: 1; content-align: right middle; color: $text-muted; }
        #apply-phase { height: auto; min-height: 1; max-height: 2; padding: 0 1; overflow-x: hidden; overflow-y: hidden; }
        #apply-activity { height: auto; min-height: 0; max-height: 3; padding: 0 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #apply-notice { height: auto; min-height: 0; max-height: 2; padding: 0 1; color: $warning; overflow-x: hidden; overflow-y: hidden; }
        #apply-log-toolbar { height: 3; min-height: 3; padding: 0 1; border-top: solid $panel; }
        #apply-log-title { width: 1fr; height: 1; content-align: left middle; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #apply-log-toggle, #apply-raw-toggle { width: auto; min-width: 10; margin-left: 1; }
        #apply-log { height: 1fr; min-height: 1; border-top: solid $surface-lighten-2; }
        .result-completed #apply-context { border-bottom: solid $success; }
        .result-completed #apply-status,
        .result-completed #apply-meta,
        .result-completed #apply-notice { color: $success; }
        .result-failed #apply-context { border-bottom: solid $error; }
        .result-failed #apply-status,
        .result-failed #apply-meta,
        .result-failed #apply-notice { color: $error; }
        .result-cancelled #apply-context { border-bottom: solid $warning; }
        .result-cancelled #apply-status,
        .result-cancelled #apply-meta,
        .result-cancelled #apply-notice { color: $warning; }
        .compact #apply-review, .narrow #apply-review { margin-left: 0; margin-right: 0; border-left: none; border-right: none; }
        .compact #apply-stage,
        .compact #apply-progress-row,
        .compact #apply-phase,
        .compact #apply-activity,
        .compact #apply-notice,
        .compact #apply-log-toolbar { padding-left: 0; padding-right: 0; }
        .compact #apply-progress-meta { min-width: 6; }
        .short #apply-context { min-height: 2; }
        .short #apply-review { display: none; }
        .short #apply-phase { max-height: 1; }
        .short #apply-activity { max-height: 1; }
        .short #apply-log-toolbar { height: 1; min-height: 1; }
        .short #apply-log-toggle, .short #apply-raw-toggle { display: none; }
        .tiny #apply-meta { display: none; }
        .tiny #apply-notice { max-height: 1; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._compact_job = compact
            self._logs_visible = True
            self._show_raw = False
            self._details_visible = False
            self._finish_seen_at: float | None = None
            self._returncode: int | None = None
            self._started_child = False
            self._started_at = 0.0
            self._poll_timer: Any | None = None
            self._last_state: dict[str, Any] = {}
            self._last_semantic_phase: str = ""
            self._activity = ArtifactActivityTracker()
            self._raw_lines: list[str] = []
            self._stage_tracker = ApplyStageTracker()

        def compose(self) -> ComposeResult:
            review = source.review
            yield Header()
            with Vertical(id="apply-root"):
                with Vertical(id="apply-context", classes="ux-context"):
                    yield Static(f"{icons['brand']}  DEVTOOL / ARTIFACT APPLY", classes="ux-kicker")
                    yield Static(str(review.get("artifact") or review.get("path") or "Artifact"), id="apply-status", classes="ux-title")
                    yield Static("Review canonical artifact contract", id="apply-meta")
                yield Static(artifact_review_text(review), id="apply-review", classes="ux-card")
                yield Static(artifact_stage_line({}), id="apply-stage")
                yield Static("\n".join(f"{icons['pending']} {stage}" for stage in APPLY_STAGES), id="apply-stages")
                with Horizontal(id="apply-progress-row"):
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="apply-progress")
                    yield Static("working", id="apply-progress-meta")
                yield Static("Canonical artifact qualification pending", id="apply-phase")
                yield Static("", id="apply-activity")
                yield Static("", id="apply-notice")
                with Horizontal(id="apply-log-toolbar"):
                    yield Static("Activity log · l hide · r raw · d details", id="apply-log-title")
                    yield Button("Hide", id="apply-log-toggle")
                    yield Button("Raw", id="apply-raw-toggle")
                yield RichLog(id="apply-log", wrap=True, highlight=False, markup=False, max_lines=source.max_log_lines)
                yield Static("l logs  ·  r raw  ·  d details  ·  Ctrl+C cancel  ·  q close", id="apply-help")
            yield Footer()

        def _apply_responsive_geometry(self, width: int, height: int) -> None:
            screen = self.screen
            desired = set(responsive_class_names(width, height))
            # Compatibility class retained for H02 qualification and historical CSS.
            if "compact" in desired:
                desired.add("narrow")
            for name in ("compact", "ultra-compact", "short", "tiny"):
                if name in desired:
                    screen.add_class(name)
                else:
                    screen.remove_class(name)
            if "narrow" in desired:
                screen.add_class("narrow")
            else:
                screen.remove_class("narrow")
            self._sync_details_visibility()
            self._refresh_stage_line()
            self._refresh_context_summary()
            self._refresh_activity()
            if self._compact_job:
                try:
                    self.query_one("#apply-help", Static).update(
                        "l logs · r raw · ^C cancel · q close" if width < 48 else
                        "l logs  ·  r raw  ·  d details  ·  Ctrl+C cancel  ·  q close"
                    )
                except NoMatches:
                    pass
            try:
                screen.refresh(layout=True)
            except Exception:
                pass

        def _sync_details_visibility(self) -> None:
            try:
                review = self.query_one("#apply-review", Static)
            except NoMatches:
                return
            constrained = self.screen.has_class("short") or self.screen.has_class("tiny")
            review.display = bool((not self._started_child or self._details_visible) and not constrained)

        def _refresh_context_summary(self) -> None:
            if not self._started_child:
                return
            width = max(24, self.size.width - 2)
            self.query_one("#apply-status", Static).update(
                artifact_running_summary(source.review, width=width)
            )

        def _refresh_stage_line(self) -> None:
            rc = source.returncode
            if rc is not None:
                self._stage_tracker.finish(rc)
            current = self._stage_tracker.visible_current()
            compact = self.screen.has_class("compact") or self.screen.has_class("short")
            index = APPLY_STAGES.index(current) + 1
            text = (f"{index}/{len(APPLY_STAGES)} {current}" if self.screen.has_class("tiny")
                    else f"{current} {index}/{len(APPLY_STAGES)}" if compact
                    else "  ".join(f"▶ {name}" if name == current else f"· {name}"
                                   for name in APPLY_STAGES))
            try:
                self.query_one("#apply-stage", Static).update(text)
                from rich.text import Text
                rows = Text()
                symbols = {"active": icons["active"], "done": icons["done"],
                           "pending": icons["pending"], "failed": icons["failed"],
                           "skipped": "−"}
                colors = {"active": "bold cyan", "done": "green", "pending": "dim",
                          "failed": "bold red", "skipped": "yellow"}
                for name in APPLY_STAGES:
                    value = self._stage_tracker.outcomes[name]
                    suffix = " · COMPLETE" if name == "Finish" and rc == 0 else (
                        " · FAILED" if name == "Finish" and rc not in (None, 0, 130)
                        else " · CANCELLED" if name == "Finish" and rc == 130 else "")
                    rows.append(f"{symbols[value]} {name}{suffix}", style=colors[value])
                    if name != APPLY_STAGES[-1]:
                        rows.append("\n")
                self.query_one("#apply-stages", Static).update(rows)
                if self.screen.has_class("tiny"):
                    self.query_one("#apply-stages", Static).update(text)
                elif self.screen.has_class("short"):
                    done = sum(s == "done" for s in self._stage_tracker.outcomes.values())
                    self.query_one("#apply-stages", Static).update(f"{text}\n{done} confirmed complete")
            except NoMatches:
                pass

        def _elapsed(self) -> float:
            value = getattr(source, "elapsed_seconds", None)
            if isinstance(value, (int, float)):
                return max(0.0, float(value))
            if self._started_at:
                return max(0.0, time.monotonic() - self._started_at)
            return 0.0

        def _refresh_activity(self) -> None:
            try:
                max_lines = 1 if self.screen.has_class("short") else 2
                text = self._activity.activity_text(
                    elapsed_seconds=self._elapsed() if self._started_child else None,
                    max_lines=max_lines,
                    width=max(28, self.size.width - 2),
                )
                self.query_one("#apply-activity", Static).update(text)
                mode = "raw log" if self._show_raw else "activity log"
                self.query_one("#apply-log-title", Static).update(
                    f"{mode} · {len(self._raw_lines)} lines · l hide · r {'activity' if self._show_raw else 'raw'} · d details"
                )
            except NoMatches:
                pass

        def on_mount(self) -> None:
            if self._compact_job:
                self.screen.add_class("compact-job")
            self._apply_responsive_geometry(self.size.width, self.size.height)
            self._poll_timer = self.set_interval(0.10, self._poll_child)
            if confirm_required:
                self.push_screen(ApplyConfirmationModal(), self._after_initial_confirmation)
            else:
                self._start_child(force_yes=True)

        def on_resize(self, event: Any) -> None:
            self._apply_responsive_geometry(event.size.width, event.size.height)

        def _after_initial_confirmation(self, confirmed: bool | None) -> None:
            if not confirmed:
                self.exit(1)
                return
            self._start_child(force_yes=True)

        def _start_child(self, *, force_yes: bool) -> None:
            if self._started_child:
                return
            self._started_child = True
            self._stage_tracker.begin()
            self._started_at = time.monotonic()
            self.screen.add_class("running")
            self.query_one("#apply-meta", Static).update("STARTING · canonical LIFE/RUN child")
            self._sync_details_visibility()
            self._refresh_context_summary()
            try:
                source.start(force_yes=force_yes)
            except Exception as exc:
                self._returncode = 1
                self._finish_seen_at = time.monotonic()
                self.screen.add_class("result-failed")
                self.query_one("#apply-meta", Static).update("FAILED · could not start canonical child")
                self.query_one("#apply-phase", Static).update(f"{type(exc).__name__}: {exc}")
                self.query_one("#apply-log", RichLog).write(styled_job_line(f"{type(exc).__name__}: {exc}"))
                self.query_one("#apply-notice", Static).update("✕ FAILED · press q or Enter to close")
                self._stop_polling()

        def _apply_state(self, state: Mapping[str, Any]) -> None:
            self._last_state = dict(state)
            self._stage_tracker.observe_state(state)
            status = str(state.get("status") or "running")
            message = str(state.get("message") or "Working…")
            percent, counter, scope = artifact_display_progress(state, self._activity, returncode=source.returncode)
            meta_scope = "canonical LIFE/RUN" if scope == "overall" else ("validation child" if scope == "validation child" else "canonical LIFE/RUN · progress indeterminate")
            self.query_one("#apply-meta", Static).update(f"{status_label(status)} · {meta_scope}")
            phase_line = apply_phase_text(state)
            if phase_line is not None:
                self._last_semantic_phase = str(phase_line)
                width = max(24, self.size.width - 2)
                self.query_one("#apply-phase", Static).update(fit_middle(phase_line, width))
            progress_widget = self.query_one("#apply-progress", ProgressBar)
            if percent is None:
                progress_widget.update(total=None)
                progress_widget.display = source.returncode is None
            else:
                progress_widget.update(total=100, progress=percent)
                progress_widget.display = True
            self.query_one("#apply-progress-meta", Static).update(counter)
            self._refresh_stage_line()
            if message and message not in {phase_line, status}:
                self.query_one("#apply-notice", Static).update(fit_middle(message, max(24, self.size.width - 2)))
            self._refresh_activity()

        def _stop_polling(self) -> None:
            timer = self._poll_timer
            if timer is not None:
                try:
                    timer.stop()
                finally:
                    self._poll_timer = None

        def _remember_line(self, line: str) -> bool:
            text = str(line or "").rstrip("\r\n")
            if not text:
                return False
            self._raw_lines.append(text)
            self._stage_tracker.observe_line(text)
            if len(self._raw_lines) > source.max_log_lines:
                del self._raw_lines[: len(self._raw_lines) - source.max_log_lines]
            return self._activity.observe(readable_job_line(text))

        def _emit_line(self, line: str, surfaced: bool) -> None:
            if not self._logs_visible:
                return
            if self._show_raw or surfaced:
                self.query_one("#apply-log", RichLog).write(styled_job_line(line))

        def _drain_source_lines(self) -> None:
            for line in source.drain_lines():
                surfaced = self._remember_line(line)
                self._emit_line(str(line), surfaced)
            self._refresh_activity()
            self._refresh_stage_line()

        def _poll_child(self) -> None:
            try:
                self._drain_source_lines()
                state = source.read_state()
                if state:
                    self._apply_state(state)
                elif self._started_child:
                    # Do not pin a fake 0% while a validator subprocess is alive.
                    percent, counter, _scope = artifact_display_progress({}, self._activity, returncode=source.returncode)
                    progress_widget = self.query_one("#apply-progress", ProgressBar)
                    if percent is None:
                        progress_widget.update(total=None)
                        progress_widget.display = source.returncode is None
                    else:
                        progress_widget.update(total=100, progress=percent)
                        progress_widget.display = True
                    self.query_one("#apply-progress-meta", Static).update(counter)
                    self._refresh_activity()
                rc = source.returncode
                if rc is None:
                    return
                self._drain_source_lines()
                if self._finish_seen_at is None:
                    self._finish_seen_at = time.monotonic()
                    self._returncode = int(rc)
                    outcome = "completed" if rc == 0 else "cancelled" if rc == 130 else "failed"
                    label = "completed successfully" if rc == 0 else ("cancelled" if rc == 130 else f"failed · exit {rc}")
                    self.query_one("#apply-meta", Static).update(status_label(outcome) + " · canonical lifecycle finished")
                    for result_class in ("result-completed", "result-failed", "result-cancelled"):
                        self.screen.remove_class(result_class)
                    self.screen.add_class(f"result-{outcome}")
                    self._refresh_stage_line()
                    # Completion is lifecycle state, not a replacement phase.
                    # Preserve the last canonical semantic phase so operators
                    # can still see what completed after the child exits.
                    if self._last_semantic_phase:
                        self.query_one("#apply-phase", Static).update(
                            fit_middle(self._last_semantic_phase, max(24, self.size.width - 2))
                        )
                    else:
                        self.query_one("#apply-phase", Static).update(f"Devtool {label}")
                    if rc == 0:
                        self.query_one("#apply-progress", ProgressBar).display = True
                        self.query_one("#apply-progress", ProgressBar).update(total=100, progress=100.0)
                        self.query_one("#apply-progress-meta", Static).update("100%")
                    symbol = icons["done"] if rc == 0 else ("■" if rc == 130 else icons["failed"])
                    self.query_one("#apply-notice", Static).update(
                        f"{symbol} {status_label(outcome)} · press q or Enter to close"
                    )
                    self._stop_polling()
                return
            except NoMatches:
                self._stop_polling()
                return

        def action_toggle_logs(self) -> None:
            log = self.query_one("#apply-log", RichLog)
            self._logs_visible = not self._logs_visible
            log.display = self._logs_visible
            self.query_one("#apply-log-toggle", Button).label = "Hide" if self._logs_visible else "Show"

        def action_toggle_details(self) -> None:
            self._details_visible = not self._details_visible
            self._sync_details_visibility()
            try:
                self.screen.refresh(layout=True)
            except Exception:
                pass

        def action_toggle_raw(self) -> None:
            self._show_raw = not self._show_raw
            log = self.query_one("#apply-log", RichLog)
            log.clear()
            if self._logs_visible:
                if self._show_raw:
                    for line in self._raw_lines:
                        log.write(styled_job_line(line))
                else:
                    replay = ArtifactActivityTracker()
                    for line in self._raw_lines:
                        if replay.observe(line):
                            log.write(styled_job_line(line))
            self.query_one("#apply-raw-toggle", Button).label = "Activity" if self._show_raw else "Raw"
            self._refresh_activity()

        def action_close_finished(self) -> None:
            if source.running or self._returncode is None:
                self.query_one("#apply-notice", Static).update(
                    "Artifact apply is still running · use Ctrl+C to cancel it."
                )
                return
            self.exit(self._returncode)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""
            if button_id == "apply-log-toggle":
                self.action_toggle_logs()
            elif button_id == "apply-raw-toggle":
                self.action_toggle_raw()

        def action_request_cancel(self) -> None:
            if not source.running:
                return
            self.push_screen(ApplyCancelModal(), self._after_cancel_confirmation)

        def _after_cancel_confirmation(self, confirmed: bool | None) -> None:
            if not confirmed:
                return
            source.request_cancel()
            self.query_one("#apply-notice", Static).update("Cancellation requested · waiting for Devtool recovery/rollback…")

        def on_unmount(self) -> None:
            self._stop_polling()
            if source.running:
                source.terminate()

    return ArtifactApplyApp()

def run_apply_textual_host(raw_argv: Sequence[str], args: Any) -> int:
    """Run one interactive artifact apply under a main-thread Textual host."""

    source = ArtifactApplyHostSource(raw_argv, args)
    confirm_required = not bool(getattr(args, "yes", False) or getattr(args, "force", False))
    mode = job_ui_mode(source.env)
    app = build_apply_textual_app(source, confirm_required=confirm_required, compact=mode == "compact")
    try:
        result = run_job_app(app, mode=mode)
    finally:
        # Closing/crashing the Textual view must not orphan a mutating apply.
        if source.running:
            source.terminate()
    observed = result if isinstance(result, int) else source.returncode
    if observed is None:
        observed = source.wait(timeout=3.0)
    # A prematurely closed UI or unknown child exit must not pass validation.
    rc = int(observed) if observed is not None else 1
    status, log_path = source.final_summary()
    # Keep the post-TUI terminal useful for shell history / support bundles.
    if rc == 0:
        print("Devtool completed successfully.")
    elif rc == 130:
        print("Devtool cancelled.")
    else:
        print("Devtool failed.")
    print(f"Run: {source.run_id}")
    if log_path:
        print(f"Log: {log_path}")
    if status and status not in {"ok", "running"}:
        print(f"Status: {status}")
    return rc


__all__ = [
    "ApplyTextualHostDecision",
    "ArtifactApplyHostSource",
    "artifact_review_snapshot",
    "build_apply_textual_app",
    "child_argv",
    "create_overlay_apply_source",
    "run_apply_textual_host",
    "select_apply_textual_host",
    "textual_available",
]
