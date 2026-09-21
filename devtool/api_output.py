from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import time
from typing import Any, Callable

from .core.results import CommandResult
from .core.events import collect_json_events
from .ui_core.machine import IPC_PROTOCOL_VERSION, MACHINE_SCHEMA_VERSION, machine_state_from_runtime
from .logstore import redact_sensitive_text, redact_sensitive_value


_ROOT_OPTIONS_WITH_VALUE = {
    "-r", "--repo", "-c", "--config", "--target", "--variant", "--group",
    "--solution", "--project", "--project-type", "--runner", "--affected-base",
    "--affected-head", "--log-dir", "--performance-file", "--machine-file",
    "--copy-via", "--theme", "--stdout-payload", "--ui-profile", "--ui-theme",
    "--ui-density", "--ui-icons", "--ui-motion", "--ui-verbosity", "--hud-mode",
    "--chroot", "--ssh", "--ssh-host", "--ssh-port", "--ssh-user",
    "--ssh-profile", "--ssh-identity", "--ssh-session", "--ssh-cwd", "--ssh-tool", "--ssh-mode",
    "--ssh-stage-dir", "--execution-environment", "--proot-distro", "--proot-user",
    "--proot-cwd", "--proot-repo-mount", "--proot-bind", "--proot-env",
    "--proot-stage-dir", "--termux-resource-profile", "--shared-storage-policy",
    "--artifact-export", "--bootstrap-downloads", "--toolchain-profile",
    "--android-sdk-root", "--android-sdk-provider", "--gradle-command",
    "--gradle-provider", "--java-home", "--java-provider", "--gradle-user-home",
    "--native-provider", "--native-cache", "--android-build-tools-provider",
    "--aapt2-provider",
}


def extract_universal_json_flag(argv: list[str]) -> tuple[list[str], bool]:
    """Strip only the root-level universal --json flag.

    Command-specific --json flags are preserved once the first subcommand has
    been reached.  This lets native APIs such as ``artifact verify --json`` keep
    their documented parser behavior while still supporting ``devtool --json``
    envelopes at the root.
    """

    stripped: list[str] = []
    requested = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            requested = True
            index += 1
            continue
        stripped.append(token)
        if token == "--":
            stripped.extend(argv[index + 1 :])
            break
        if token in _ROOT_OPTIONS_WITH_VALUE:
            if index + 1 < len(argv):
                stripped.append(argv[index + 1])
                index += 2
            else:
                index += 1
            continue
        if any(token.startswith(option + "=") for option in _ROOT_OPTIONS_WITH_VALUE if option.startswith("--")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        remainder = argv[index + 1 :]
        if "--json" in remainder:
            requested = True
        stripped.extend(remainder)
        break
    return stripped, requested


def _parsed_stdout(text: str) -> Any:
    candidate = text.strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None




def _native_machine_envelope(value: Any) -> bool:
    """Return True for command-native stable API envelopes.

    Devtool's validation control API predates universal ``--json`` and has a
    documented machine contract consumed by the TUI, machine files, and
    external callers. Wrapping that payload again would be a breaking API
    change, so universal JSON treats it as already normalized.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("api_version"), int)
        and isinstance(value.get("accepted"), bool)
        and isinstance(value.get("action"), str)
        and isinstance(value.get("data"), dict)
    )

def invocation_envelope(*, command: str, repo: str, exit_code: int, stdout: str, stderr: str, started_at: datetime, duration_seconds: float, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    safe_stdout = redact_sensitive_text(stdout)
    safe_stderr = redact_sensitive_text(stderr)
    data = redact_sensitive_value(_parsed_stdout(stdout))
    result = CommandResult(success=exit_code == 0, exit_code=exit_code, stdout="" if data is not None else safe_stdout, stderr=safe_stderr, duration=duration_seconds, metadata={"command": command, "repo": repo})
    machine = machine_state_from_runtime({
        "producer": "devtool",
        "status": "ok" if exit_code == 0 else "failed",
        "command": command,
        "repo_root": repo,
        "repo_name": repo.rstrip("/").rsplit("/", 1)[-1],
        "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "message": safe_stderr if exit_code else "",
    }).as_dict()
    return {
        "schema_version": 1,
        "machine_schema_version": MACHINE_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "machine": machine,
        "ok": exit_code == 0,
        "command": command,
        "repo": repo,
        "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "data": data,
        "stdout": "" if data is not None else safe_stdout,
        "stderr": safe_stderr,
        "result": redact_sensitive_value(result.to_dict()),
        "events": redact_sensitive_value(list(events or [])),
    }


def run_json_invocation(invoke: Callable[[], int], *, command: str, repo: str) -> int:
    stdout = io.StringIO()
    stderr = io.StringIO()
    started = datetime.now(timezone.utc)
    monotonic = time.monotonic()
    with collect_json_events() as event_listener:
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = int(invoke())
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else 1
            if not isinstance(exc.code, int) and exc.code:
                print(str(exc.code), file=stderr)
        except Exception as exc:
            code = 1
            print(f"Internal devtool error: {exc}", file=stderr)
    captured_stdout = stdout.getvalue()
    native_payload = _parsed_stdout(captured_stdout)
    if _native_machine_envelope(native_payload):
        print(json.dumps(native_payload, indent=2, ensure_ascii=False))
        return code
    payload = invocation_envelope(
        command=command,
        repo=repo,
        exit_code=code,
        stdout=captured_stdout,
        stderr=stderr.getvalue(),
        started_at=started,
        duration_seconds=max(0.0, time.monotonic() - monotonic),
        events=event_listener.events,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code
