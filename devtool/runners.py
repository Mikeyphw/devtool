from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path


from .artifacts import is_artifact_metadata_member, read_delete_manifest, preview_artifact
from .config import generate_template
from .console_utils import create_console, is_termux_environment, resolve_cli_path
from .diagnostics import classify_failure, parse_issues
from .dotnet_progress import DotnetProgressTracker, dotnet_progress_plan
from .transactions import create_apply_transaction, rollback_transaction
from .logstore import write_log
from .models import CommandSpec, OperationResult, ProjectConfig
from .tasker import TaskerProgressSink

console = create_console()


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
    prefix = os.environ.get("PREFIX", "").strip()
    if prefix:
        base = Path(prefix) / "var" / "devtool" / "proot"
    else:
        base = Path.home() / ".local" / "state" / "devtool" / "proot"
    etc_dir = base / "etc"
    etc_dir.mkdir(parents=True, exist_ok=True)
    os_release = etc_dir / "os-release"
    if not os_release.exists() or os_release.read_text(encoding="utf-8", errors="ignore") != _TERMUX_DOTNET_OS_RELEASE:
        os_release.write_text(_TERMUX_DOTNET_OS_RELEASE, encoding="utf-8")
    return etc_dir


def _prepare_termux_dotnet(argv: list[str]) -> tuple[list[str], str | None]:
    if not argv or argv[0] != "dotnet" or not is_termux_environment():
        return argv, None

    dotnet_path = _resolve_termux_dotnet()
    if not dotnet_path or not shutil.which("proot"):
        return argv, None

    etc_dir = _termux_proot_etc_dir()
    return ["proot", "-b", f"{etc_dir}:/etc", dotnet_path, *argv[1:]], None



def build_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    if action == "validate":
        return build_validation_specs(config)
    if action == "wine-validate":
        return build_validation_specs(config, wine=True)

    if config.kind == "dotnet":
        return _dotnet_specs(config, action)
    if config.kind == "python":
        return _python_specs(config, action)

    # unknown kind relies on explicit commands
    return _generic_specs(config, action)


def build_validation_specs(
    config: ProjectConfig,
    *,
    wine: bool = False,
    skip_restore: bool = False,
    skip_build: bool = False,
    skip_test: bool = False,
) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    ran_restore = False
    if not skip_restore:
        specs.extend(build_specs(config, "restore"))
        ran_restore = True
    if not skip_build:
        build_phase_specs = build_specs(config, "build")
        if config.kind == "dotnet" and ran_restore:
            build_phase_specs = [_add_no_restore_to_dotnet_build_spec(spec) for spec in build_phase_specs]
        specs.extend(build_phase_specs)
    if not skip_test:
        test_specs = build_specs(config, "wine-test" if wine else "test")
        if config.kind == "dotnet" and not skip_build:
            test_specs = [_add_no_build_to_dotnet_test_spec(spec) for spec in test_specs]
        specs.extend(test_specs)
    if not specs:
        raise SystemExit("Validation would run no commands. Clear at least one skip flag.")
    return specs


def _maybe_explicit(command_name: str, text: str, env: dict[str, str] | None = None) -> list[CommandSpec]:
    if text:
        return [CommandSpec(command_name, shlex.split(text), env=env or {}, metadata={"explicit": True})]
    return []




def _is_dotnet_build_argv(argv: list[str]) -> bool:
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet" and index + 1 < len(argv):
            return argv[index + 1] == "build"
    return False


def _add_no_restore_to_dotnet_build_spec(spec: CommandSpec) -> CommandSpec:
    if spec.metadata.get("explicit"):
        return spec
    if not _is_dotnet_build_argv(spec.argv) or "--no-restore" in spec.argv:
        return spec
    argv = list(spec.argv)
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet" and index + 1 < len(argv) and argv[index + 1] == "build":
            argv.insert(index + 2, "--no-restore")
            metadata = dict(spec.metadata)
            metadata["validation_no_restore"] = True
            return CommandSpec(spec.name, argv, env=spec.env, cwd=spec.cwd, destructive=spec.destructive, metadata=metadata)
    return spec

def _is_dotnet_test_argv(argv: list[str]) -> bool:
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet" and index + 1 < len(argv):
            return argv[index + 1] == "test"
    return False


def _add_no_build_to_dotnet_test_spec(spec: CommandSpec, *, allow_explicit: bool = False) -> CommandSpec:
    if spec.metadata.get("explicit") and not allow_explicit:
        return spec
    if not _is_dotnet_test_argv(spec.argv):
        return spec
    if "--no-build" in spec.argv:
        return spec
    argv = list(spec.argv)
    for index, value in enumerate(argv):
        if Path(value).name == "dotnet" and index + 1 < len(argv) and argv[index + 1] == "test":
            argv.insert(index + 2, "--no-build")
            metadata = dict(spec.metadata)
            metadata["validation_no_build"] = True
            return CommandSpec(spec.name, argv, env=spec.env, cwd=spec.cwd, destructive=spec.destructive, metadata=metadata)
    return spec


def add_no_build_to_dotnet_test_specs(specs: list[CommandSpec], *, allow_explicit: bool = True) -> tuple[list[CommandSpec], int]:
    """Return specs with --no-build added to dotnet test commands.

    This is used by the public ``devtool test --no-build`` flag, so explicit
    dotnet test commands are eligible too. Non-dotnet test commands are left
    unchanged and reported via the returned count.
    """

    updated: list[CommandSpec] = []
    changed = 0
    for spec in specs:
        new_spec = _add_no_build_to_dotnet_test_spec(spec, allow_explicit=allow_explicit)
        if new_spec.argv != spec.argv:
            changed += 1
            metadata = dict(new_spec.metadata)
            metadata["user_no_build"] = True
            new_spec = CommandSpec(new_spec.name, new_spec.argv, env=new_spec.env, cwd=new_spec.cwd, destructive=new_spec.destructive, metadata=metadata)
        updated.append(new_spec)
    return updated, changed


def _dotnet_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    env = config.env
    restore_target = config.restore_target or config.build_target
    build_target = config.build_target or restore_target
    test_target = config.test_target or build_target
    run_target = config.resolve_repo_relative(config.run_target) if config.run_target else build_target

    if not any([restore_target, build_target, test_target, run_target]) and action in {"restore", "build", "test", "wine-test", "run"}:
        raise SystemExit("No dotnet target resolved. Use --target, --solution, --project, or configure .devtool.toml.")

    if action == "restore":
        return _maybe_explicit("restore", config.restore_command, env) or [CommandSpec("restore", ["dotnet", "restore", restore_target], env=env)]
    if action == "build":
        return _maybe_explicit("build", config.build_command, env) or [CommandSpec("build", ["dotnet", "build", build_target], env=env)]
    if action == "test":
        return _maybe_explicit("test", config.test_command, env) or [CommandSpec("test", ["dotnet", "test", test_target], env=env)]
    if action == "wine-test":
        return _maybe_explicit("wine-test", config.test_command, env) or [CommandSpec("wine-test", ["dotnet", "test", test_target], env=env)]
    if action == "run":
        return _maybe_explicit("run", config.run_command, env) or [CommandSpec("run", ["dotnet", "run", "--project", run_target], env=env)]
    if action == "package":
        if config.package_command:
            return [CommandSpec("package", shlex.split(config.package_command), env=env)]
        if (config.repo_root / "scripts" / "package-release.sh").exists():
            return [CommandSpec("package", ["bash", "./scripts/package-release.sh"], env=env)]
        raise SystemExit("No package command configured.")
    raise SystemExit(f"Unsupported action for dotnet: {action}")


def _python_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    env = config.env
    py = config.python_executable
    repo = config.repo_root

    if action == "restore":
        if config.restore_command:
            return [CommandSpec("restore", shlex.split(config.restore_command), env=env)]
        if (repo / "requirements-dev.txt").exists():
            return [CommandSpec("restore", [py, "-m", "pip", "install", "-r", "requirements-dev.txt"], env=env)]
        if (repo / "requirements.txt").exists():
            return [CommandSpec("restore", [py, "-m", "pip", "install", "-r", "requirements.txt"], env=env)]
        return [CommandSpec("restore", [py, "-m", "pip", "install", "-e", "."], env=env)]
    if action == "build":
        return _maybe_explicit("build", config.build_command, env) or [CommandSpec("build", [py, "-m", "build"], env=env)]
    if action == "test":
        if config.test_command:
            return [CommandSpec("test", shlex.split(config.test_command), env=env)]
        if (repo / "pytest.ini").exists() or (repo / "pyproject.toml").exists() or (repo / "tests").exists():
            return [CommandSpec("test", [py, "-m", "pytest"], env=env)]
        return [CommandSpec("test", [py, "-m", "unittest", "discover"], env=env)]
    if action == "wine-test":
        # usually same as test; env may include wine-related variables for parity tools
        return _python_specs(config, "test")
    if action == "run":
        if config.run_command:
            return [CommandSpec("run", shlex.split(config.run_command), env=env)]
        if config.run_module:
            return [CommandSpec("run", [py, "-m", config.run_module], env=env)]
        if config.run_target:
            return [CommandSpec("run", [py, config.resolve_repo_relative(config.run_target)], env=env)]
        raise SystemExit("No Python run target configured. Set run_command, run_module, or run_target.")
    if action == "package":
        if config.package_command:
            return [CommandSpec("package", shlex.split(config.package_command), env=env)]
        return [CommandSpec("package", [py, "-m", "build"], env=env)]
    raise SystemExit(f"Unsupported action for python: {action}")


def _generic_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    mapping = {
        "restore": config.restore_command,
        "build": config.build_command,
        "test": config.test_command,
        "wine-test": config.test_command,
        "run": config.run_command,
        "package": config.package_command,
    }
    text = mapping.get(action, "")
    if not text:
        raise SystemExit(f"No command configured for action '{action}'")
    return [CommandSpec(action, shlex.split(text), env=config.env)]


def _interesting_output_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    return bool(re.search(r"(error|warning|failed|fail|restore|build|test|pack|publish|\d+%)", text, re.IGNORECASE))



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


def _iter_process_output_lines(proc: subprocess.Popen, *, drain_seconds: float = 0.75, idle_timeout: float | None = None):
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
    timed_out = False

    def emit_text(text: str):
        nonlocal pending
        pending += text
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            yield line + "\n"

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
                    for line in emit_text(decoder.decode(chunk)):
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
                    for line in emit_text(decoder.decode(chunk)):
                        yield line
                break

            if idle_timeout is not None and idle_timeout > 0 and (time.monotonic() - last_output_at) >= idle_timeout:
                timed_out = True
                message = f"\nDevtool stopped a quiet child process after {idle_timeout:g}s without output.\n"
                for line in emit_text(message):
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
            for line in emit_text(tail):
                yield line
        if pending:
            yield pending
    finally:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        except Exception:
            pass


def _wait_process_returncode(proc: subprocess.Popen, *, timeout: float = 5.0) -> int:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, 15)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            return proc.wait(timeout=2.0)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, 9)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                return proc.wait(timeout=1.0)
            except Exception:
                return 124

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
) -> tuple[int, str]:
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
            if verbose and output:
                print(output, end="", flush=True)
            return completed.returncode, output
        except OSError:
            return 127, str(exc)

    output_parts: list[str] = []
    assert proc.stdout is not None
    tracker = DotnetProgressTracker(argv, cwd, command_name, console, phase_index=phase_index, phase_total=phase_total) if (not verbose or tasker_progress is not None) else None
    if tracker is not None and verbose:
        # In verbose mode stdout is already streamed as raw text. Keep the
        # dotnet parser active only for Tasker/progress events; do not
        # render a second Rich progress bar over the raw output.
        tracker.enabled = False
    if tracker is not None and tasker_progress is not None:
        def _tasker_update(snapshot: dict) -> None:
            tasker_progress.progress(
                detail=str(snapshot.get("detail") or ""),
                completed=snapshot.get("completed"),
                total=snapshot.get("total"),
                percent=snapshot.get("percent"),
                force=True,
            )
        tracker.update_callback = _tasker_update
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

    try:
        idle_timeout = _idle_timeout_for(effective_argv if False else argv, command_name)
        if tracker is None:
            for line in _iter_process_output_lines(proc, idle_timeout=idle_timeout):
                output_parts.append(line)
                print(line, end="", flush=True)
            return _wait_process_returncode(proc), "".join(output_parts)

        with tracker:
            for line in _iter_process_output_lines(proc, idle_timeout=idle_timeout):
                output_parts.append(line)
                # Non-verbose mode intentionally keeps command stdout/stderr captured only.
                # Dotnet commands get a parsed Rich progress display instead of raw output.
                # Use --verbose when the full restore/build/test stream is needed live.
                if verbose:
                    print(line, end="", flush=True)
                tracker.update_from_line(line)
            returncode = _wait_process_returncode(proc)
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


def run_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    verbose: bool,
    dry_run: bool = False,
    continue_on_error: bool = False,
    log_path: Path | str | None = None,
    tasker_progress: TaskerProgressSink | None = None,
) -> tuple[list[OperationResult], str]:
    results: list[OperationResult] = []
    raw_sections: list[str] = []

    for spec_index, spec in enumerate(specs, start=1):
        effective_argv, cleanup_dir = _prepare_termux_dotnet(spec.argv)
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_started(spec.name, index=spec_index, total=len(specs), argv=effective_argv)

        if dry_run:
            output = "$ " + " ".join(effective_argv)
            log_file = str(write_log(output, log_path, append=log_path is not None))
            results.append(OperationResult(spec.name, effective_argv, 0, 0.0, output, [], skipped=True, log_path=log_file))
            raw_sections.append(output)
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            continue

        env = os.environ.copy()
        env.update(config.env)
        env.update(spec.env)

        started = time.perf_counter()
        returncode = 1
        output = ""
        try:
            if verbose:
                console.print(f"[cyan]$ {' '.join(shlex.quote(a) for a in effective_argv)}[/cyan]")
                returncode, output = _run_process_live(
                    effective_argv,
                    cwd=spec.cwd or str(config.repo_root),
                    env=env,
                    verbose=True,
                    command_name=spec.name,
                    tasker_progress=tasker_progress,
                    phase_index=spec_index,
                    phase_total=len(specs),
                )
            else:
                cwd = spec.cwd or str(config.repo_root)
                if dotnet_progress_plan(effective_argv, cwd):
                    returncode, output = _run_process_live(
                        effective_argv,
                        cwd=cwd,
                        env=env,
                        verbose=False,
                        command_name=spec.name,
                        tasker_progress=tasker_progress,
                        phase_index=spec_index,
                        phase_total=len(specs),
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
                        )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        duration = time.perf_counter() - started
        issues = parse_issues(config.repo_root, spec.name, output)
        failure_kind = classify_failure(spec.name, output, returncode)
        command_line = "$ " + " ".join(shlex.quote(a) for a in effective_argv)
        raw_text = command_line + "\n" + output
        log_file = str(write_log(raw_text, log_path, append=log_path is not None))
        result = OperationResult(spec.name, effective_argv, returncode, duration, output, issues, log_path=log_file, failure_kind=failure_kind)
        results.append(result)
        if tasker_progress is not None and not dry_run:
            tasker_progress.phase_finished(result)
        raw_sections.append(raw_text)

        if returncode != 0 and not continue_on_error:
            break

    return results, "\n\n".join(raw_sections)

def apply_overlay(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False, log_path: Path | str | None = None, preview: dict | None = None) -> OperationResult:
    artifact_path = resolve_cli_path(artifact)
    argv = ["unzip", "-o", str(artifact_path), "-d", str(repo_root)]
    return _apply_archive(repo_root, artifact_path, argv, delete_artifact=True, snapshot=False, force=force, yes=yes, dry_run=dry_run, log_path=log_path, preview=preview)


def apply_snapshot(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False, log_path: Path | str | None = None, preview: dict | None = None) -> OperationResult:
    artifact_path = resolve_cli_path(artifact)
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
    try:
        preview_data = preview or preview_artifact(repo_root, artifact_path, snapshot=snapshot)
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
        # The original artifact is intentionally not deleted here.  Apply is now
        # transactional across extraction plus any later validation/commit steps,
        # so cleanup is deferred until the CLI knows no rollback happened.
        output = f"Applied {'snapshot' if snapshot else 'overlay'} from {artifact_path}"
        output += f"\nTransaction backup: {transaction.get('transaction_id')}"
        if skipped_metadata:
            output += f"\nSkipped {skipped_metadata} devtool metadata file(s)."
        if deleted_paths:
            output += f"\nDeleted {len(deleted_paths)} path(s) from .devtool-delete."
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
    if artifact_path.suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as zf:
            return _safe_extract_zip(zf, target)
    if artifact_path.suffix == ".tar" or artifact_path.name.endswith((".tar.gz", ".tgz")):
        mode = "r:gz" if artifact_path.name.endswith((".tar.gz", ".tgz")) else "r"
        with tarfile.open(artifact_path, mode) as tf:
            return _safe_extract_tar(tf, target)
    raise RuntimeError(f"Unsupported artifact type: {artifact_path}")


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> int:
    target = target.resolve()
    skipped_metadata = 0
    for member in archive.infolist():
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


def git_commit(repo_root: Path, message: str, *, verbose: bool = False, dry_run: bool = False, log_path: Path | str | None = None) -> tuple[list[OperationResult], str]:
    specs = [
        CommandSpec("git-add", ["git", "add", "-A"]),
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
