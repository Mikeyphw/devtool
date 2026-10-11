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
_RUNTIME_LOG_DIR: Path | None = None


def state_dir() -> Path:
    return _xdg_state_dir()


def set_log_dir_override(path: str | Path | None) -> None:
    global _RUNTIME_LOG_DIR
    text = str(path or "").strip()
    _RUNTIME_LOG_DIR = Path(text).expanduser() if text else None


def get_log_dir_override() -> Path | None:
    """Return the current in-process log-dir override without resolving it."""

    return _RUNTIME_LOG_DIR


def logs_dir() -> Path:
    env_override = os.environ.get("DEVTOOL_LOG_DIR", "").strip()
    path = _RUNTIME_LOG_DIR or (Path(env_override).expanduser() if env_override else state_dir() / "logs")
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_log_identifier(value: str | None) -> str:
    text = Path(str(value or "").strip()).stem
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-_")
    return text[:96]


def new_log_path(identifier: str | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    suffix = _safe_log_identifier(identifier)
    return logs_dir() / f"{stamp}{'-' + suffix if suffix else ''}.log"


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
        mirrored = mirror_root / path.name
        _durable_copy(path, mirrored)
        _schedule_retention(mirror_root, protected=mirrored)
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


def _rotate_log_root(root: Path, *, protected: Path | None = None) -> None:
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
    victims: list[Path] = []
    survivors: list[Path] = []
    for path in files:
        try:
            if protected_resolved is not None and path.resolve(strict=False) == protected_resolved:
                survivors.append(path)
                continue
            if now - path.stat().st_mtime > max_age:
                victims.append(path)
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
        victims.append(victim)
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
        victims.append(victim)
        survivors.remove(victim)

    # Retention policy selects candidates; LIFE is the only destructive
    # authority.  The current Devtool repository RUN catalog is projected into
    # the ownership view, so a log that remains a canonical RUN attachment is
    # protected even when age/count/size policy would otherwise select it.
    unique_victims = list(dict.fromkeys(path.resolve(strict=False) for path in victims))
    if not unique_victims:
        return
    try:
        from .cleanup_lifecycle import (
            CleanupReference,
            CleanupScope,
            GeneratedOutputDescriptor,
            apply_cleanup_plan,
            build_cleanup_ownership_view,
            compile_cleanup_plan,
        )

        authority_root = Path(os.environ.get("DEVTOOL_REPO_ROOT") or Path.cwd()).expanduser().resolve(strict=False)
        descriptors = [
            GeneratedOutputDescriptor.create(
                path, owner="devtool-logstore", provider="logstore", category="diagnostic-log",
                producer_id=f"log:{path.name}", lifecycle="retained", rebuild_cost="unknown",
                scope=CleanupScope.USER_GLOBAL, destination="local", managed=True, published=False,
                provenance={"retention_policy": "age-count-size", "log_root": str(root)},
            )
            for path in unique_victims
        ]
        explicit_refs = []
        if protected_resolved is not None:
            explicit_refs.append(CleanupReference(
                reference_id=f"logstore-active:{protected_resolved}", source="logstore-active",
                path=str(protected_resolved), protective=True, reason="active log is protected from rotation",
            ))
        ownership = build_cleanup_ownership_view(
            authority_root, descriptors, references=explicit_refs,
            policy_generation="life-r03b-log-retention-v1", strict_authorities=True,
        )
        plan = compile_cleanup_plan(
            ownership, scope=CleanupScope.USER_GLOBAL, destination="local", include_user_global=True,
        )
        apply_cleanup_plan(plan, ownership=ownership, confirm=True)
    except Exception:
        # Rotation is secondary housekeeping.  If any canonical protection
        # authority is unavailable or rejects the candidate, fail closed by
        # retaining the log rather than bypassing LIFE with direct unlink.
        return


def _schedule_retention(root: Path, *, protected: Path | None = None) -> None:
    """Keep log durability synchronous, but dispatch costly LIFE retention on Termux.

    A persistent request and a single-worker lock make this recoverable and
    coalesced; on dispatch failure, preserve the historical synchronous path.
    LIFE remains the *only* authority for deleting log files.
    """
    try:
        from .log_retention_worker import schedule_retention
    except ImportError:
        # Windows and other hosts without POSIX flock retain the original
        # synchronous cleanup behavior and protection authority.
        schedule_retention = None

    if schedule_retention is not None and schedule_retention(root, protected=protected):
        return
    _rotate_log_root(root, protected=protected)


def _rotate_logs(*, protected: Path | None = None) -> None:
    _schedule_retention(logs_dir(), protected=protected)


# Credential redaction is deliberately scanner-based rather than relying on
# backtracking-heavy expressions. Every input character is visited a bounded
# number of times and individual records are capped before inspection.
_SECRET_WORDS = {"TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "CREDENTIALS", "AUTH", "APIKEY", "PRIVATEKEY", "AUTHTOKEN", "ACCESSTOKEN", "REFRESHTOKEN", "CLIENTSECRET"}
_SECRET_PAIRS = {("API", "KEY"), ("AUTH", "TOKEN"), ("PRIVATE", "KEY"), ("ACCESS", "TOKEN"), ("REFRESH", "TOKEN")}
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
    """Return True only for credential-like identifiers, not arbitrary substrings.

    Log diagnostics frequently contain identifiers such as
    ``managedDriveConfigurationIsCredentialFreeAtUiBoundary``.  Treating
    ``CREDENTIAL`` as a substring in that identifier destroys useful failure
    messages.  Secret matching therefore works on underscore/hyphen-delimited
    identifier components and known component pairs.
    """
    normalized = name.upper().replace("-", "_")
    parts = tuple(part for part in normalized.split("_") if part)
    if not parts:
        return False
    if any(part in _SECRET_WORDS for part in parts):
        return True
    return any(pair in zip(parts, parts[1:]) for pair in _SECRET_PAIRS)


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



def redact_sensitive_value(value, *, key: str = ""):
    """Recursively redact credential-like values before JSON serialization.

    Text redaction alone can miss structured objects where the sensitive
    meaning lives in the key (for example {"token": "abc"}). This helper
    preserves primitive types and shapes while replacing secret-bearing leaves.
    """
    if isinstance(value, dict):
        redacted = {}
        for raw_key, raw_item in value.items():
            item_key = str(raw_key)
            redacted[item_key] = redact_sensitive_value(raw_item, key=item_key)
        return redacted
    if isinstance(value, (list, tuple)):
        redacted_items = []
        redact_next = False
        for item in value:
            if redact_next and isinstance(item, str):
                redacted_items.append("<redacted>" if item else item)
                redact_next = False
                continue
            redacted = redact_sensitive_value(item, key=key)
            redacted_items.append(redacted)
            if isinstance(item, str) and item.strip().split("=", 1)[0].lower() in _CLI_SECRET_FLAGS and "=" not in item:
                redact_next = True
        return redacted_items
    if isinstance(value, str):
        if key and _secret_identifier(key):
            return "<redacted>" if value else value
        return redact_sensitive_text(value)
    return value

class StreamingLogRedactor:
    """Stateful redactor for line/chunk-oriented live subprocess output.

    ``redact_sensitive_text`` safely handles complete private-key blocks, but
    live command output reaches Devtool incrementally.  This redactor remembers
    when a PEM/OpenSSH private-key block has started so body lines cannot leak
    into the durable log before the closing marker arrives.
    """

    def __init__(self) -> None:
        self._inside_private_key = False

    @staticmethod
    def _private_key_begin(line: str) -> int:
        upper = line.upper()
        marker = "-----BEGIN "
        start = upper.find(marker)
        if start < 0:
            return -1
        end = upper.find("-----", start + len(marker))
        if end < 0:
            return -1
        return start if "PRIVATE KEY" in upper[start:end] else -1

    @staticmethod
    def _private_key_end(line: str) -> tuple[int, int] | None:
        upper = line.upper()
        marker = "-----END "
        start = upper.find(marker)
        if start < 0:
            return None
        end = upper.find("-----", start + len(marker))
        if end < 0 or "PRIVATE KEY" not in upper[start:end]:
            return None
        return start, end + len("-----")

    def redact(self, text: str) -> str:
        value = str(text)
        if not value:
            return value
        output: list[str] = []
        for line in value.splitlines(keepends=True):
            if self._inside_private_key:
                closing = self._private_key_end(line)
                if closing is None:
                    continue
                self._inside_private_key = False
                suffix = line[closing[1]:]
                if suffix:
                    output.append(redact_sensitive_text(suffix))
                continue

            begin = self._private_key_begin(line)
            if begin < 0:
                output.append(redact_sensitive_text(line))
                continue

            prefix = line[:begin]
            if prefix:
                output.append(redact_sensitive_text(prefix))
            output.append("<redacted:private-key>")
            if line.endswith("\n"):
                output.append("\n")
            closing = self._private_key_end(line[begin:])
            self._inside_private_key = closing is None
            if closing is not None:
                absolute_end = begin + closing[1]
                suffix = line[absolute_end:]
                if suffix and suffix != "\n":
                    output.append(redact_sensitive_text(suffix))
        # splitlines() returns [] for some edge cases only when value is empty,
        # already handled above.  A chunk without a newline is still processed.
        return "".join(output)

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
