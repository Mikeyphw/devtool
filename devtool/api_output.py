from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import time
from typing import Any, Callable

from .core.results import CommandResult
from .core.events import collect_json_events


def extract_universal_json_flag(argv: list[str]) -> tuple[list[str], bool]:
    stripped: list[str] = []
    requested = False
    for item in argv:
        if item == "--json":
            requested = True
            continue
        stripped.append(item)
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
    data = _parsed_stdout(stdout)
    result = CommandResult(success=exit_code == 0, exit_code=exit_code, stdout="" if data is not None else stdout, stderr=stderr, duration=duration_seconds, metadata={"command": command, "repo": repo})
    return {
        "schema_version": 1,
        "ok": exit_code == 0,
        "command": command,
        "repo": repo,
        "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "data": data,
        "stdout": "" if data is not None else stdout,
        "stderr": stderr,
        "result": result.to_dict(),
        "events": list(events or []),
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
