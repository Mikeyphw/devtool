from __future__ import annotations

import contextlib
from collections import Counter, defaultdict, deque
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Sequence


from .artifacts import (
    archive_suffix,
    is_artifact_metadata_member,
    is_overlay_bundle,
    materialize_overlay_bundle_entries,
    open_tar_archive,
    preview_artifact,
    preview_overlay_bundle,
    create_overlay_bundle,
    read_artifact_manifest,
    artifact_solution,
    artifact_target,
    read_delete_manifest,
    resolve_artifact_path,
    validate_artifact,
)
from .config import ensure_artifact_target_config, generate_template
from .console_utils import create_console, is_termux_environment, resolve_cli_path
from .diagnostics import classify_failure, parse_issues
from .dotnet_progress import DotnetProgressTracker, dotnet_progress_plan
from .generic_progress import GenericProgressTracker
from .npm_progress import NpmProgressTracker, npm_progress_plan
from .gradle_progress import GradleProgressTracker
from .gradle_reports import collect_report_metadata
from .transactions import create_apply_transaction, rollback_transaction
from .logstore import redact_sensitive_text, write_log
from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .xdg import state_dir
from .project_runners import (
    DotnetRunner as _DotnetRunner,
    GenericCommandRunner as _GenericCommandRunner,
    PythonRunner as _PythonRunner,
    add_no_build_to_dotnet_test_spec as _runner_add_no_build_to_dotnet_test_spec,
    add_no_build_to_dotnet_test_specs as _runner_add_no_build_to_dotnet_test_specs,
    add_no_restore_to_dotnet_build_spec as _runner_add_no_restore_to_dotnet_build_spec,
    get_project_runner as _resolve_project_runner,
    is_dotnet_build_argv as _runner_is_dotnet_build_argv,
    is_dotnet_test_argv as _runner_is_dotnet_test_argv,
)
from .tasker import TaskerProgressSink

console = create_console()


def _memory_snapshot() -> tuple[int | None, int | None, int | None, float | None, float | None]:
    """Return MemAvailable, SwapFree, SwapTotal and memory PSI avg10 values."""

    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in {"MemAvailable", "SwapFree", "SwapTotal"}:
                continue
            values[key] = max(0, int(remainder.strip().split()[0]) // 1024)
    except (OSError, ValueError, IndexError):
        pass

    psi_some: float | None = None
    psi_full: float | None = None
    try:
        for line in Path("/proc/pressure/memory").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            kind, _, payload = line.partition(" ")
            fields = {
                key: value
                for token in payload.split()
                if "=" in token
                for key, value in [token.split("=", 1)]
            }
            if kind == "some" and "avg10" in fields:
                psi_some = float(fields["avg10"])
            elif kind == "full" and "avg10" in fields:
                psi_full = float(fields["avg10"])
    except (OSError, ValueError):
        pass

    return (
        values.get("MemAvailable"),
        values.get("SwapFree"),
        values.get("SwapTotal"),
        psi_some,
        psi_full,
    )


def _memory_guard_limits(memory_guard_mb: int) -> tuple[int, int]:
    """Return adaptive sustained-pressure and hard-emergency thresholds."""

    sustained = max(320, min(memory_guard_mb - 96, int(memory_guard_mb * 0.70)))
    hard = max(192, min(sustained - 96, int(memory_guard_mb * 0.50)))
    return sustained, hard


def _critical_memory_pressure(
    snapshot: tuple[int | None, int | None, int | None, float | None, float | None],
    history: Sequence[int],
    sustained_limit: int,
) -> tuple[bool, str]:
    available, swap_free, swap_total, psi_some, psi_full = snapshot
    if available is None or available >= sustained_limit:
        return False, ""

    swap_exhausted = False
    if swap_total is not None:
        swap_reserve = max(64, int(swap_total * 0.05))
        swap_exhausted = swap_total <= 0 or (swap_free or 0) <= swap_reserve
    worsening = len(history) >= 8 and (history[0] - history[-1]) >= 64
    psi_stalled = (psi_full is not None and psi_full >= 1.0) or (
        psi_some is not None and psi_some >= 25.0
    )
    telemetry_missing = swap_total is None and psi_some is None and psi_full is None
    critical = (
        (swap_exhausted and (psi_stalled or worsening))
        or (psi_stalled and worsening)
        or (telemetry_missing and worsening)
    )

    signals: list[str] = []
    if swap_total is not None:
        signals.append(f"swap {swap_free or 0}/{swap_total} MiB free")
    if psi_some is not None:
        signals.append(f"PSI some avg10={psi_some:.2f}")
    if psi_full is not None:
        signals.append(f"PSI full avg10={psi_full:.2f}")
    if len(history) >= 2:
        signals.append(f"trend {history[-1] - history[0]:+d} MiB")
    return critical, ", ".join(signals)


def _command_delegates_memory_guard(argv: Sequence[str]) -> bool:
    """Avoid two watchdogs monitoring the same Android builder process."""

    return any(
        value == "--memory-guard-mb" or value.startswith("--memory-guard-mb=")
        for value in argv
    )


def _try_root_cache_reclaim() -> bool:
    """Drop clean page cache once when direct root, su, or passwordless sudo works."""

    if os.name != "posix":
        return False
    command = "sync; echo 1 > /proc/sys/vm/drop_caches"
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.sync()
            Path("/proc/sys/vm/drop_caches").write_text("1\n", encoding="ascii")
            return True
    except OSError:
        pass

    candidates: list[list[str]] = []
    su = shutil.which("su")
    if su:
        candidates.append([su, "-c", command])
    sudo = shutil.which("sudo")
    shell = shutil.which("sh") or "/bin/sh"
    if sudo:
        candidates.append([sudo, "-n", shell, "-c", command])
    for candidate in candidates:
        try:
            result = subprocess.run(
                candidate,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


_TERMUX_DOTNET_ENV = "DEVTOOL_TERMUX_DOTNET"
_TERMUX_DOTNET_OS_RELEASE = """NAME=Termux
PRETTY_NAME=Termux
ID=termux
VERSION_ID=1
"""
_TERMUX_DOTNET_FALLBACKS = (
    "/data/data/com.termux/files/usr/bin/dotnet",
)


def _resolve_termux_dotnet() -> str | None:
    override = os.environ.get(_TERMUX_DOTNET_ENV, "").strip()
    if override:
        return override

    dotnet_path = shutil.which("dotnet")
    if dotnet_path:
        return dotnet_path

    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        candidate = Path(prefix) / "bin" / "dotnet"
        if candidate.exists():
            return str(candidate)

    for fallback in _TERMUX_DOTNET_FALLBACKS:
        if Path(fallback).exists():
            return fallback

    return None


def _termux_proot_etc_dir() -> Path:
    """Return a writable synthetic ``/etc`` tree for Termux dotnet.

    The normal location lives below Termux ``PREFIX`` or Devtool's state
    directory. Tests, stripped environments, and unusual launchers may expose
    a read-only HOME such as ``/usr`` while still explicitly declaring
    ``TERMUX_VERSION``. In that case, fall back to the process temporary
    directory instead of attempting to create ``/usr/var/devtool``.
    """

    candidates: list[Path] = []
    override = os.environ.get("DEVTOOL_TERMUX_PROOT_ROOT", "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        candidates.append(Path(prefix).expanduser() / "var" / "devtool" / "proot")

    candidates.append(state_dir() / "proot")
    candidates.append(
        Path(tempfile.gettempdir())
        / f"devtool-{getattr(os, 'getuid', lambda: 0)()}"
        / "proot"
    )

    attempted: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        normalized = str(base)
        if normalized in seen:
            continue
        seen.add(normalized)
        etc_dir = base / "etc"
        try:
            etc_dir.mkdir(parents=True, exist_ok=True)
            os_release = etc_dir / "os-release"
            current = (
                os_release.read_text(encoding="utf-8", errors="ignore")
                if os_release.exists()
                else ""
            )
            if current != _TERMUX_DOTNET_OS_RELEASE:
                os_release.write_text(_TERMUX_DOTNET_OS_RELEASE, encoding="utf-8")
            return etc_dir
        except OSError as exc:
            attempted.append(f"{etc_dir}: {exc}")

    details = "; ".join(attempted) or "no candidate directories were available"
    raise RuntimeError(
        "Unable to create a writable Termux proot /etc shim. "
        "Set DEVTOOL_TERMUX_PROOT_ROOT to a writable directory. "
        f"Attempts: {details}"
    )


def _prepare_termux_dotnet(argv: list[str]) -> tuple[list[str], str | None]:
    if not argv or argv[0] != "dotnet" or not is_termux_environment():
        return argv, None

    dotnet_path = _resolve_termux_dotnet()
    if not dotnet_path or not shutil.which("proot"):
        return argv, None

    etc_dir = _termux_proot_etc_dir()
    return ["proot", "-b", f"{etc_dir}:/etc", dotnet_path, *argv[1:]], None



def get_project_runner(config: ProjectConfig):
    """Return the command planner for ``config``.

    Kept in this module as the public compatibility entry point while runner
    implementations live in :mod:`devtool.project_runners`.
    """

    return _resolve_project_runner(config)


def runner_capabilities(config: ProjectConfig):
    return get_project_runner(config).capabilities_for(config)


def build_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    runner = get_project_runner(config)
    if action == "validate":
        return _apply_config_metadata(config, runner.build_validation_specs(config))
    if action == "wine-validate":
        return _apply_config_metadata(config, runner.build_validation_specs(config, wine=True))
    return _apply_config_metadata(config, runner.build_specs(config, action))


def build_validation_specs(
    config: ProjectConfig,
    *,
    wine: bool = False,
    skip_restore: bool = False,
    skip_build: bool = False,
    skip_test: bool = False,
) -> list[CommandSpec]:
    return _apply_config_metadata(config, get_project_runner(config).build_validation_specs(
        config,
        wine=wine,
        skip_restore=skip_restore,
        skip_build=skip_build,
        skip_test=skip_test,
    ))


def _apply_config_metadata(config: ProjectConfig, specs: list[CommandSpec]) -> list[CommandSpec]:
    if not config.variant and not config.selected_target:
        return specs
    for spec in specs:
        metadata = dict(spec.metadata)
        if config.selected_target:
            metadata.setdefault("target", config.selected_target)
        if config.variant:
            metadata.setdefault("variant", config.variant)
            env = dict(spec.env)
            env.setdefault("DEVTOOL_VARIANT", config.variant)
            spec.env = env
        spec.metadata = metadata
    return specs


def _contains_dotnet_cli(argv: Sequence[str]) -> bool:
    return any(Path(str(value)).name == "dotnet" for value in argv)


def _is_dotnet_build_argv(argv: list[str]) -> bool:
    return _runner_is_dotnet_build_argv(argv)


def _add_no_restore_to_dotnet_build_spec(spec: CommandSpec, *, allow_explicit: bool = False) -> CommandSpec:
    return _runner_add_no_restore_to_dotnet_build_spec(spec, allow_explicit=allow_explicit)


def _is_dotnet_test_argv(argv: list[str]) -> bool:
    return _runner_is_dotnet_test_argv(argv)


def _add_no_build_to_dotnet_test_spec(spec: CommandSpec, *, allow_explicit: bool = False) -> CommandSpec:
    return _runner_add_no_build_to_dotnet_test_spec(spec, allow_explicit=allow_explicit)


def add_no_build_to_dotnet_test_specs(specs: list[CommandSpec], *, allow_explicit: bool = True) -> tuple[list[CommandSpec], int]:
    return _runner_add_no_build_to_dotnet_test_specs(specs, allow_explicit=allow_explicit)


def _dotnet_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _DotnetRunner().build_specs(config, action)


def _python_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _PythonRunner().build_specs(config, action)


def _generic_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    """Backward-compatible private wrapper for older integrations/tests."""

    return _GenericCommandRunner().build_specs(config, action)


def _interesting_output_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    return bool(re.search(r"(error|warning|failed|fail|restore|build|test|pack|publish|\d+%)", text, re.IGNORECASE))


_DOTNET_TEST_RESULT_LINE_RE = re.compile(
    r"\b(?P<status>Passed|Failed)!\s+-\s+Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+),\s*Skipped:\s*(?P<skipped>\d+),\s*Total:\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_TEST_RUN_SUCCESS_RE = re.compile(r"\bTest Run Successful\b", re.IGNORECASE)
_TEST_RUN_FAILED_RE = re.compile(r"\bTest Run Failed\b", re.IGNORECASE)


def _dotnet_test_completion_status(line: str) -> int | None:
    text = line.strip()
    if not text:
        return None
    match = _DOTNET_TEST_RESULT_LINE_RE.search(text)
    if match:
        return 1 if int(match.group("failed")) > 0 or match.group("status").lower() == "failed" else 0
    if _TEST_RUN_SUCCESS_RE.search(text):
        return 0
    if _TEST_RUN_FAILED_RE.search(text):
        return 1
    return None


def _is_dotnet_test_command(argv: list[str], command_name: str = "") -> bool:
    lowered_name = (command_name or "").strip().lower()
    if lowered_name in {"test", "tests", "wine-test"}:
        return True
    for index, value in enumerate(argv[:-1]):
        if Path(value).name == "dotnet" and str(argv[index + 1]).lower() == "test":
            return True
    return False


def _test_completion_grace_for(argv: list[str], command_name: str) -> float | None:
    if not _is_dotnet_test_command(argv, command_name):
        return None
    raw = os.environ.get("DEVTOOL_TEST_COMPLETION_GRACE", os.environ.get("DEVTOOL_TEST_COMPLETION_GRACE_SECONDS", "")).strip()
    if raw:
        try:
            value = float(raw)
            return value if value > 0 else None
        except ValueError:
            pass
    # Only applies after a final test summary has already printed, so this does
    # not kill legitimately quiet tests that are still running.
    return 30.0


def _idle_timeout_for(argv: list[str], command_name: str) -> float | None:
    """Return a safe idle timeout for commands known to hang after output ends.

    Dotnet/vstest can print all visible summaries but keep a process alive with
    no further output.  That makes the Rich progress bar look complete while
    devtool waits forever.  Keep this narrow by defaulting only test commands to
    an idle timeout; users can override or disable it with environment vars.
    """
    raw = os.environ.get("DEVTOOL_COMMAND_IDLE_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
            return value if value > 0 else None
        except ValueError:
            pass
    test_raw = os.environ.get("DEVTOOL_TEST_IDLE_TIMEOUT", "").strip()
    if test_raw:
        try:
            value = float(test_raw)
            return value if value > 0 else None
        except ValueError:
            pass
    # Do not auto-kill quiet test commands by default. Some test assemblies are
    # legitimately silent for more than a few seconds, and killing them can turn
    # a passing validation into exit 130. If a local environment really has a
    # dotnet/vstest hang, opt in explicitly with DEVTOOL_TEST_IDLE_TIMEOUT or
    # DEVTOOL_COMMAND_IDLE_TIMEOUT.
    return None


def _iter_process_output_lines(
    proc: subprocess.Popen,
    *,
    drain_seconds: float = 0.75,
    idle_timeout: float | None = None,
    completion_idle_grace: float | None = None,
    completion_status_detector: Callable[[str], int | None] | None = None,
    memory_guard_mb: int = 0,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
):
    """Yield child output without waiting forever for pipe EOF.

    This implementation avoids ``TextIOWrapper.readline()`` on a pipe after
    ``select`` because it can still block waiting for a newline.  It reads the
    pipe in non-blocking byte chunks, yields complete lines promptly, and stops
    shortly after the real child exits even if descendants inherited stdout.

    If ``idle_timeout`` is set and the main process stays alive with no output
    for that many seconds, the process group is terminated.  This is used for
    dotnet test/vstest hangs where all summaries have printed but the process
    never exits.
    """
    stdout = proc.stdout
    if stdout is None:
        return
    if os.name != "posix":
        for line in stdout:
            yield line
        return

    import codecs
    import fcntl
    import select

    fd = stdout.fileno()
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    last_output_at = time.monotonic()
    completion_seen_at: float | None = None
    completion_assumed_returncode: int | None = None
    memory_guard_pressure_samples = 0
    memory_guard_critical_samples = 0
    memory_guard_hard_samples = 0
    memory_guard_reclaim_attempted = False
    memory_guard_checked_at = 0.0
    supervisor_checked_at = 0.0
    heartbeat_checked_at = 0.0
    memory_guard_history: deque[int] = deque(maxlen=12)
    memory_guard_details = ""
    memory_guard_sustained_limit, memory_guard_hard_limit = _memory_guard_limits(memory_guard_mb)

    def emit_text(text: str):
        nonlocal pending
        pending += text
        # MSBuild/NuGet sometimes redraw progress with carriage returns rather
        # than newlines. Treat standalone CR as a live line boundary while
        # preserving normal CRLF pairs.
        while True:
            newline = pending.find("\n")
            carriage = pending.find("\r")
            candidates = [index for index in (newline, carriage) if index >= 0]
            if not candidates:
                break
            index = min(candidates)
            delimiter = pending[index]
            if delimiter == "\r" and index + 1 >= len(pending):
                # A trailing CR may be the first half of CRLF; wait for one more
                # chunk before deciding whether it is a redraw boundary.
                break
            line = pending[:index]
            if delimiter == "\r" and index + 1 < len(pending) and pending[index + 1] == "\n":
                pending = pending[index + 2:]
            else:
                pending = pending[index + 1:]
            yield line + "\n"

    def emit_and_track(text: str):
        nonlocal completion_seen_at, completion_assumed_returncode
        for line in emit_text(text):
            if completion_status_detector is not None:
                status = completion_status_detector(line)
                if status is not None:
                    completion_seen_at = time.monotonic()
                    # Multiple dotnet test projects can print separate summaries.
                    # Preserve the worst result so a later passing project cannot
                    # erase an earlier failed project when the post-summary hang
                    # guard has to assume an exit status.
                    if completion_assumed_returncode is None:
                        completion_assumed_returncode = int(status)
                    else:
                        completion_assumed_returncode = max(completion_assumed_returncode, int(status))
            yield line

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    last_output_at = time.monotonic()
                    for line in emit_and_track(decoder.decode(chunk)):
                        yield line
                    continue
                if proc.poll() is not None:
                    break

            if proc.poll() is not None:
                deadline = time.monotonic() + max(0.0, drain_seconds)
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([fd], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                    if not chunk:
                        break
                    for line in emit_and_track(decoder.decode(chunk)):
                        yield line
                break

            now_monotonic = time.monotonic()
            if heartbeat_callback is not None and (now_monotonic - heartbeat_checked_at) >= 1.0:
                heartbeat_checked_at = now_monotonic
                try:
                    heartbeat_callback()
                except Exception:
                    pass
            if supervisor_probe is not None and (now_monotonic - supervisor_checked_at) >= 1.0:
                supervisor_checked_at = now_monotonic
                event = None
                try:
                    event = supervisor_probe()
                except Exception as exc:
                    event = {"kind": "environment", "reason": f"supervisor probe failed: {type(exc).__name__}: {exc}"}
                kind = ""
                reason = ""
                if isinstance(event, dict):
                    kind = str(event.get("kind") or "").strip().lower()
                    reason = str(event.get("reason") or "").strip()
                elif isinstance(event, tuple) and len(event) >= 2:
                    kind = str(event[0] or "").strip().lower()
                    reason = str(event[1] or "").strip()
                elif isinstance(event, str) and event.strip():
                    kind = "resource"
                    reason = event.strip()
                if kind:
                    assumed_codes = {"timeout": 124, "interrupted": 130, "resource": 75, "environment": 76}
                    setattr(proc, "_devtool_assumed_returncode", assumed_codes.get(kind, 76))
                    setattr(proc, "_devtool_supervisor_event", {"kind": kind, "reason": reason})
                    message = f"\nDevtool validation supervisor stopped process: {kind}: {reason or 'policy requested stop'}.\n"
                    for line in emit_and_track(message):
                        yield line
                    try:
                        os.killpg(proc.pid, 15)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=3.0)
                    except Exception:
                        try:
                            os.killpg(proc.pid, 9)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    break

            if memory_guard_mb > 0 and (time.monotonic() - memory_guard_checked_at) >= 1.0:
                memory_guard_checked_at = time.monotonic()
                snapshot = _memory_snapshot()
                available = snapshot[0]
                if available is not None:
                    memory_guard_history.append(available)
                memory_guard_pressure_samples = (
                    memory_guard_pressure_samples + 1
                    if available is not None and available < memory_guard_mb
                    else 0
                )
                critical, memory_guard_details = _critical_memory_pressure(
                    snapshot, tuple(memory_guard_history), memory_guard_sustained_limit
                )
                memory_guard_critical_samples = (
                    memory_guard_critical_samples + 1 if critical else 0
                )
                memory_guard_hard_samples = (
                    memory_guard_hard_samples + 1
                    if available is not None and available < memory_guard_hard_limit
                    else 0
                )
                if memory_guard_pressure_samples >= 4 and not memory_guard_reclaim_attempted:
                    memory_guard_reclaim_attempted = True
                    before = available
                    if _try_root_cache_reclaim():
                        time.sleep(0.25)
                        snapshot = _memory_snapshot()
                        available = snapshot[0]
                        message = (
                            "\nDevtool used root once to reclaim clean filesystem cache under "
                            f"memory pressure ({before} MiB before; {available} MiB after).\n"
                        )
                        for line in emit_and_track(message):
                            yield line
                        if available is not None:
                            memory_guard_history.append(available)
                        if available is not None and available >= memory_guard_mb:
                            memory_guard_pressure_samples = 0
                            memory_guard_critical_samples = 0
                            memory_guard_hard_samples = 0
                        elif available is not None and available >= memory_guard_sustained_limit:
                            memory_guard_critical_samples = 0
                            memory_guard_hard_samples = 0
                if memory_guard_hard_samples >= 3 or memory_guard_critical_samples >= 10:
                    setattr(proc, "_devtool_assumed_returncode", 75)
                    reason = (
                        "hard emergency floor"
                        if memory_guard_hard_samples >= 3
                        else "corroborated sustained memory pressure"
                    )
                    detail_suffix = f"; {memory_guard_details}" if memory_guard_details else ""
                    message = (
                        f"\nDevtool stopped validation after {reason}: {available} MiB available; "
                        f"configured warning guard {memory_guard_mb} MiB; adaptive sustained threshold "
                        f"{memory_guard_sustained_limit} MiB; hard floor "
                        f"{memory_guard_hard_limit} MiB{detail_suffix}.\n"
                    )
                    for line in emit_and_track(message):
                        yield line
                    try:
                        os.killpg(proc.pid, 15)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=3.0)
                    except Exception:
                        try:
                            os.killpg(proc.pid, 9)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                    break

            if (
                completion_idle_grace is not None
                and completion_idle_grace > 0
                and completion_seen_at is not None
                and (time.monotonic() - last_output_at) >= completion_idle_grace
            ):
                assumed = 1 if completion_assumed_returncode is None else completion_assumed_returncode
                setattr(proc, "_devtool_assumed_returncode", assumed)
                message = (
                    f"\nDevtool stopped a quiet test process {completion_idle_grace:g}s after the final test summary. "
                    f"Assuming exit code {assumed}.\n"
                )
                for line in emit_and_track(message):
                    yield line
                try:
                    os.killpg(proc.pid, 15)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        os.killpg(proc.pid, 9)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                break

            if idle_timeout is not None and idle_timeout > 0 and (time.monotonic() - last_output_at) >= idle_timeout:
                message = f"\nDevtool stopped a quiet child process after {idle_timeout:g}s without output.\n"
                for line in emit_and_track(message):
                    yield line
                try:
                    os.killpg(proc.pid, 15)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        os.killpg(proc.pid, 9)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                break

        tail = decoder.decode(b"", final=True)
        if tail:
            for line in emit_and_track(tail):
                yield line
        if pending:
            yield pending
    finally:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        except Exception:
            pass


def _wait_process_returncode(proc: subprocess.Popen, *, timeout: float = 5.0) -> int:
    assumed = getattr(proc, "_devtool_assumed_returncode", None)
    try:
        code = proc.wait(timeout=timeout)
        if assumed is not None and code != 0:
            return int(assumed)
        return code
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, 15)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            code = proc.wait(timeout=2.0)
            if assumed is not None and code != 0:
                return int(assumed)
            return code
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                code = proc.wait(timeout=1.0)
                if assumed is not None and code != 0:
                    return int(assumed)
                return code
            except Exception:
                return int(assumed) if assumed is not None else 124

def _run_process_live(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    verbose: bool,
    command_name: str = "",
    tasker_progress: TaskerProgressSink | None = None,
    phase_index: int | None = None,
    phase_total: int | None = None,
    progress_argv: list[str] | None = None,
    progress_cwd: str | None = None,
    display_target: str = "",
    target_phase_index: int | None = None,
    target_phase_total: int | None = None,
    progress_parser: str = "",
    live_log_path: Path | str | None = None,
    live_log_header: str = "",
    memory_guard_mb: int = 0,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[int, str]:
    live_log_handle = None
    if live_log_path is not None:
        try:
            if live_log_header:
                write_log(live_log_header, live_log_path, append=True)
            live_log_handle = Path(live_log_path).open("a", encoding="utf-8")
        except OSError:
            live_log_handle = None

    def _append_live_log(line: str) -> None:
        if live_log_handle is None:
            return
        try:
            live_log_handle.write(redact_sensitive_text(line))
            live_log_handle.flush()
        except OSError:
            pass

    def _close_live_log() -> None:
        if live_log_handle is not None:
            with contextlib.suppress(Exception):
                live_log_handle.close()

    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        # Keep live streaming as the normal path, but return a structured failure
        # instead of crashing when the executable is unavailable.  This also keeps
        # older tests/wrappers that monkeypatch subprocess.run working.
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout or ""
            if live_log_handle is not None and output:
                _append_live_log(output)
            if verbose and output:
                print(output, end="", flush=True)
            _close_live_log()
            return completed.returncode, output
        except OSError:
            _append_live_log(str(exc))
            _close_live_log()
            return 127, str(exc)

    output_parts: list[str] = []
    assert proc.stdout is not None
    parser_argv = list(progress_argv or argv)
    parser_cwd = progress_cwd or cwd
    tracker = None
    if not verbose or tasker_progress is not None:
        if progress_parser == "gradle":
            tracker = GradleProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
        elif dotnet_progress_plan(parser_argv, parser_cwd):
            tracker = DotnetProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
        elif progress_parser == "npm" or npm_progress_plan(parser_argv, parser_cwd):
            tracker = NpmProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
        else:
            tracker = GenericProgressTracker(
                parser_argv,
                parser_cwd,
                command_name,
                console,
                phase_index=phase_index,
                phase_total=phase_total,
                display_target=display_target,
                target_phase_index=target_phase_index,
                target_phase_total=target_phase_total,
            )
    if tracker is not None and verbose:
        # In verbose mode stdout is already streamed as raw text. Keep the
        # parser active only for Tasker/progress events; do not render a second
        # Rich live display over the raw output.
        tracker.enabled = False
    if tracker is not None and (tasker_progress is not None or progress_callback is not None):
        def _progress_update(snapshot: dict) -> None:
            if tasker_progress is not None:
                phase = tasker_progress.current_phase or {}
                display_target_value = str(phase.get("target") or phase.get("workspace") or snapshot.get("target") or "")
                tasker_progress.progress(
                    detail=str(snapshot.get("detail") or ""),
                    completed=snapshot.get("completed"),
                    total=snapshot.get("total"),
                    percent=snapshot.get("percent"),
                    target=display_target_value,
                    warnings=snapshot.get("warnings"),
                    errors=snapshot.get("errors"),
                    metrics={
                        key: snapshot.get(key)
                        for key in (
                            "command", "unit_label", "tests_passed", "tests_failed",
                            "tests_skipped", "tests_total", "test_assemblies",
                            "current_task", "current_test",
                        )
                        if snapshot.get(key) is not None
                    },
                    force=bool(snapshot.get("force", False)),
                )
            if progress_callback is not None:
                try:
                    progress_callback(dict(snapshot))
                except Exception:
                    pass
        tracker.update_callback = _progress_update
    def _interrupt_process() -> None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, 2)
            else:
                proc.terminate()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    cancel_watch_stop = threading.Event()
    cancel_watcher: threading.Thread | None = None
    if tasker_progress is not None:
        def _watch_cancel() -> None:
            while not cancel_watch_stop.wait(0.1):
                if proc.poll() is not None:
                    return
                if tasker_progress.cancel_requested():
                    _interrupt_process()
                    return
        cancel_watcher = threading.Thread(target=_watch_cancel, name=f"devtool-cancel-{proc.pid}", daemon=True)
        cancel_watcher.start()

    try:
        idle_timeout = _idle_timeout_for(parser_argv, command_name)
        completion_idle_grace = _test_completion_grace_for(parser_argv, command_name)
        completion_detector = _dotnet_test_completion_status if completion_idle_grace is not None else None
        if tracker is None:
            for line in _iter_process_output_lines(
                proc,
                idle_timeout=idle_timeout,
                completion_idle_grace=completion_idle_grace,
                completion_status_detector=completion_detector,
                memory_guard_mb=memory_guard_mb,
                supervisor_probe=supervisor_probe,
                heartbeat_callback=heartbeat_callback,
            ):
                output_parts.append(line)
                _append_live_log(line)
                print(line, end="", flush=True)
            code = _wait_process_returncode(proc)
            if tasker_progress is not None and tasker_progress.cancel_requested():
                code = 130
            return code, "".join(output_parts)

        with tracker:
            for line in _iter_process_output_lines(
                proc,
                idle_timeout=idle_timeout,
                completion_idle_grace=completion_idle_grace,
                completion_status_detector=completion_detector,
                memory_guard_mb=memory_guard_mb,
                supervisor_probe=supervisor_probe,
                heartbeat_callback=heartbeat_callback,
            ):
                output_parts.append(line)
                _append_live_log(line)
                # Non-verbose mode intentionally keeps command stdout/stderr captured only.
                # Dotnet commands get a parsed Rich progress display instead of raw output.
                # Use --verbose when the full restore/build/test stream is needed live.
                if verbose:
                    print(line, end="", flush=True)
                tracker.update_from_line(line)
            returncode = _wait_process_returncode(proc)
            if tasker_progress is not None and tasker_progress.cancel_requested():
                returncode = 130
                output_parts.append("\nCancelled from Devtool Control Center. Child process was terminated.\n")
            tracker.finish(returncode)
        return returncode, "".join(output_parts)
    except KeyboardInterrupt:
        _interrupt_process()
        output_parts.append("\nInterrupted by user. Child process was terminated.\n")
        if tracker is not None:
            try:
                tracker.finish(130)
            except Exception:
                pass
        return 130, "".join(output_parts)
    finally:
        cancel_watch_stop.set()
        if cancel_watcher is not None and cancel_watcher is not threading.current_thread():
            cancel_watcher.join(timeout=0.25)
        if tasker_progress is not None:
            tasker_progress.clear_cancel()
        _close_live_log()
        with contextlib.suppress(Exception):
            proc.stdout.close()


def _shutdown_gradle_daemons(
    config: ProjectConfig,
    *,
    project_root: str,
    dry_run: bool,
    log_path: Path | str | None,
) -> tuple[OperationResult, str]:
    root = Path(project_root or config.project_root or config.repo_root).resolve()
    wrapper = root / "gradlew"
    argv = [str(wrapper), "--stop"] if wrapper.is_file() else ["gradle", "--stop"]
    command_line = "$ " + " ".join(shlex.quote(value) for value in argv)
    if dry_run:
        output = "Gradle daemon shutdown skipped during dry-run."
        log_file = str(write_log(command_line + "\n" + output, log_path, append=log_path is not None))
        return OperationResult("gradle-daemon-shutdown", argv, 0, 0.0, output, [], skipped=True, log_path=log_file, metadata={"cleanup": True}), command_line + "\n" + output
    started = time.perf_counter()
    env = os.environ.copy()
    env.update(config.env)
    try:
        completed = subprocess.run(argv, cwd=str(root), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=30)
        code = completed.returncode
        output = completed.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        code = 127
        output = str(exc)
    raw = command_line + ("\n" + output if output else "")
    log_file = str(write_log(raw, log_path, append=log_path is not None))
    return OperationResult(
        "gradle-daemon-shutdown", argv, code, time.perf_counter() - started, output,
        parse_issues(config.repo_root, "gradle-daemon-shutdown", output),
        log_path=log_file, failure_kind="cleanup_failure" if code else "", metadata={"cleanup": True},
    ), raw


def _shutdown_dotnet_build_server(
    config: ProjectConfig,
    *,
    dry_run: bool,
    log_path: Path | str | None,
) -> tuple[OperationResult, str]:
    argv, cleanup_dir = _prepare_termux_dotnet(["dotnet", "build-server", "shutdown"])
    command_line = "$ " + " ".join(shlex.quote(value) for value in argv)
    if dry_run:
        raw = command_line + "\nBuild-server shutdown skipped during dry-run."
        log_file = str(write_log(raw, log_path, append=log_path is not None))
        return OperationResult(
            "dotnet-build-server-shutdown", argv, 0, 0.0, raw.split("\n", 1)[1], [],
            skipped=True, log_path=log_file, metadata={"cleanup": True},
        ), raw

    env = os.environ.copy()
    env.update(config.env)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(config.repo_root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        returncode = completed.returncode
        output = completed.stdout or ""
    except OSError as exc:
        returncode = 127
        output = str(exc)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
    duration = time.perf_counter() - started
    issues = parse_issues(config.repo_root, "dotnet-build-server-shutdown", output)
    raw = command_line + ("\n" + output if output else "")
    log_file = str(write_log(raw, log_path, append=log_path is not None))
    result = OperationResult(
        "dotnet-build-server-shutdown",
        argv,
        returncode,
        duration,
        output,
        issues,
        log_path=log_file,
        failure_kind="cleanup_failure" if returncode else "",
        metadata={"cleanup": True},
    )
    return result, raw


def _dotnet_target_argument(argv: Sequence[str], cwd: str) -> str:
    command_index: int | None = None
    for index, value in enumerate(argv):
        if Path(str(value)).name == "dotnet":
            command_index = index
            break
    if command_index is None or command_index + 1 >= len(argv):
        return ""
    skip_next = False
    value_options = {
        "--framework", "-f", "--runtime", "-r", "--configuration", "-c",
        "--output", "-o", "--logger", "-l", "--verbosity", "-v",
    }
    for value in argv[command_index + 2 :]:
        text = str(value)
        if skip_next:
            skip_next = False
            continue
        if text in value_options:
            skip_next = True
            continue
        if text.startswith("-"):
            continue
        return text
    return cwd


def _dotnet_display_target(spec: CommandSpec, config: ProjectConfig) -> str:
    for key in ("bundle_target", "matrix", "workspace", "devtool_target"):
        value = str(spec.metadata.get(key) or "").strip()
        if value:
            return value
    if config.selected_target:
        return config.selected_target
    progress_argv = list(spec.metadata.get("progress_argv") or spec.argv)
    progress_cwd = str(spec.metadata.get("progress_cwd") or spec.cwd or config.repo_root)
    target_text = _dotnet_target_argument(progress_argv, progress_cwd)
    if not target_text:
        return "repository"
    target = Path(target_text)
    if not target.is_absolute():
        target = Path(progress_cwd) / target
    try:
        return str(target.resolve().relative_to(config.repo_root.resolve())).replace(os.sep, "/")
    except (OSError, ValueError):
        return target.name or target_text


def _dotnet_phase_layout(specs: Sequence[CommandSpec], config: ProjectConfig) -> dict[int, tuple[str, int, int]]:
    targets: dict[int, str] = {}
    totals: Counter[str] = Counter()
    for index, spec in enumerate(specs):
        progress_argv = list(spec.metadata.get("progress_argv") or spec.argv)
        if not _contains_dotnet_cli(progress_argv):
            continue
        target = _dotnet_display_target(spec, config) or "repository"
        targets[index] = target
        totals[target] += 1

    seen: defaultdict[str, int] = defaultdict(int)
    layout: dict[int, tuple[str, int, int]] = {}
    for index, target in targets.items():
        seen[target] += 1
        layout[index] = (target, seen[target], totals[target])
    return layout


def dotnet_build_server_shutdown_enabled(config: ProjectConfig) -> bool:
    """Return whether devtool should stop shared .NET build servers after a run."""
    value = config.runner_options.get("dotnet", {})
    options = dict(value) if isinstance(value, dict) else {}
    return bool(options.get("stop_after_run", False))


def run_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    verbose: bool,
    dry_run: bool = False,
    continue_on_error: bool = False,
    log_path: Path | str | None = None,
    tasker_progress: TaskerProgressSink | None = None,
    shutdown_dotnet: bool | None = None,
    supervisor_probe: Callable[[], tuple[str, str] | dict | str | None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[list[OperationResult], str]:
    results: list[OperationResult] = []
    raw_sections: list[str] = []
    used_dotnet = any(_contains_dotnet_cli(spec.argv) for spec in specs)
    stop_dotnet_after_run = (
        dotnet_build_server_shutdown_enabled(config)
        if shutdown_dotnet is None
        else bool(shutdown_dotnet)
    )
    used_gradle = any(
        str(spec.metadata.get("progress_parser") or "") == "gradle"
        or any(Path(str(value)).name in {"gradle", "gradlew"} for value in spec.argv)
        for spec in specs
    )
    gradle_project_root = next((
        str(spec.metadata.get("project_root") or spec.cwd or config.project_root or config.repo_root)
        for spec in specs
        if str(spec.metadata.get("progress_parser") or "") == "gradle"
           or any(Path(str(value)).name in {"gradle", "gradlew"} for value in spec.argv)
    ), str(config.project_root or config.repo_root))
    dotnet_layout = _dotnet_phase_layout(specs, config)

    if tasker_progress is not None and not dry_run:
        tasker_progress.plan_phases([
            {
                "name": spec.name,
                "target": str(spec.metadata.get("bundle_target") or spec.metadata.get("matrix") or ""),
                "workspace": str(spec.metadata.get("workspace") or ""),
                "workspace_root": str(spec.metadata.get("workspace_root") or ""),
            }
            for spec in specs
        ])

    for spec_index, spec in enumerate(specs, start=1):
        effective_argv, cleanup_dir = _prepare_termux_dotnet(spec.argv)
        display_target, target_phase_index, target_phase_total = dotnet_layout.get(
            spec_index - 1, ("", None, None)
        )
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_started(spec.name, index=spec_index, total=len(specs), argv=effective_argv, metadata=spec.metadata)

        if dry_run:
            output = "$ " + " ".join(effective_argv)
            log_file = str(write_log(output, log_path, append=log_path is not None))
            results.append(OperationResult(spec.name, effective_argv, 0, 0.0, output, [], skipped=True, log_path=log_file, metadata=dict(spec.metadata)))
            raw_sections.append(output)
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            continue

        env = os.environ.copy()
        env.update(config.env)
        env.update(spec.env)

        started = time.perf_counter()
        started_wall = time.time()
        returncode = 1
        output = ""
        process_output = ""
        command_line = "$ " + " ".join(shlex.quote(a) for a in effective_argv)
        try:
            cwd = spec.cwd or str(config.repo_root)
            progress_argv = list(spec.metadata.get("progress_argv") or effective_argv)
            progress_cwd = str(spec.metadata.get("progress_cwd") or cwd)
            progress_parser = str(spec.metadata.get("progress_parser") or "")
            memory_guard_mb = int(
                spec.metadata.get("validation_memory_guard_mb")
                or spec.metadata.get("memory_guard_mb")
                or 0
            )
            if memory_guard_mb > 0 and _command_delegates_memory_guard(effective_argv):
                # The Android builder monitors the actual Gradle child itself.
                # A second outer watchdog would sample the same kernel state,
                # reclaim caches twice, and race the inner adaptive policy.
                memory_guard_mb = 0
            if verbose:
                console.print(f"[cyan]$ {' '.join(shlex.quote(a) for a in effective_argv)}[/cyan]")
                returncode, output = _run_process_live(
                    effective_argv,
                    cwd=cwd,
                    env=env,
                    verbose=True,
                    command_name=spec.name,
                    tasker_progress=tasker_progress,
                    phase_index=spec_index,
                    phase_total=len(specs),
                    progress_argv=progress_argv,
                    progress_cwd=progress_cwd,
                    display_target=display_target,
                    target_phase_index=target_phase_index,
                    target_phase_total=target_phase_total,
                    progress_parser=progress_parser,
                    live_log_path=log_path,
                    live_log_header=command_line + "\n" if log_path is not None else "",
                    memory_guard_mb=memory_guard_mb,
                    supervisor_probe=supervisor_probe,
                    heartbeat_callback=heartbeat_callback,
                    progress_callback=progress_callback,
                )
            else:
                if progress_parser == "gradle" or dotnet_progress_plan(progress_argv, progress_cwd):
                    returncode, output = _run_process_live(
                        effective_argv,
                        cwd=cwd,
                        env=env,
                        verbose=False,
                        command_name=spec.name,
                        tasker_progress=tasker_progress,
                        phase_index=spec_index,
                        phase_total=len(specs),
                        progress_argv=progress_argv,
                        progress_cwd=progress_cwd,
                        display_target=display_target,
                        target_phase_index=target_phase_index,
                        target_phase_total=target_phase_total,
                        progress_parser=progress_parser,
                        live_log_path=log_path,
                        live_log_header=command_line + "\n" if log_path is not None else "",
                        memory_guard_mb=memory_guard_mb,
                        supervisor_probe=supervisor_probe,
                        heartbeat_callback=heartbeat_callback,
                        progress_callback=progress_callback,
                    )
                else:
                    with console.status(f"[bold cyan]Running {spec.name}[/bold cyan]", spinner="dots"):
                        returncode, output = _run_process_live(
                            effective_argv,
                            cwd=cwd,
                            env=env,
                            verbose=False,
                            command_name=spec.name,
                            tasker_progress=tasker_progress,
                            phase_index=spec_index,
                            phase_total=len(specs),
                            progress_argv=progress_argv,
                            progress_cwd=progress_cwd,
                            progress_parser=progress_parser,
                            live_log_path=log_path,
                            live_log_header=command_line + "\n" if log_path is not None else "",
                            memory_guard_mb=memory_guard_mb,
                            supervisor_probe=supervisor_probe,
                            heartbeat_callback=heartbeat_callback,
                            progress_callback=progress_callback,
                        )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        process_output = output
        duration = time.perf_counter() - started
        issues = parse_issues(config.repo_root, spec.name, output)
        result_metadata = dict(spec.metadata)
        configured_report_parsers = result_metadata.get("report_parsers")
        if isinstance(configured_report_parsers, (list, tuple)):
            report_parsers = [str(value).strip() for value in configured_report_parsers if str(value).strip()]
        else:
            report_parser = str(result_metadata.get("report_parser") or "").strip()
            report_parsers = [report_parser] if report_parser else []
        report_parsers = list(dict.fromkeys(report_parsers))
        nonfatal_report_issues: list[Issue] = []
        if report_parsers:
            report_root = Path(str(result_metadata.get("project_root") or spec.cwd or config.repo_root)).resolve()
            for report_parser in report_parsers:
                collection = collect_report_metadata(
                    report_root,
                    repo_root=config.repo_root,
                    parser=report_parser,
                    tasks=result_metadata.get("gradle_tasks") or (),
                    started_at=started_wall,
                    command_name=spec.name,
                    output=output,
                    report_path=str(
                        result_metadata.get("coverage_report")
                        if report_parser == "python-coverage"
                        else result_metadata.get("test_report") or result_metadata.get("package_report") or ""
                    ),
                )
                if report_parser in {"gradle-junit", "pytest-junit", "android-instrumentation"}:
                    tests = dict(collection.metadata)
                    catalog_discovered = result_metadata.get("test_catalog_discovered")
                    catalog_selected = result_metadata.get("test_catalog_selected")
                    if catalog_discovered is not None:
                        tests["discovered"] = int(catalog_discovered)
                    if catalog_selected is not None:
                        tests["selected"] = int(catalog_selected)
                    result_metadata["tests"] = tests
                    if tests.get("report_files") or report_parser == "android-instrumentation":
                        test_prefix = "Devtool Gradle tests: " if report_parser == "gradle-junit" else f"Devtool tests ({tests.get('framework', report_parser)}): "
                        output = (output.rstrip() + "\n" if output else "") + (
                            test_prefix
                            + f"{tests.get('passed', 0)} passed, "
                            + f"{tests.get('failed', 0)} failed, "
                            + f"{tests.get('skipped', 0)} skipped, "
                            + f"{tests.get('total', 0)} total.\n"
                        )
                    try:
                        from .test_selection import save_failed_test_ids
                        failed_ids = [issue.message.split(":", 1)[0] for issue in collection.issues if issue.code in {"GRADLE_TEST", "ANDROID_INSTRUMENTATION"}]
                        save_failed_test_ids(config.repo_root, failed_ids)
                    except Exception:
                        pass
                elif report_parser == "android-lint":
                    result_metadata["lint"] = collection.metadata
                    if collection.metadata.get("report_files"):
                        output = (output.rstrip() + "\n" if output else "") + (
                            "Devtool Android Lint: "
                            f"{collection.metadata.get('errors', 0)} errors, "
                            f"{collection.metadata.get('warnings', 0)} warnings.\n"
                        )
                    nonfatal_report_issues.extend(collection.issues)
                elif report_parser == "python-package":
                    result_metadata["packages"] = collection.metadata
                    output = (output.rstrip() + "\n" if output else "") + (
                        "Devtool Python packages: "
                        f"{collection.metadata.get('valid', 0)} valid, "
                        f"{collection.metadata.get('invalid', 0)} invalid.\n"
                    )
                elif report_parser == "python-tool":
                    result_metadata.setdefault("python_tools", []).append(collection.metadata)
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python {collection.metadata.get('tool', 'tool')}: "
                            f"{collection.metadata.get('errors', 0)} errors, "
                            f"{collection.metadata.get('warnings', 0)} warnings.\n"
                        )
                elif report_parser == "python-audit":
                    result_metadata["python_audit"] = collection.metadata
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python source audit: {collection.metadata.get('errors', 0)} issue(s).\n"
                        )
                elif report_parser == "python-coverage":
                    result_metadata["coverage"] = collection.metadata
                    if collection.metadata:
                        output = (output.rstrip() + "\n" if output else "") + (
                            f"Devtool Python coverage: {collection.metadata.get('line_percent', 0):.2f}%.\n"
                        )
                issues.extend(collection.issues)


        if bool(result_metadata.get("collect_artifacts") or result_metadata.get("verify_artifacts")) and config.kind in {"android", "gradle"}:
            from .android_artifacts import collect_and_verify_android_artifacts
            artifact_root = Path(str(result_metadata.get("project_root") or spec.cwd or config.repo_root)).resolve()
            artifact_metadata, artifact_issues = collect_and_verify_android_artifacts(
                config,
                project_root=artifact_root,
                started_at=started_wall,
                tasks=result_metadata.get("gradle_tasks") or (),
            )
            result_metadata["artifacts"] = artifact_metadata
            issues.extend(artifact_issues)
            output = (output.rstrip() + "\n" if output else "") + (
                "Devtool artifacts: "
                f"{artifact_metadata.get('valid', 0)} valid, "
                f"{artifact_metadata.get('invalid', 0)} invalid, "
                f"{artifact_metadata.get('count', 0)} total.\n"
            )
            if artifact_issues:
                failures = [issue for issue in artifact_issues if issue.severity == "error"]
                warnings = [issue for issue in artifact_issues if issue.severity == "warning"]
                if failures:
                    output += "Devtool artifact verification failures:\n"
                    for issue in failures[:8]:
                        output += f"- {issue.file} [{issue.code}]: {issue.message}\n"
                    if len(failures) > 8:
                        output += f"- ... and {len(failures) - 8} more failure(s).\n"
                if warnings:
                    output += "Devtool artifact verification notices:\n"
                    for issue in warnings[:8]:
                        output += f"- {issue.file} [{issue.code}]: {issue.message}\n"
                    if len(warnings) > 8:
                        output += f"- ... and {len(warnings) - 8} more notice(s).\n"
        nonfatal_issue_ids = {id(issue) for issue in nonfatal_report_issues}
        has_fatal_error_diagnostics = any(
            issue.severity == "error" and id(issue) not in nonfatal_issue_ids
            for issue in issues
        )
        if returncode == 0 and has_fatal_error_diagnostics:
            output = (output.rstrip() + "\n" if output else "") + (
                "Devtool detected error diagnostics even though the process exited 0; "
                "treating this command as failed.\n"
            )
            returncode = 1
        supervisor_events = []
        for match in re.finditer(
            r"Devtool validation supervisor stopped process:\s*([a-z-]+):\s*(.+?)(?:\.|$)",
            output,
            re.IGNORECASE,
        ):
            supervisor_events.append({"kind": match.group(1).lower(), "reason": match.group(2).strip()})
        if supervisor_events:
            result_metadata["supervisor_events"] = supervisor_events
            result_metadata["supervisor_reason"] = supervisor_events[-1]["reason"]
            kind = supervisor_events[-1]["kind"]
            result_metadata["resource_guard_triggered"] = kind == "resource"
            result_metadata["timed_out"] = kind == "timeout"
        failure_kind = classify_failure(spec.name, output, returncode)
        if supervisor_events:
            failure_kind = {
                "resource": "resource_interrupted",
                "timeout": "command_timeout",
                "interrupted": "interrupted",
                "environment": "environment_failure",
            }.get(supervisor_events[-1]["kind"], failure_kind)
        raw_text = command_line + "\n" + output
        if log_path is not None:
            if output != process_output:
                extra_output = output[len(process_output):] if output.startswith(process_output) else output
                if extra_output:
                    write_log(extra_output, log_path, append=True)
            log_file = str(log_path)
        else:
            log_file = str(write_log(raw_text, log_path, append=False))
        result = OperationResult(spec.name, effective_argv, returncode, duration, output, issues, log_path=log_file, failure_kind=failure_kind, metadata=result_metadata)
        results.append(result)
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_finished(result)
        raw_sections.append(raw_text)

        if returncode != 0 and not continue_on_error:
            break

    gradle_options = config.runner_options.get("gradle", {})
    gradle_options = dict(gradle_options) if isinstance(gradle_options, dict) else {}
    stop_gradle_after_run = bool(gradle_options.get("stop_after_run", False))
    if used_gradle and stop_gradle_after_run:
        gradle_result, gradle_raw = _shutdown_gradle_daemons(
            config, project_root=gradle_project_root, dry_run=dry_run, log_path=log_path
        )
        results.append(gradle_result)
        raw_sections.append(gradle_raw)

    if used_dotnet and stop_dotnet_after_run:
        shutdown_result, shutdown_raw = _shutdown_dotnet_build_server(
            config,
            dry_run=dry_run,
            log_path=log_path,
        )
        results.append(shutdown_result)
        raw_sections.append(shutdown_raw)

    return results, "\n\n".join(raw_sections)

def shutdown_dotnet_build_server(
    config: ProjectConfig,
    *,
    dry_run: bool = False,
    log_path: Path | str | None = None,
) -> tuple[OperationResult, str]:
    """Stop shared .NET build servers once at an outer workflow boundary."""
    return _shutdown_dotnet_build_server(config, dry_run=dry_run, log_path=log_path)


def _artifact_config_declaration(manifest: dict) -> dict[str, str]:
    return {
        "target": artifact_target(manifest),
        "solution": artifact_solution(manifest),
        "project": str(manifest.get("run_project") or manifest.get("project") or "").strip(),
    }


def _declaration_has_dotnet_target(declaration: dict[str, str]) -> bool:
    return bool(declaration.get("solution") or declaration.get("project"))


def _preview_with_config_registration(preview: dict, declarations: Sequence[dict[str, str]]) -> dict:
    if not any(_declaration_has_dotnet_target(item) for item in declarations):
        return preview
    updated = dict(preview)
    original_changes = [dict(item) for item in preview.get("changes", []) if isinstance(item, dict)]
    already_listed = any(str(item.get("path") or "") == ".devtool.toml" for item in original_changes)
    if not already_listed:
        original_changes.append({"path": ".devtool.toml", "kind": "modify"})
    updated["changes"] = original_changes
    if not already_listed:
        counts = dict(preview.get("counts", {}))
        counts["modify"] = int(counts.get("modify", 0) or 0) + 1
        updated["counts"] = counts
    return updated


def _register_artifact_config(repo_root: Path, manifest: dict, *, target_override: str = "") -> dict[str, str]:
    declaration = _artifact_config_declaration(manifest)
    if target_override.strip():
        declaration["target"] = target_override.strip()
    if not _declaration_has_dotnet_target(declaration):
        return {}
    return ensure_artifact_target_config(repo_root, **declaration)


def _bundle_config_declarations(bundle: Path) -> list[dict[str, str]]:
    declarations: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-config-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            declaration = _artifact_config_declaration(read_artifact_manifest(entry.path))
            if entry.target:
                declaration["target"] = entry.target
            if _declaration_has_dotnet_target(declaration):
                declarations.append(declaration)
    return declarations


def apply_overlay(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False, log_path: Path | str | None = None, preview: dict | None = None) -> OperationResult:
    artifact_path = resolve_artifact_path(artifact)
    if is_overlay_bundle(artifact_path):
        return apply_overlay_bundle(repo_root, artifact, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview)
    argv = ["devtool", "extract-overlay", str(artifact_path), "-C", str(repo_root)]
    return _apply_archive(repo_root, artifact_path, argv, delete_artifact=True, snapshot=False, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview)


def apply_overlay_bundle(
    repo_root: Path,
    artifact: str | Path | Sequence[str | Path],
    *,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    preview: dict | None = None,
    source_artifacts: Sequence[str | Path] | None = None,
) -> OperationResult:
    raw_artifacts = [artifact] if isinstance(artifact, (str, Path)) else list(artifact)
    if not raw_artifacts:
        output = "No overlay artifacts were provided."
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("apply-overlays", ["devtool", "apply-overlays"], 1, 0.0, output, [], log_path=path)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if len(raw_artifacts) == 1:
            artifact_path = resolve_artifact_path(raw_artifacts[0])
            original_paths = [artifact_path]
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="devtool-adhoc-overlays-")
            original_paths = [resolve_artifact_path(item) for item in raw_artifacts]
            artifact_path = create_overlay_bundle(Path(temp_dir.name) / "adhoc_overlay_bundle.zip", original_paths, name="adhoc_overlay_bundle")
        argv = ["devtool", "apply-overlays", *(str(item) for item in original_paths), "-C", str(repo_root)]
        start = time.perf_counter()
        if dry_run:
            text = "$ " + " ".join(argv)
            path = str(write_log(text, log_path, append=log_path is not None))
            return OperationResult("apply-overlays", argv, 0, 0.0, text, [], skipped=True, log_path=path)
        if not artifact_path.exists():
            output = f"Artifact not found: {artifact_path}"
            path = str(write_log(output, log_path, append=log_path is not None))
            return OperationResult("apply-overlays", argv, 1, 0.0, output, [], log_path=path)

        transaction: dict | None = None
        rollback_result: OperationResult | None = None
        try:
            declarations = _bundle_config_declarations(artifact_path)
            preview_data = _preview_with_config_registration(
                preview or preview_overlay_bundle(repo_root, artifact_path),
                declarations,
            )
            transaction = create_apply_transaction(
                repo_root,
                artifact_path,
                artifact_type="overlay_bundle",
                preview=preview_data,
            )
            skipped_metadata = 0
            deleted_paths: list[str] = []
            applied: list[str] = []
            registrations: list[dict[str, str]] = []
            with tempfile.TemporaryDirectory(prefix="devtool-bundle-apply-") as tmp:
                _manifest, entries = materialize_overlay_bundle_entries(artifact_path, tmp)
                for entry in entries:
                    validation = validate_artifact(repo_root, entry.path, artifact_type="overlay", check_source_state=True)
                    if not validation.ok:
                        label = entry.label or Path(entry.bundle_path).name
                        raise RuntimeError(f"Nested overlay validation failed for {label}: " + "; ".join(validation.messages))
                    skipped_metadata += _extract_archive(entry.path, repo_root)
                    deleted_paths.extend(_apply_delete_manifest(repo_root, entry.path))
                    registration = _register_artifact_config(repo_root, validation.manifest, target_override=entry.target)
                    if registration:
                        registrations.append(registration)
                    label = entry.label or Path(entry.bundle_path).name
                    target_note = f" target={entry.target}" if entry.target else ""
                    applied.append(f"[{entry.index}/{len(entries)}] {label}{target_note}")
            output = f"Applied overlay bundle from {artifact_path}"
            output += f"\nTransaction backup: {transaction.get('transaction_id')}"
            if applied:
                output += "\nApplied overlays:\n" + "\n".join(applied)
            if skipped_metadata:
                output += f"\nSkipped {skipped_metadata} devtool metadata file(s)."
            if deleted_paths:
                output += f"\nDeleted {len(deleted_paths)} path(s) from nested .devtool-delete file(s)."
            if registrations:
                output += f"\nRegistered {len(registrations)} target(s) in .devtool.toml."
            rc = 0
        except KeyboardInterrupt:
            rc = 130
            output = "Interrupted during overlay bundle apply."
            if transaction:
                rollback_result = rollback_transaction(transaction.get("path"), reason="interrupted during overlay bundle apply")
                output += "\n" + rollback_result.output
        except BaseException as exc:
            output = f"{type(exc).__name__}: {exc}"
            rc = 1
            if transaction:
                rollback_result = rollback_transaction(transaction.get("path"), reason="error during overlay bundle apply")
                output += "\n" + rollback_result.output
                if rollback_result.returncode != 0:
                    output += "\nRollback failed; manual intervention may be required."
        duration = time.perf_counter() - start
        path = str(write_log(output, log_path, append=log_path is not None))
        metadata = {}
        if transaction:
            tx_id = str(transaction.get("transaction_id") or "")
            tx_path = str(transaction.get("path") or "") if tx_id else ""
            cleanup_sources = [str(resolve_artifact_path(item)) for item in (source_artifacts or original_paths)]
            metadata = {
                "transaction_id": tx_id,
                "transaction_path": tx_path,
                "artifact_path": str(cleanup_sources[0] if len(cleanup_sources) == 1 else artifact_path),
                "artifact_paths": cleanup_sources,
                "delete_artifact_on_success": True,
                "artifact_type": "overlay_bundle",
                "config_paths": sorted({item.get("path", "") for item in registrations if item.get("path")}),
            }
            if rollback_result is not None:
                metadata["rollback_returncode"] = rollback_result.returncode
                metadata["rolled_back_during_apply"] = True
        return OperationResult("apply-overlays", argv, rc, duration, output, [], log_path=path, metadata=metadata)
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def apply_snapshot(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False, log_path: Path | str | None = None, preview: dict | None = None) -> OperationResult:
    artifact_path = resolve_artifact_path(artifact)
    return _apply_archive(repo_root, artifact_path, ["snapshot", str(artifact_path)], delete_artifact=True, snapshot=True, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview)


def _apply_archive(repo_root: Path, artifact_path: Path, argv: list[str], *, delete_artifact: bool, snapshot: bool, force: bool, yes: bool, dry_run: bool, log_path: Path | str | None = None, preview: dict | None = None) -> OperationResult:
    start = time.perf_counter()
    if dry_run:
        text = "$ " + " ".join(argv)
        path = str(write_log(text, log_path, append=log_path is not None))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 0, 0.0, text, [], skipped=True, log_path=path)

    if not artifact_path.exists():
        output = f"Artifact not found: {artifact_path}"
        path = str(write_log(output, log_path, append=log_path is not None))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 1, 0.0, output, [], log_path=path)

    transaction: dict | None = None
    rollback_result: OperationResult | None = None
    registration: dict[str, str] = {}
    try:
        manifest = read_artifact_manifest(artifact_path) if not snapshot else {}
        declarations = [_artifact_config_declaration(manifest)] if manifest else []
        preview_data = _preview_with_config_registration(
            preview or preview_artifact(repo_root, artifact_path, snapshot=snapshot),
            declarations,
        )
        transaction = create_apply_transaction(
            repo_root,
            artifact_path,
            artifact_type="snapshot" if snapshot else "overlay",
            preview=preview_data,
        )
        skipped_metadata = 0
        deleted_paths: list[str] = []
        if snapshot:
            tmpdir = Path(tempfile.mkdtemp(prefix="devtool-snapshot-"))
            try:
                skipped_metadata += _extract_archive(artifact_path, tmpdir)
                _sync_snapshot(tmpdir, repo_root)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            skipped_metadata += _extract_archive(artifact_path, repo_root)
            deleted_paths = _apply_delete_manifest(repo_root, artifact_path)
            registration = _register_artifact_config(repo_root, manifest)
        # The original artifact is intentionally not deleted here.  Apply is now
        # transactional across extraction plus any later validation/commit steps,
        # so cleanup is deferred until the CLI knows no rollback happened.
        output = f"Applied {'snapshot' if snapshot else 'overlay'} from {artifact_path}"
        output += f"\nTransaction backup: {transaction.get('transaction_id')}"
        if skipped_metadata:
            output += f"\nSkipped {skipped_metadata} devtool metadata file(s)."
        if deleted_paths:
            output += f"\nDeleted {len(deleted_paths)} path(s) from .devtool-delete."
        if registration:
            output += f"\n{registration.get('action', 'updated').title()} .devtool.toml target: {registration.get('target', '')}."
        rc = 0
    except KeyboardInterrupt as exc:
        rc = 130
        output = "Interrupted during artifact apply."
        if transaction:
            rollback_result = rollback_transaction(transaction.get("path"), reason="interrupted during artifact apply")
            output += "\n" + rollback_result.output
    except BaseException as exc:
        output = f"{type(exc).__name__}: {exc}"
        rc = 1
        if transaction:
            rollback_result = rollback_transaction(transaction.get("path"), reason="error during artifact apply")
            output += "\n" + rollback_result.output
            if rollback_result.returncode != 0:
                output += "\nRollback failed; manual intervention may be required."
    duration = time.perf_counter() - start
    path = str(write_log(output, log_path, append=log_path is not None))
    metadata = {}
    if transaction:
        tx_id = str(transaction.get("transaction_id") or "")
        tx_path = str(transaction.get("path") or "") if tx_id else ""
        metadata = {
            "transaction_id": tx_id,
            "transaction_path": tx_path,
            "artifact_path": str(artifact_path),
            "delete_artifact_on_success": bool(delete_artifact),
            "config_paths": [registration.get("path", "")] if registration.get("path") else [],
        }
        if rollback_result is not None:
            metadata["rollback_returncode"] = rollback_result.returncode
            metadata["rolled_back_during_apply"] = True
    return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, rc, duration, output, [], log_path=path, metadata=metadata)



def _artifact_sidecars(artifact_path: Path) -> list[Path]:
    return [
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ]

def _extract_archive(artifact_path: Path, target: Path) -> int:
    suffix = archive_suffix(artifact_path)
    if suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as zf:
            return _safe_extract_zip(zf, target)
    if suffix and suffix != ".zip":
        with open_tar_archive(artifact_path) as tf:
            return _safe_extract_tar(tf, target)
    raise RuntimeError(f"Unsupported artifact type: {artifact_path}")


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> int:
    target = target.resolve()
    skipped_metadata = 0
    for member in archive.infolist():
        mode = (member.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise RuntimeError(f"Unsafe zip symlink member: {member.filename}")
        if is_artifact_metadata_member(member.filename):
            skipped_metadata += 1
            continue
        member_path = _safe_destination_path(target, Path(member.filename))
        if member.is_dir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue
        member_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member, "r") as source, member_path.open("wb") as dest:
            shutil.copyfileobj(source, dest)
    return skipped_metadata


def _safe_extract_tar(archive: tarfile.TarFile, target: Path) -> int:
    target = target.resolve()
    skipped_metadata = 0
    for member in archive.getmembers():
        if is_artifact_metadata_member(member.name):
            skipped_metadata += 1
            continue
        if member.islnk() or member.issym() or member.isdev():
            raise RuntimeError(f"Unsafe tar member: {member.name}")
        member_path = _safe_destination_path(target, Path(member.name))
        if member.isdir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise RuntimeError(f"Unsupported tar member type: {member.name}")
        member_path.parent.mkdir(parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"Failed to extract tar member: {member.name}")
        with extracted, member_path.open("wb") as dest:
            shutil.copyfileobj(extracted, dest)
    return skipped_metadata


def _safe_destination_path(base: Path, member_path: Path) -> Path:
    if member_path.is_absolute():
        raise RuntimeError(f"Unsafe archive path: {member_path}")
    resolved = (base / member_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe archive path: {member_path}") from exc
    return resolved



def _apply_delete_manifest(repo_root: Path, artifact_path: Path) -> list[str]:
    deleted: list[str] = []
    root = repo_root.resolve()
    for raw_entry in read_delete_manifest(artifact_path):
        target = _safe_destination_path(root, Path(raw_entry))
        if target == root:
            raise RuntimeError("Refusing to delete repo root from .devtool-delete")
        if target.is_dir():
            shutil.rmtree(target)
            deleted.append(raw_entry)
        elif target.exists() or target.is_symlink():
            target.unlink()
            deleted.append(raw_entry)
    return deleted

def _sync_snapshot(snapshot_root: Path, repo_root: Path) -> None:
    existing = {p.relative_to(repo_root) for p in repo_root.rglob("*") if ".git" not in p.parts}
    incoming = {p.relative_to(snapshot_root) for p in snapshot_root.rglob("*")}
    for rel in sorted(existing - incoming, reverse=True):
        target = repo_root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    for path in sorted(snapshot_root.rglob("*")):
        rel = path.relative_to(snapshot_root)
        target = repo_root / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _normalize_git_pathspecs(paths: list[str] | tuple[str, ...] | None) -> list[str] | None:
    """Return safe repo-relative pathspecs for artifact-scoped commits.

    ``None`` preserves the old behavior for the explicit ``devtool commit`` command:
    stage the whole worktree. A concrete list means "only stage these paths".
    """
    if paths is None:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw).strip().replace("\\", "/")
        if not value or value == ".":
            continue
        if value.startswith("/") or value.startswith("../") or "/../" in value:
            raise ValueError(f"Refusing unsafe git pathspec: {raw!r}")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def git_commit(
    repo_root: Path,
    message: str,
    *,
    verbose: bool = False,
    dry_run: bool = False,
    log_path: Path | str | None = None,
    paths: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[OperationResult], str]:
    pathspecs = _normalize_git_pathspecs(paths)

    if pathspecs is None:
        add_argv = ["git", "add", "-A"]
    elif pathspecs:
        # Artifact-scoped commits must be able to add payload files that the
        # repository normally ignores (for example generated bridge binaries).
        # The caller supplies the exact artifact-owned path list, so force only
        # those paths instead of widening the commit to the whole worktree.
        add_argv = ["git", "add", "-f", "-A", "--", *pathspecs]
    else:
        output = "No artifact paths changed; skipped git add and git commit."
        results = [
            OperationResult("git-add-skipped", ["git", "add", "-A", "--"], 0, 0.0, output, [], skipped=True),
            OperationResult("git-commit-skipped", ["git", "commit", "-m", message], 0, 0.0, output, [], skipped=True),
        ]
        return results, output

    specs = [
        CommandSpec("git-add", add_argv),
        CommandSpec("git-commit", ["git", "commit", "-m", message]),
    ]
    config = ProjectConfig(repo_root=repo_root, kind="unknown")
    return run_specs(config, specs, verbose=verbose, dry_run=dry_run, log_path=log_path)


def write_template_config(repo_root: Path, *, force: bool = False) -> Path:
    repo_root = repo_root.expanduser().resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    target = repo_root / ".devtool.toml"
    if target.exists() and not force:
        raise SystemExit(f"{target} already exists. Use --force to overwrite.")
    target.write_text(generate_template(repo_root), encoding="utf-8")
    return target
