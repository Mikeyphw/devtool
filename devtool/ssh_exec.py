from __future__ import annotations

"""Managed arbitrary SSH execution built on the canonical command-mode substrate."""

from dataclasses import replace
import io
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Sequence

from .config import load_project_config
from .core.events import (
    GraphSealed,
    NodeDiscovered,
    NodeFinished,
    NodeStarted,
    ProcessExited,
    ProcessStarted,
    RunFinished,
    RunStarted,
    publish_event,
)
from .core.execution_contract import RunId
from .models import CommandSpec
from .ssh_history import follow_output
from .ssh_resilience import resume_remote_run
from .ssh_returns import load_return_state, recover_return_bundles
from .ssh_tmux_runner import SSHRunnerConfig, wrap_specs_for_ssh_commands


def _safe_cwd(repo_root: Path, value: str) -> Path:
    if not str(value or "").strip():
        return repo_root
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("ssh exec --cwd must remain inside the selected repository") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"ssh exec cwd does not exist or is not a directory: {candidate}")
    return candidate


def _run_id() -> str:
    existing = str(os.environ.get("DEVTOOL_RUN_ID") or RunId.current(create=False) or "").strip()
    return existing or str(RunId.new(prefix="ssh"))


def execute_managed(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    argv: Sequence[str] = (),
    shell_text: str = "",
    target: str = "",
    cwd: str = "",
    detached: bool = False,
    follow: bool = False,
    assume_yes: bool = False,
    capture_launcher: bool = True,
    follow_live: bool = True,
) -> dict[str, Any]:
    """Execute one arbitrary command through the normal managed SSH machinery.

    argv mode preserves argument boundaries. Raw shell text is accepted only via
    the explicit ``shell_text`` parameter/CLI ``--shell`` path.
    """
    repo = Path(repo_root).expanduser().resolve()
    if shell_text and argv:
        raise ValueError("ssh exec accepts either argv after -- or --shell text, not both")
    if shell_text:
        command_argv = ["sh", "-lc", str(shell_text)]
        shell_mode = True
    else:
        command_argv = [str(item) for item in argv]
        if command_argv and command_argv[0] == "--":
            command_argv = command_argv[1:]
        if not command_argv:
            raise ValueError("ssh exec requires a command after -- or explicit --shell text")
        shell_mode = False

    project = load_project_config(
        str(repo), None,
        target_name=str(target or ""),
        allow_ambiguous_target=not bool(target),
        allow_ambiguous_project_kind=True,
    )
    local_cwd = _safe_cwd(repo, cwd)
    run_id = _run_id()
    command_config = replace(config, mode="commands", detached=False, operation_id=run_id)
    metadata = {
        "runner": "command",
        "ssh_exec_managed": True,
        "ssh_exec_detached": bool(detached or follow),
        "ssh_exec_release_lease": bool(detached or follow),
        "ssh_confirmation_mode": "automatic" if assume_yes else "interactive",
        "ssh_exec_shell_text": shell_mode,
        "target": str(target or project.selected_target or ""),
    }
    spec = CommandSpec("ssh-exec", command_argv, cwd=str(local_cwd), env={}, metadata=metadata)
    wrapped = wrap_specs_for_ssh_commands(
        [spec], command_config, project_config=project, repo_root=repo, dry_run=False,
    )
    if len(wrapped) != 1:
        raise ValueError("ssh exec expected exactly one canonical remote phase")
    remote = wrapped[0]
    session = str(remote.metadata.get("ssh_session") or "")
    node_id = str(remote.metadata.get("oci_phase_node_id") or session or "ssh-exec")
    process_id = "ssh:" + (session or "exec")

    publish_event(RunStarted(
        run_id=run_id, command="ssh exec", project=str(repo), source="ssh-exec",
        metadata={"ssh_profile": command_config.profile, "ssh_destination": command_config.destination},
    ))
    publish_event(NodeDiscovered(run_id=run_id, node_id=node_id, label="ssh exec", kind="remote-command", source="ssh-exec"))
    publish_event(GraphSealed(run_id=run_id, reason="single managed ssh exec phase", source="ssh-exec"))
    publish_event(NodeStarted(run_id=run_id, node_id=node_id, label="ssh exec", kind="remote-command", argv=tuple(command_argv), source="ssh-exec"))
    publish_event(ProcessStarted(
        run_id=run_id, node_id=node_id, process_id=process_id, argv=tuple(command_argv), cwd=str(local_cwd), source="ssh-exec",
    ))

    started = time.monotonic()
    completed = subprocess.run(
        remote.argv,
        cwd=str(repo),
        env={**os.environ, **dict(remote.env or {})},
        text=True,
        capture_output=capture_launcher,
        check=False,
    )
    launcher_rc = int(completed.returncode)
    launched_detached = bool(detached or follow) and launcher_rc == 125
    return_metadata = dict(remote.metadata or {})
    if follow and launched_detached:
        follow_stdout = None if follow_live else io.StringIO()
        follow_stderr = None if follow_live else io.StringIO()
        followed = follow_output(
            command_config, run_id=run_id, session=session,
            stdout_writer=follow_stdout, stderr_writer=follow_stderr,
        )
        if follow_stdout is not None:
            followed["stdout"] = follow_stdout.getvalue()
        if follow_stderr is not None:
            followed["stderr"] = follow_stderr.getvalue()
        final_rc = followed.get("status")
        final_rc = int(final_rc) if final_rc is not None else (255 if followed.get("state") in {"stale", "interrupted"} else 0)
        # Reconcile staged secrets/lease without rerunning the successful remote
        # command. The phase itself already attempted its return transport.
        resume_remote_run(command_config, run_id=run_id, session=session, attach=False)
        returns = load_return_state(return_metadata) if return_metadata.get("ssh_return_manifest") else {
            "state": "unavailable", "recoverable": False, "artifacts": [],
        }
        if returns.get("recoverable"):
            try:
                returns = recover_return_bundles(command_config, repo, run_id=run_id, session=session)
            except ValueError as exc:
                returns = {**returns, "recovery_error": str(exc)}
    else:
        followed = {}
        returns = load_return_state(return_metadata) if return_metadata.get("ssh_return_manifest") else {
            "state": "unavailable", "recoverable": False, "artifacts": [],
        }
        final_rc = 0 if launched_detached else launcher_rc

    duration = time.monotonic() - started
    terminal = not launched_detached or follow
    status_name = "succeeded" if final_rc == 0 else "failed"
    if launched_detached and not follow:
        status_name = "running"

    if terminal:
        publish_event(ProcessExited(
            run_id=run_id, node_id=node_id, process_id=process_id, exit_code=int(final_rc),
            duration_seconds=duration, failure_kind="" if final_rc == 0 else "remote-command", source="ssh-exec",
        ))
        publish_event(NodeFinished(
            run_id=run_id, node_id=node_id, status="succeeded" if final_rc == 0 else "failed",
            exit_code=int(final_rc), duration_seconds=duration, failure_kind="" if final_rc == 0 else "remote-command",
            process_id=process_id, source="ssh-exec",
        ))
        publish_event(RunFinished(
            run_id=run_id, status="succeeded" if final_rc == 0 else "failed", exit_code=int(final_rc),
            duration_seconds=duration, source="ssh-exec",
        ))

    return {
        "schema_version": 1,
        "kind": "ssh-exec",
        "ok": True if launched_detached and not follow else final_rc == 0,
        "run_id": run_id,
        "node_id": node_id,
        "session": session,
        "profile": command_config.profile,
        "destination": command_config.destination,
        "repository": str(repo),
        "repository_fingerprint": str(getattr(project.repository_identity, "fingerprint", "") or ""),
        "target": str(target or project.selected_target or ""),
        "cwd": str(local_cwd),
        "argv": command_argv if not shell_mode else [],
        "shell_text_mode": shell_mode,
        "detached": bool(detached),
        "follow": bool(follow),
        "confirmation_mode": "automatic" if assume_yes else "interactive",
        "state": status_name,
        "returncode": None if launched_detached and not follow else int(final_rc),
        "launcher_returncode": launcher_rc,
        "launcher_stdout": str(completed.stdout or "") if capture_launcher else "",
        "launcher_stderr": str(completed.stderr or "") if capture_launcher else "",
        "follow_result": followed,
        "return_recovery": returns,
        "execution_failed": bool(terminal and final_rc != 0),
        "return_failed": bool(returns.get("return_failed") or (returns.get("ok") is False)),
        "duration_seconds": duration,
        "recovery_hint": (
            f"devtool ssh output {command_config.profile} --run-id {run_id} --follow"
            if launched_detached and not follow else ""
        ),
    }
