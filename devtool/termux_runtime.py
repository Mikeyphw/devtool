from __future__ import annotations

"""Termux-specific runtime hardening helpers.

This module intentionally contains no Android Gradle runner logic.  It models
host storage/runtime/resource concerns that apply to every project kind and is
safe to import on ordinary desktop hosts.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import hashlib
import shlex
import warnings
import shutil
import struct
import subprocess
from typing import Iterable, Mapping

from .execution_environments import is_termux_host

_SHARED_PREFIXES = (
    "/sdcard",
    "/storage/emulated/",
    "/storage/self/primary",
    "/mnt/media_rw/",
)


@dataclass(frozen=True, slots=True)
class TermuxResourceProfile:
    name: str
    max_workers: int
    cpu_limit: int
    gradle_heap_mb: int
    gradle_metaspace_mb: int
    memory_guard_mb: int
    native_jobs: int


@dataclass(frozen=True, slots=True)
class RuntimeFailureHint:
    kind: str
    detail: str


_ELF_MACHINE_NAMES = {
    3: "x86",
    40: "arm",
    62: "x86_64",
    183: "aarch64",
    243: "riscv64",
}


def _host_elf_machine() -> int | None:
    machine = platform.machine().strip().lower()
    aliases = {
        "aarch64": 183, "arm64": 183,
        "arm": 40, "armv7l": 40, "armv8l": 40,
        "x86_64": 62, "amd64": 62,
        "i386": 3, "i486": 3, "i586": 3, "i686": 3, "x86": 3,
        "riscv64": 243,
    }
    return aliases.get(machine)


def _elf_machine_and_interpreter(path: Path) -> tuple[int | None, str]:
    """Return ELF machine and PT_INTERP without executing the candidate."""

    try:
        with path.open("rb") as handle:
            header = handle.read(64)
            if len(header) < 20 or header[:4] != b"\x7fELF":
                return None, ""
            elf_class = header[4]
            data = header[5]
            if elf_class not in {1, 2} or data not in {1, 2}:
                return None, ""
            endian = "<" if data == 1 else ">"
            machine = struct.unpack_from(endian + "H", header, 18)[0]
            if elf_class == 2:
                if len(header) < 58:
                    return machine, ""
                phoff = struct.unpack_from(endian + "Q", header, 32)[0]
                phentsize = struct.unpack_from(endian + "H", header, 54)[0]
                phnum = struct.unpack_from(endian + "H", header, 56)[0]
            else:
                if len(header) < 46:
                    return machine, ""
                phoff = struct.unpack_from(endian + "I", header, 28)[0]
                phentsize = struct.unpack_from(endian + "H", header, 42)[0]
                phnum = struct.unpack_from(endian + "H", header, 44)[0]
            if not phoff or not phentsize or not phnum or phnum > 512:
                return machine, ""
            for index in range(phnum):
                handle.seek(phoff + index * phentsize)
                entry = handle.read(phentsize)
                if len(entry) < min(phentsize, 16) or len(entry) < 8:
                    break
                p_type = struct.unpack_from(endian + "I", entry, 0)[0]
                if p_type != 3:  # PT_INTERP
                    continue
                if elf_class == 2:
                    if len(entry) < 40:
                        return machine, ""
                    offset = struct.unpack_from(endian + "Q", entry, 8)[0]
                    size = struct.unpack_from(endian + "Q", entry, 32)[0]
                else:
                    if len(entry) < 20:
                        return machine, ""
                    offset = struct.unpack_from(endian + "I", entry, 4)[0]
                    size = struct.unpack_from(endian + "I", entry, 16)[0]
                if not size or size > 4096:
                    return machine, ""
                handle.seek(offset)
                raw = handle.read(size)
                return machine, raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            return machine, ""
    except (OSError, struct.error, ValueError):
        return None, ""


def native_termux_executable_hint(
    argv: Iterable[str], *, cwd: str | Path | None = None, env: Mapping[str, str] | None = None
) -> RuntimeFailureHint | None:
    """Reject a clearly incompatible ELF before native Termux execution.

    Architecture alone is insufficient: conventional Linux aarch64 binaries
    typically request an ld-linux/ld-musl interpreter that Android/Bionic does
    not provide. Static same-architecture ELF files remain eligible.
    """

    env = env or os.environ
    values = [str(item) for item in argv]
    if not values or not is_termux_host(env):
        return None
    command = values[0]
    if os.path.isabs(os.path.expanduser(command)) or "/" in command:
        candidate = Path(os.path.expanduser(command))
        if not candidate.is_absolute():
            candidate = Path(cwd or os.getcwd()) / candidate
    else:
        resolved = shutil.which(command, path=str(env.get("PATH") or os.environ.get("PATH", "")))
        if not resolved:
            return None
        candidate = Path(resolved)
    if not candidate.is_file():
        return None
    machine, interpreter = _elf_machine_and_interpreter(candidate)
    if machine is None:
        return None
    expected = _host_elf_machine()
    if expected is not None and machine != expected:
        actual_name = _ELF_MACHINE_NAMES.get(machine, f"machine-{machine}")
        expected_name = _ELF_MACHINE_NAMES.get(expected, f"machine-{expected}")
        return RuntimeFailureHint(
            "abi-mismatch",
            f"Executable {candidate} targets {actual_name}, but native Termux is {expected_name}. "
            "Use a Termux-native binary or execute it through chroot, PRoot-Distro, or SSH.",
        )
    interp = interpreter.strip().lower()
    if interp and ("ld-linux" in interp or "ld-musl" in interp or "glibc" in interp):
        return RuntimeFailureHint(
            "libc-mismatch",
            f"Executable {candidate} requests conventional Linux interpreter {interpreter}, not Android/Bionic. "
            "Use a Termux-native binary or execute it through chroot, PRoot-Distro, or SSH.",
        )
    return None


def is_android_shared_storage(path: str | Path) -> bool:
    value = os.path.realpath(os.path.expanduser(os.fspath(path)))
    if value == "/sdcard" or value.startswith("/sdcard/"):
        return True
    if any(value == prefix.rstrip("/") or value.startswith(prefix) for prefix in _SHARED_PREFIXES[1:]):
        return True
    # Portable/removable volumes are exposed below /storage/<volume-id>.  The
    # private Termux app tree never lives below /storage, so treating these as
    # shared storage is both conservative and consistent with Android's
    # noexec/symlink restrictions.
    if value == "/storage" or value.startswith("/storage/"):
        return True
    return False


def shared_storage_message(path: str | Path) -> str:
    return (
        f"Build/runtime path is on Android shared storage: {Path(path).expanduser()}. "
        "Shared storage is normally noexec and lacks normal symlink/case-sensitive semantics; "
        "keep source/build trees under Termux $HOME and export only finalized artifacts."
    )


def termux_cache_root(env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    raw = str(env.get("DEVTOOL_TERMUX_CACHE_ROOT", "") or "").strip()
    if raw:
        explicit = Path(raw).expanduser()
        if not explicit.is_absolute():
            raise ValueError("DEVTOOL_TERMUX_CACHE_ROOT must be an absolute path")
        return explicit.resolve(strict=False)
    from .xdg import cache_home
    home_cache = Path(str(env.get("HOME", "~"))).expanduser() / ".cache"
    base = cache_home(env)
    # Android shared storage is unsuitable for executable/build caches. Honor
    # XDG only while it remains on a private filesystem; an explicit
    # DEVTOOL_TERMUX_CACHE_ROOT remains the deliberate escape hatch.
    if is_android_shared_storage(base):
        base = home_cache
    return base / "devtool" / "host-termux"


def _mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemAvailable:"):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 2048


def select_termux_resource_profile(name: str = "balanced", *, available_mb: int | None = None, cpus: int | None = None) -> TermuxResourceProfile:
    requested = str(name or "balanced").strip().lower().replace("_", "-")
    if requested in {"auto", "automatic"}:
        requested = "balanced"
    if requested not in {"conservative", "balanced", "performance"}:
        raise ValueError(f"Unknown Termux resource profile: {name}")
    memory = max(256, int(available_mb or _mem_available_mb()))
    cores = max(1, int(cpus or os.cpu_count() or 1))
    if requested == "conservative":
        workers = 1
        heap = min(1024, max(512, memory // 3))
        meta = min(512, max(256, heap // 2))
        guard = 0
    elif requested == "performance":
        workers = min(4, cores)
        heap = min(2048, max(768, memory // 2))
        meta = min(768, max(384, heap // 2))
        guard = 0
    else:
        workers = min(2, cores)
        heap = min(1280, max(640, memory * 2 // 5))
        meta = min(768, max(320, heap // 2))
        guard = 0
    return TermuxResourceProfile(requested, workers, min(workers, 2), heap, meta, guard, workers)


def apply_termux_resource_defaults(runner_options: dict, *, profile_name: str = "balanced", env: Mapping[str, str] | None = None) -> TermuxResourceProfile | None:
    if not is_termux_host(env or os.environ):
        return None
    normalized = str(profile_name or "balanced").strip().lower().replace("_", "-")
    if normalized in {"off", "none", "disabled", "disable"}:
        return None
    profile = select_termux_resource_profile(profile_name)
    gradle = runner_options.setdefault("gradle", {})
    if not isinstance(gradle, dict):
        gradle = {}
        runner_options["gradle"] = gradle
    validation = runner_options.setdefault("validation", {})
    if not isinstance(validation, dict):
        validation = {}
        runner_options["validation"] = validation
    for key, value in {
        "max_workers": profile.max_workers,
        "no_daemon": True,
        "parallel": False,
    }.items():
        gradle.setdefault(key, value)
    for key, value in {
        "max_workers": profile.max_workers,
        "cpu_limit": profile.cpu_limit,
        "gradle_heap_mb": profile.gradle_heap_mb,
        "memory_guard_mb": profile.memory_guard_mb,
        "parallel": False,
    }.items():
        validation.setdefault(key, value)
    native = runner_options.setdefault("native_runtime", {})
    if isinstance(native, dict):
        native.setdefault("jobs", profile.native_jobs)
    return profile


def classify_runtime_failure(output: str, *, native_termux: bool = True) -> RuntimeFailureHint | None:
    text = str(output or "")
    lower = text.lower()
    if native_termux and (
        "cannot link executable" in lower
        or "wrong elf interpreter" in lower
        or "ld-linux-aarch64" in lower
        or ("glibc" in lower and "not found" in lower)
    ):
        return RuntimeFailureHint("libc-mismatch", "Executable appears to target conventional Linux/glibc rather than native Termux/Android Bionic.")
    if native_termux and "exec format error" in lower:
        return RuntimeFailureHint("abi-mismatch", "Executable architecture or ABI does not match the current Termux runtime.")
    if "could not resolve host" in lower or "temporary failure in name resolution" in lower or "name or service not known" in lower:
        return RuntimeFailureHint("dns", "Network dependency resolution failed at DNS lookup.")
    if "certificate verify failed" in lower or "ssl certificate problem" in lower or "tls handshake" in lower:
        return RuntimeFailureHint("tls", "TLS/certificate negotiation failed while accessing a dependency or tool provider.")
    if "timed out" in lower or "connection timeout" in lower or "connection timed out" in lower:
        return RuntimeFailureHint("network-timeout", "Network access timed out; this may be transient, unlike a confirmed HTTP 404.")
    http_404_patterns = (
        re.compile(r"\bhttp(?:/\d(?:\.\d)?)?\s+(?:error\s+)?404\b", re.IGNORECASE),
        re.compile(r"\bhttp\s+error\s+404\b", re.IGNORECASE),
        re.compile(r"\b404\s+not\s+found\b", re.IGNORECASE),
        re.compile(r"\b(?:url|uri)\b[^\n]{0,160}\b404\b", re.IGNORECASE),
        re.compile(r"\b404\b[^\n]{0,160}\bhttps?://", re.IGNORECASE),
    )
    if any(
        pattern.search(line)
        for line in text.splitlines()
        for pattern in http_404_patterns
    ):
        return RuntimeFailureHint(
            "http-404",
            "The requested upstream artifact does not exist; retries should not be treated as transient.",
        )
    return None


def gradle_stall_message(idle_seconds: float) -> str:
    seconds = max(1, int(round(float(idle_seconds))))
    return (
        f"Devtool stall diagnostic: no Gradle output for {seconds} seconds. "
        "Dependency resolution, repository connectivity, or configuration may still be active; "
        "the process was not stopped. For diagnosis retry with Gradle --offline and --info where appropriate."
    )


def termux_shebang_warning(path: str | Path, *, env: Mapping[str, str] | None = None) -> str:
    """Describe a conventional-Linux shebang that may fail on native Termux."""

    env = env or os.environ
    if not is_termux_host(env):
        return ""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return ""
    try:
        with candidate.open("rb") as handle:
            first = handle.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    if not first.startswith("#!"):
        return ""
    interpreter = first[2:].strip().split()[0] if first[2:].strip() else ""
    if interpreter.startswith("/bin/") or interpreter.startswith("/usr/bin/"):
        prefix = str(env.get("PREFIX") or "/data/data/com.termux/files/usr")
        return (
            f"Script {candidate} uses conventional Linux shebang {interpreter}; native Termux binaries "
            f"normally live under {prefix}/bin. Invoke it through a resolved interpreter or use a Linux execution backend if needed."
        )
    return ""



def adapt_termux_script_argv(
    argv: Iterable[str], *, cwd: str | Path | None = None, env: Mapping[str, str] | None = None
) -> tuple[list[str], str]:
    """Use a Termux-resolved interpreter for a conventional Linux shebang.

    The project file is not modified.  Rewriting happens only for native
    Termux execution and only when the shebang's absolute interpreter is
    missing but an equivalent executable can be resolved from Termux PATH.
    """

    values = [str(item) for item in argv]
    env = env or os.environ
    if not values or not is_termux_host(env):
        return values, ""
    command = Path(os.path.expanduser(values[0]))
    if not command.is_absolute():
        command = Path(cwd or os.getcwd()) / command
    if not command.is_file():
        return values, ""
    try:
        with command.open("rb") as handle:
            first = handle.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return values, ""
    if not first.startswith("#!"):
        return values, ""
    try:
        words = shlex.split(first[2:].strip(), posix=True)
    except ValueError:
        return values, termux_shebang_warning(command, env=env)
    if not words:
        return values, ""
    interpreter = words[0]
    if not (interpreter.startswith("/bin/") or interpreter.startswith("/usr/bin/")):
        return values, ""
    # On native Termux, conventional /usr/bin and /bin are not authoritative
    # interpreter locations even if a host/test namespace happens to expose
    # them. Resolve the requested tool from Termux PATH instead.
    tool = ""
    extra: list[str] = []
    if interpreter == "/usr/bin/env" and len(words) > 1:
        env_words = words[1:]
        # GNU/coreutils env -S exists specifically so a shebang can carry a
        # split command string. Parse that command without treating -S itself
        # as the interpreter name. Also skip the portable env flags/assignments
        # that do not select an executable.
        if env_words and env_words[0] in {"-S", "--split-string"}:
            split_source = " ".join(env_words[1:])
            try:
                env_words = shlex.split(split_source, posix=True)
            except ValueError:
                return values, termux_shebang_warning(command, env=env)
        index = 0
        while index < len(env_words):
            item = env_words[index]
            if item in {"-i", "--ignore-environment"}:
                index += 1
                continue
            if item in {"-u", "--unset"}:
                index += 2
                continue
            if item.startswith("--unset=") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", item):
                index += 1
                continue
            break
        if index < len(env_words):
            tool = env_words[index]
            extra = env_words[index + 1:]
    else:
        tool = Path(interpreter).name
        extra = words[1:]
    replacement = shutil.which(tool, path=str(env.get("PATH") or os.environ.get("PATH", ""))) if tool else None
    if not replacement:
        return values, termux_shebang_warning(command, env=env)
    message = (
        f"Native Termux adapted conventional shebang {interpreter} for {command} "
        f"to resolved interpreter {replacement}."
    )
    return [replacement, *extra, str(command), *values[1:]], message

def tmux_available() -> bool:
    return bool(shutil.which("tmux"))




def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def export_artifacts(paths: Iterable[str | Path], destination: str | Path) -> list[Path]:
    """Export verified files without clobbering a different artifact.

    Basenames are convenient on shared storage, but two modules can emit the
    same filename. Preserve that convenience only while identity is unambiguous;
    otherwise suffix the name with the source content digest.
    """
    dest = Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    claimed: dict[str, str] = {}
    for raw in paths:
        source = Path(raw).expanduser()
        if not source.is_file():
            continue
        digest = _sha256_file(source)
        target = dest / source.name
        existing_digest = None
        if target.is_file():
            try:
                existing_digest = _sha256_file(target)
            except OSError:
                existing_digest = None
        prior = claimed.get(source.name)
        if (prior is not None and prior != digest) or (existing_digest is not None and existing_digest != digest):
            suffix = "".join(source.suffixes)
            stem = source.name[:-len(suffix)] if suffix else source.name
            target = dest / f"{stem}-{digest}{suffix}"
            counter = 1
            while target.exists() and (not target.is_file() or _sha256_file(target) != digest):
                target = dest / f"{stem}-{digest}-{counter}{suffix}"
                counter += 1
        claimed[source.name] = digest
        if source.resolve() != target.resolve():
            if not target.is_file() or _sha256_file(target) != digest:
                shutil.copy2(source, target)
        copied.append(target)
    return copied



def termux_runtime_doctor_rows(config, *, env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    env = env or os.environ
    if not is_termux_host(env):
        return []
    options = config.runner_options.get("termux", {}) if hasattr(config, "runner_options") else {}
    options = dict(options) if isinstance(options, dict) else {}
    storage = options.get("storage", {}) if isinstance(options.get("storage", {}), dict) else {}
    profile_name = str(options.get("resource_profile") or "balanced")
    if profile_name.strip().lower().replace("_", "-") in {"off", "none", "disabled", "disable"}:
        profile_detail = "disabled (automatic Termux resource defaults bypassed)"
    else:
        try:
            profile = select_termux_resource_profile(profile_name)
            profile_detail = f"{profile.name}: workers={profile.max_workers}, heap={profile.gradle_heap_mb}MiB, native_jobs={profile.native_jobs}"
        except ValueError as exc:
            profile_detail = f"INVALID: {exc}"
    wake = bool(shutil.which("termux-wake-lock") and shutil.which("termux-wake-unlock"))
    try:
        from .proot_distro_runner import installed_distros, proot_distro_available
        proot = proot_distro_available()
        distros = installed_distros(proot) if proot else ()
        distro_detail = "discovery failed" if distros is None else (", ".join(distros) if distros else "none")
    except Exception:
        proot = ""
        distro_detail = "discovery failed"
    return [
        ("termux_cache", str(termux_cache_root(env))),
        ("termux_resource_profile", profile_detail),
        ("termux_shared_storage_policy", str(storage.get("shared_storage_policy") or "warn")),
        ("termux_repo_storage", "shared" if is_android_shared_storage(config.repo_root) else "private"),
        ("termux_wake_lock", "READY" if wake else "MISSING"),
        ("tmux", "READY" if tmux_available() else "MISSING"),
        ("proot_distro", f"READY: {proot}" if proot else "MISSING"),
        ("proot_containers", distro_detail),
    ]

@contextmanager
def termux_wake_lock(mode: str = "auto", *, env: Mapping[str, str] | None = None):
    env = env or os.environ
    normalized = str(mode or "auto").strip().lower()
    if normalized not in {"auto", "on", "true", "1", "required", "false", "off", "0", "never"}:
        raise ValueError(f"Unknown Termux wake-lock mode: {mode}. Expected auto, on, or off.")
    if normalized in {"false", "off", "0", "never"} or not is_termux_host(env):
        yield False
        return
    lock = shutil.which("termux-wake-lock")
    unlock = shutil.which("termux-wake-unlock")
    required = normalized in {"on", "true", "1", "required"}
    if not lock or not unlock:
        if required:
            raise RuntimeError("Termux wake lock was requested but termux-wake-lock/termux-wake-unlock are unavailable.")
        yield False
        return
    try:
        acquired = subprocess.run(
            [lock], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        acquired = False
    try:
        if required and not acquired:
            raise RuntimeError("termux-wake-lock failed or timed out.")
        yield acquired
    finally:
        if acquired:
            try:
                result = subprocess.run(
                    [unlock], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                )
                if result.returncode != 0:
                    warnings.warn(
                        f"termux-wake-unlock exited with status {result.returncode}; wake-lock cleanup could not be verified.",
                        RuntimeWarning, stacklevel=2,
                    )
            except (OSError, subprocess.TimeoutExpired) as exc:
                warnings.warn(
                    f"termux-wake-unlock failed; wake-lock cleanup could not be verified: {exc}",
                    RuntimeWarning, stacklevel=2,
                )
