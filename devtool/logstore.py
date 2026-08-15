from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .xdg import state_dir as _xdg_state_dir

_DEFAULT_MAX_FILES = 120
_DEFAULT_MAX_AGE_DAYS = 30.0
_DEFAULT_MAX_TOTAL_MB = 128.0
_DEFAULT_MAX_FILE_MB = 12.0
_MAX_IDENTIFIER = 128
_MAX_SECRET_VALUE = 1024 * 1024


def state_dir() -> Path:
    return _xdg_state_dir()


def logs_dir() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_log_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"


def _is_phone_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    home = os.environ.get("HOME", "")
    return bool(
        os.environ.get("TERMUX_VERSION")
        or os.environ.get("TERMUX_APP_PID")
        or prefix.startswith("/data/data/com.termux/")
        or home.startswith("/data/data/com.termux/files/home")
    )


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _durable_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as src, temp.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        with source.open("rb") as src:
            try:
                shutil.copystat(source, temp, follow_symlinks=True)
            except OSError:
                pass
        os.replace(temp, destination)
        _fsync_directory(destination.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _mirror_log_to_phone(path: Path) -> None:
    if not _is_phone_termux() or path.suffix != ".log":
        return
    mirror_root = Path(os.environ.get("DEVTOOL_PHONE_LOG_MIRROR_DIR", "/sdcard/.logs/devtool")).expanduser()
    try:
        resolved = path.resolve(strict=False)
        logs_root = logs_dir().resolve(strict=False)
        if not (resolved == logs_root or logs_root in resolved.parents):
            return
        _durable_copy(path, mirror_root / path.name)
    except OSError:
        # Mirroring is secondary persistence. The authoritative XDG log remains
        # available and callers should not lose a completed command because
        # shared storage is temporarily unavailable.
        pass


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.01) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _max_file_bytes() -> int:
    return int(_env_float("DEVTOOL_LOG_MAX_FILE_MB", _DEFAULT_MAX_FILE_MB) * 1024 * 1024)


def _bound_one_log(path: Path) -> None:
    limit = _max_file_bytes()
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= limit:
        return
    # Preserve a useful command/header prefix plus the newest output. The
    # truncation marker is explicit evidence that the middle was rotated away.
    marker = b"\n\n--- devtool log middle rotated to enforce size limit ---\n\n"
    head_size = max(1, limit // 4)
    tail_size = max(1, limit - head_size - len(marker))
    try:
        with path.open("rb") as handle:
            head = handle.read(head_size)
            handle.seek(max(0, size - tail_size))
            tail = handle.read(tail_size)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rotate")
        with temp.open("wb") as handle:
            handle.write(head)
            handle.write(marker)
            handle.write(tail)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except OSError:
        return


def _rotate_logs(*, protected: Path | None = None) -> None:
    root = logs_dir()
    try:
        files = [path for path in root.glob("*.log") if path.is_file()]
    except OSError:
        return
    protected_resolved = protected.resolve(strict=False) if protected is not None else None
    now = time.time()
    max_age = _env_float("DEVTOOL_LOG_MAX_AGE_DAYS", _DEFAULT_MAX_AGE_DAYS) * 86400.0
    max_files = _env_int("DEVTOOL_LOG_MAX_FILES", _DEFAULT_MAX_FILES)
    max_total = int(_env_float("DEVTOOL_LOG_MAX_TOTAL_MB", _DEFAULT_MAX_TOTAL_MB) * 1024 * 1024)

    def stat_key(path: Path) -> tuple[float, str]:
        try:
            return path.stat().st_mtime, path.name
        except OSError:
            return 0.0, path.name

    files.sort(key=stat_key)
    survivors: list[Path] = []
    for path in files:
        try:
            if protected_resolved is not None and path.resolve(strict=False) == protected_resolved:
                survivors.append(path)
                continue
            if now - path.stat().st_mtime > max_age:
                path.unlink()
                continue
        except OSError:
            continue
        survivors.append(path)

    def removable() -> list[Path]:
        return [
            path for path in survivors
            if protected_resolved is None or path.resolve(strict=False) != protected_resolved
        ]

    while len(survivors) > max_files:
        choices = removable()
        if not choices:
            break
        victim = choices[0]
        try:
            victim.unlink()
        except OSError:
            break
        survivors.remove(victim)

    def total_size() -> int:
        total = 0
        for path in survivors:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    while total_size() > max_total:
        choices = removable()
        if not choices:
            break
        victim = choices[0]
        try:
            victim.unlink()
        except OSError:
            break
        survivors.remove(victim)


# Credential redaction is deliberately scanner-based rather than relying on
# backtracking-heavy expressions. Every input character is visited a bounded
# number of times and individual records are capped before inspection.
_SECRET_WORDS = ("TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "API_KEY", "AUTH", "PRIVATE_KEY")
_CLI_SECRET_FLAGS = ("--token", "--password", "--secret", "--api-key", "--auth-token")
_MAX_REDACTION_RECORD = 2 * 1024 * 1024


def _secret_value_end(value: str, start: int) -> int:
    limit = min(len(value), start + _MAX_SECRET_VALUE)
    if start >= len(value):
        return start
    quote = value[start] if value[start] in {'"', "'"} else ""
    if quote:
        index = start + 1
        while index < limit and value[index] not in {quote, "\r", "\n"}:
            index += 1
        if index < len(value) and value[index] == quote:
            return index + 1
        # Never expose a tail just because the bounded value scanner hit its cap.
        return len(value)
    index = start
    while index < limit and value[index] not in " \t\r\n;":
        index += 1
    return index if index < limit else len(value)


def _identifier_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_-"


def _secret_identifier(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    return any(word in normalized for word in _SECRET_WORDS)


def _redact_private_key_blocks(value: str) -> str:
    """Remove PEM/OpenSSH private-key blocks with monotonic searches."""
    marker = "-----BEGIN "
    output: list[str] = []
    cursor = 0
    length = len(value)
    while cursor < length:
        begin = value.find(marker, cursor)
        if begin < 0:
            output.append(value[cursor:])
            break
        header_end = value.find("\n", begin)
        if header_end < 0:
            header_end = length
        header = value[begin:header_end].upper()
        if "PRIVATE KEY-----" not in header:
            output.append(value[cursor:begin + len(marker)])
            cursor = begin + len(marker)
            continue
        output.append(value[cursor:begin])
        end_begin = value.find("-----END ", header_end)
        if end_begin < 0:
            output.append("<redacted:private-key>")
            break
        end_line = value.find("\n", end_begin)
        end_line = length if end_line < 0 else end_line + 1
        end_header = value[end_begin:end_line].upper()
        if "PRIVATE KEY-----" not in end_header:
            # A malformed/unclosed private key is safer to redact through EOF.
            output.append("<redacted:private-key>")
            break
        output.append("<redacted:private-key>")
        cursor = end_line
    return "".join(output)


def _redact_record(record: str) -> str:
    """Redact one bounded record in one monotonic pass."""
    if not record:
        return record
    if len(record) > _MAX_REDACTION_RECORD:
        newline = "\n" if record.endswith("\n") else ""
        return f"<oversized-log-record-redacted:{len(record)}>{newline}"

    lower = record.lower()
    pieces: list[str] = []
    literal_start = 0
    index = 0
    length = len(record)

    def emit_replacement(prefix_end: int, value_start: int, value_end: int, marker: str = "<redacted>") -> int:
        nonlocal literal_start
        pieces.append(record[literal_start:value_start])
        pieces.append(marker)
        literal_start = value_end
        return value_end

    while index < length:
        ch = record[index]

        # Authorization: Bearer/Basic SECRET (':' or '=' accepted).
        if (ch == "A" or ch == "a") and lower.startswith("authorization", index):
            before_ok = index == 0 or not _identifier_char(record[index - 1])
            after = index + len("authorization")
            after_ok = after >= length or not _identifier_char(record[after])
            if before_ok and after_ok:
                cursor = after
                while cursor < length and record[cursor] in " \t":
                    cursor += 1
                if cursor < length and record[cursor] in ":=":
                    cursor += 1
                    while cursor < length and record[cursor] in " \t":
                        cursor += 1
                    for scheme in ("bearer", "basic"):
                        if lower.startswith(scheme, cursor):
                            scheme_end = cursor + len(scheme)
                            if scheme_end < length and record[scheme_end] in " \t":
                                cursor = scheme_end
                                while cursor < length and record[cursor] in " \t":
                                    cursor += 1
                                if cursor < length:
                                    end = _secret_value_end(record, cursor)
                                    index = emit_replacement(cursor, cursor, end)
                                    break
                    else:
                        index += 1
                        continue
                    if index == literal_start:
                        continue

        # Explicit CLI secret operands. Checking five fixed flags at a '-' is O(n).
        if ch == "-":
            matched_flag = ""
            for flag in _CLI_SECRET_FLAGS:
                if lower.startswith(flag, index):
                    end_flag = index + len(flag)
                    if end_flag == length or record[end_flag] in "= \t":
                        matched_flag = flag
                        break
            if matched_flag:
                cursor = index + len(matched_flag)
                if cursor < length and record[cursor] == "=":
                    cursor += 1
                elif cursor < length and record[cursor] in " \t":
                    while cursor < length and record[cursor] in " \t":
                        cursor += 1
                else:
                    index += 1
                    continue
                if cursor < length:
                    end = _secret_value_end(record, cursor)
                    index = emit_replacement(cursor, cursor, end)
                    continue

        # URL user:password credentials before '@'. The scan never rewinds.
        if (ch == "h" or ch == "H") and (lower.startswith("https://", index) or lower.startswith("http://", index)):
            scheme_len = 8 if lower.startswith("https://", index) else 7
            auth_start = index + scheme_len
            cursor = auth_start
            colon = -1
            at = -1
            while cursor < length and record[cursor] not in "/ \t\r\n":
                if record[cursor] == ":" and colon < 0:
                    colon = cursor
                elif record[cursor] == "@":
                    at = cursor
                    break
                cursor += 1
            if colon >= auth_start and at > colon:
                pieces.append(record[literal_start:auth_start])
                pieces.append("<redacted>@")
                literal_start = at + 1
                index = at + 1
                continue

        # Common standalone token prefixes used by desktop launchers/logs.
        if lower.startswith("gh", index) and index + 4 < length and record[index + 2] in "opsur" and record[index + 3] == "_":
            cursor = index + 4
            while cursor < length and (record[cursor].isalnum() or record[cursor] == "_"):
                cursor += 1
            if cursor - (index + 4) >= 16:
                pieces.append(record[literal_start:index])
                pieces.append("<redacted-github-token>")
                literal_start = cursor
                index = cursor
                continue
        if lower.startswith("sk-", index):
            cursor = index + 3
            while cursor < length and (record[cursor].isalnum() or record[cursor] in "_-"):
                cursor += 1
            if cursor - (index + 3) >= 16:
                pieces.append(record[literal_start:index])
                pieces.append("<redacted-api-key>")
                literal_start = cursor
                index = cursor
                continue

        # NAME [spaces] (=|:) [spaces] VALUE. Consume each identifier once.
        if _identifier_char(ch) and (index == 0 or not _identifier_char(record[index - 1])):
            name_end = index + 1
            while name_end < length and _identifier_char(record[name_end]):
                name_end += 1
            cursor = name_end
            while cursor < length and record[cursor] in " \t":
                cursor += 1
            if cursor < length and record[cursor] in "=:":
                separator = cursor
                cursor += 1
                while cursor < length and record[cursor] in " \t":
                    cursor += 1
                if cursor < length and _secret_identifier(record[index:name_end]):
                    end = _secret_value_end(record, cursor)
                    index = emit_replacement(cursor, cursor, end)
                    continue
            index = name_end
            continue

        index += 1

    pieces.append(record[literal_start:])
    return "".join(pieces)


def redact_sensitive_text(text: str) -> str:
    """Redact common credentials with bounded linear work per record."""
    value = _redact_private_key_blocks(str(text))
    result: list[str] = []
    offset = 0
    while offset < len(value):
        newline = value.find("\n", offset)
        end = len(value) if newline < 0 else newline + 1
        result.append(_redact_record(value[offset:end]))
        offset = end
    return "".join(result)

def write_log(text: str, path: Path | str | None = None, *, append: bool = False) -> Path:
    target = Path(path) if path is not None else new_log_path()
    text = redact_sensitive_text(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    if append:
        existed = target.exists() and target.stat().st_size > 0
        with target.open("a", encoding="utf-8") as handle:
            if existed:
                handle.write("\n\n")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        with target.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    _fsync_directory(target.parent)
    _bound_one_log(target)
    _rotate_logs(protected=target)
    # Mirror only after the authoritative file has been durably written and
    # bounded; even a header-only first write therefore has a durable mirror.
    _mirror_log_to_phone(target)
    return target


def finalize_log(path: Path | str) -> Path:
    """Durably seal, bound, rotate and mirror a live-streamed log."""
    target = Path(path)
    try:
        with target.open("ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(target.parent)
    except OSError:
        return target
    _bound_one_log(target)
    _rotate_logs(protected=target)
    _mirror_log_to_phone(target)
    return target


def latest_log() -> Path | None:
    try:
        files = list(logs_dir().glob("*.log"))
    except OSError:
        return None
    if not files:
        return None
    def key(path: Path) -> tuple[float, str]:
        try:
            return path.stat().st_mtime, str(path)
        except OSError:
            return 0.0, str(path)
    return max(files, key=key)
