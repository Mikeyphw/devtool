from __future__ import annotations

"""Process-global state guards shared by Devtool's CLI and test harness.

Public process-style entry points use these helpers to preserve the process
state a real child process could not leak back to its caller.  The richer
``ProcessStateGuard`` is also test infrastructure: it snapshots the process
state that can make one in-process test poison the next, reports the exact
drift, and restores the baseline before the next test starts.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import signal
from typing import Iterator, Mapping, Sequence


DEFAULT_ENVIRONMENT_KEYS = frozenset({
    "HOME",
    "PREFIX",
    "TMPDIR",
    "TMP",
    "TEMP",
})
DEFAULT_ENVIRONMENT_PREFIXES = (
    "DEVTOOL_",
    "XDG_",
    "PYTHON",
)
DEFAULT_SIGNAL_NAMES = (
    "SIGINT",
    "SIGTERM",
    "SIGHUP",
)
_SECRET_NAME_FRAGMENTS = (
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE_KEY",
)


def _safe_state_value(name: str, value: str) -> str:
    if any(fragment in name.upper() for fragment in _SECRET_NAME_FRAGMENTS):
        return "<redacted>"
    if len(value) > 180:
        return value[:177] + "..."
    return value


@contextmanager
def preserve_environment_keys(keys: Sequence[str]) -> Iterator[None]:
    """Restore selected environment keys when an in-process operation exits.

    Nested invocations are safe: each context restores the values that were
    visible on entry rather than unconditionally deleting the keys.
    """

    names = tuple(dict.fromkeys(str(key) for key in keys if str(key)))
    missing = object()
    before = {key: os.environ.get(key, missing) for key in names}
    try:
        yield
    finally:
        for key, value in before.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)


def _tracked_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in environ.items()
        if key in DEFAULT_ENVIRONMENT_KEYS
        or any(key.startswith(prefix) for prefix in DEFAULT_ENVIRONMENT_PREFIXES)
    }


def _selected_signal_handlers() -> dict[int, object]:
    handlers: dict[int, object] = {}
    for name in DEFAULT_SIGNAL_NAMES:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            handlers[int(signum)] = signal.getsignal(signum)
        except (OSError, RuntimeError, ValueError):
            continue
    return handlers


def _runtime_log_override() -> str:
    try:
        from .logstore import get_log_dir_override

        value = get_log_dir_override()
    except Exception:
        return ""
    return str(value or "")


@dataclass(frozen=True, slots=True)
class ProcessStateDifference:
    category: str
    name: str
    before: str
    after: str

    def render(self) -> str:
        before = _safe_state_value(self.name, self.before)
        after = _safe_state_value(self.name, self.after)
        return f"{self.category}:{self.name}: {before!r} -> {after!r}"


@dataclass(slots=True)
class ProcessStateGuard:
    environment: dict[str, str]
    cwd: str
    log_dir_override: str
    signal_handlers: dict[int, object]

    @classmethod
    def capture(cls) -> "ProcessStateGuard":
        return cls(
            environment=_tracked_environment(dict(os.environ)),
            cwd=str(Path.cwd()),
            log_dir_override=_runtime_log_override(),
            signal_handlers=_selected_signal_handlers(),
        )

    def differences(self) -> tuple[ProcessStateDifference, ...]:
        differences: list[ProcessStateDifference] = []
        current_env = _tracked_environment(dict(os.environ))
        for key in sorted(set(self.environment) | set(current_env)):
            before = self.environment.get(key)
            after = current_env.get(key)
            if before != after:
                differences.append(ProcessStateDifference(
                    "environment",
                    key,
                    "<unset>" if before is None else before,
                    "<unset>" if after is None else after,
                ))

        current_cwd = str(Path.cwd())
        if current_cwd != self.cwd:
            differences.append(ProcessStateDifference("cwd", "cwd", self.cwd, current_cwd))

        current_log_override = _runtime_log_override()
        if current_log_override != self.log_dir_override:
            differences.append(ProcessStateDifference(
                "runtime-override",
                "log_dir",
                self.log_dir_override or "<unset>",
                current_log_override or "<unset>",
            ))

        current_signals = _selected_signal_handlers()
        for signum in sorted(set(self.signal_handlers) | set(current_signals)):
            before = self.signal_handlers.get(signum)
            after = current_signals.get(signum)
            if before != after:
                try:
                    name = signal.Signals(signum).name
                except ValueError:
                    name = str(signum)
                differences.append(ProcessStateDifference(
                    "signal",
                    name,
                    repr(before),
                    repr(after),
                ))
        return tuple(differences)

    def restore(self) -> None:
        current_env = _tracked_environment(dict(os.environ))
        for key in set(current_env) - set(self.environment):
            os.environ.pop(key, None)
        for key, value in self.environment.items():
            os.environ[key] = value

        try:
            os.chdir(self.cwd)
        except OSError:
            # A test can deliberately remove its temporary working directory.
            # Environment and runtime overrides are still restored, and the
            # resulting cwd drift remains visible in the reported differences.
            pass

        try:
            from .logstore import set_log_dir_override

            set_log_dir_override(self.log_dir_override or None)
        except Exception:
            pass

        for signum, handler in self.signal_handlers.items():
            try:
                if signal.getsignal(signum) != handler:
                    signal.signal(signum, handler)
            except (OSError, RuntimeError, ValueError):
                # Signal mutation is still reported by ``differences``. Restore
                # can be impossible outside the main interpreter thread.
                continue

    def verify_and_restore(self) -> tuple[ProcessStateDifference, ...]:
        differences = self.differences()
        self.restore()
        return differences


def format_process_state_differences(
    differences: Sequence[ProcessStateDifference],
    *,
    limit: int = 20,
) -> str:
    items = list(differences)
    lines = [item.render() for item in items[: max(1, int(limit))]]
    if len(items) > len(lines):
        lines.append(f"... and {len(items) - len(lines)} more process-state difference(s)")
    return "\n".join(lines)


@contextmanager
def preserve_process_state() -> Iterator[None]:
    """Restore Devtool-owned process state after an in-process operation.

    This gives process-entry-point style code subprocess-like isolation when it
    is invoked repeatedly inside one Python interpreter.  Detection/reporting
    remains the responsibility of ``ProcessStateGuard`` in tests; production
    callers use this context only to guarantee restoration.
    """

    guard = ProcessStateGuard.capture()
    try:
        yield
    finally:
        guard.restore()
