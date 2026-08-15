from __future__ import annotations

"""PRoot-Distro command-phase execution backend.

Devtool remains on the host.  Only planned external commands enter the guest,
so the guest never needs devtool installed.
"""

from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable, Mapping

from .execution_environments import NATIVE_TERMUX, PathMapping, detect_current_environment, translate_host_path
from .models import CommandSpec, ProjectConfig

_SAFE_ENV = {"CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ"}
_BLOCKED_PREFIXES = (
    "ANDROID_", "DEVTOOL_BUILD_", "DEVTOOL_CHROOT_", "DEVTOOL_SSH_",
    "GRADLE_USER_HOME", "JAVA_HOME", "HOME", "PATH", "PWD", "TMPDIR",
    "UV_CACHE_DIR", "PIP_CACHE_DIR", "PDM_CACHE_DIR", "POETRY_CACHE_DIR",
    "RUFF_CACHE_DIR", "MYPY_CACHE_DIR", "TOX_WORK_DIR", "CARGO_TARGET_DIR",
    "PYTHONPYCACHEPREFIX", "SKBUILD_BUILD_DIR",
    "npm_config_cache", "YARN_CACHE_FOLDER", "BUN_INSTALL_CACHE_DIR",
    "XDG_CACHE_HOME", "CCACHE_DIR",
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
)

_DEFAULT_GUEST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_GUEST_EXEC_NAME = "devtool-guest-exec.sh"
_RESERVED_PROOT_ENV = {
    "DEVTOOL_EXECUTION_CONTEXT",
    "DEVTOOL_PROOT_DISTRO",
    "DEVTOOL_PROOT_CLEAN_PATH",
}


@dataclass(slots=True)
class ProotRunnerConfig:
    distro: str = ""
    user: str = "root"
    cwd: str = "/workspace/project"
    repo_mount: str = "/workspace/project"
    auto_bind_repo: bool = True
    binds: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    shared_home: bool = False
    shared_tmp: bool = False
    isolated: bool = False
    minimal: bool = False
    stage_dir: str = "~/.cache/devtool/proot-stage"
    stage_mount: str = "/run/devtool"
    cache_dir: str = "~/.cache/devtool/proot"
    cache_mount: str = "/var/cache/devtool"


def _bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def resolve_proot_config(project_config: ProjectConfig, args) -> ProotRunnerConfig:
    raw = project_config.runner_options.get("proot", {})
    options = dict(raw) if isinstance(raw, dict) else {}
    distro = str(getattr(args, "proot_distro", "") or options.get("distro") or "").strip()
    user = str(getattr(args, "proot_user", "") or options.get("user") or "root").strip() or "root"
    repo_mount = str(getattr(args, "proot_repo_mount", "") or options.get("repo_mount") or "/workspace/project").strip()
    cwd = str(getattr(args, "proot_cwd", "") or options.get("cwd") or repo_mount).strip() or repo_mount
    binds = _list(options.get("bind")) + list(getattr(args, "proot_bind", []) or [])
    env_values = _list(options.get("env")) + list(getattr(args, "proot_env", []) or [])
    auto_bind = _bool(options.get("auto_bind_repo"), True)
    if bool(getattr(args, "proot_no_auto_bind_repo", False)):
        auto_bind = False
    cfg = ProotRunnerConfig(
        distro=distro,
        user=user,
        cwd=cwd,
        repo_mount=repo_mount,
        auto_bind_repo=auto_bind,
        binds=binds,
        env=env_values,
        shared_home=bool(getattr(args, "proot_shared_home", False)) or _bool(options.get("shared_home")),
        shared_tmp=bool(getattr(args, "proot_shared_tmp", False)) or _bool(options.get("shared_tmp")),
        isolated=bool(getattr(args, "proot_isolated", False)) or _bool(options.get("isolated")),
        minimal=bool(getattr(args, "proot_minimal", False)) or _bool(options.get("minimal")),
        stage_dir=str(getattr(args, "proot_stage_dir", "") or options.get("stage_dir") or "~/.cache/devtool/proot-stage"),
        stage_mount=str(options.get("stage_mount") or "/run/devtool"),
        cache_dir=str(options.get("cache_dir") or "~/.cache/devtool/proot"),
        cache_mount=str(options.get("cache_mount") or "/var/cache/devtool"),
    )
    validate_proot_config(cfg)
    return cfg


def validate_proot_config(config: ProotRunnerConfig) -> None:
    if not config.distro:
        raise ValueError("PRoot-Distro execution requires --proot-distro DISTRO or [proot].distro.")
    if config.isolated and config.minimal:
        raise ValueError("PRoot-Distro --isolated and --minimal are mutually exclusive.")
    for value in (config.cwd, config.repo_mount, config.stage_mount, config.cache_mount):
        if not value.startswith("/"):
            raise ValueError(f"PRoot guest path must be absolute: {value}")
    for bind in config.binds:
        if ":" in bind:
            _src, dst = bind.rsplit(":", 1)
            if dst and not dst.startswith("/"):
                raise ValueError(f"PRoot bind destination must be absolute: {bind}")
    for item in config.env:
        if "=" not in item or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", item):
            raise ValueError(f"PRoot environment entry must be NAME=VALUE: {item}")
        key = item.split("=", 1)[0]
        if key in _RESERVED_PROOT_ENV or key.startswith((
            "DEVTOOL_EXECUTION_", "DEVTOOL_PROOT_", "DEVTOOL_INSIDE_", "DEVTOOL_CHROOT_", "DEVTOOL_SSH_"
        )):
            raise ValueError(f"PRoot environment entry overrides a devtool-managed backend variable: {key}")


def proot_distro_available() -> str:
    return shutil.which(os.environ.get("DEVTOOL_PROOT_DISTRO_COMMAND", "proot-distro")) or ""


def installed_distros(executable: str | None = None) -> tuple[str, ...] | None:
    """Return installed distro aliases, or ``None`` when discovery itself fails."""

    command = executable or proot_distro_available()
    if not command:
        return None
    try:
        result = subprocess.run([command, "list", "-q"], text=True, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return tuple(line.strip().split()[0] for line in result.stdout.splitlines() if line.strip())


def _parse_bind(value: str) -> tuple[str, str]:
    if ":" in value:
        source, guest = value.rsplit(":", 1)
        resolved = str(Path(source).expanduser().resolve())
        return resolved, guest
    resolved = str(Path(value).expanduser().resolve())
    return resolved, resolved


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _is_termux_host_path(value: str) -> bool:
    normalized = os.path.realpath(os.path.expanduser(value))
    return normalized == "/data/data/com.termux" or normalized.startswith("/data/data/com.termux/")


def _is_termux_prefix_path(value: str) -> bool:
    prefix = os.path.realpath(str(os.environ.get("PREFIX") or "/data/data/com.termux/files/usr"))
    normalized = os.path.realpath(os.path.expanduser(value))
    return normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")


def _validate_mapping_conflicts(mappings: list[PathMapping]) -> None:
    seen: dict[str, str] = {}
    for mapping in mappings:
        guest = mapping.guest.rstrip("/") or "/"
        host = mapping.host.rstrip("/") or "/"
        previous = seen.get(guest)
        if previous is not None and previous != host:
            raise ValueError(
                f"PRoot guest destination {guest} is bound from both {previous} and {host}. "
                "Disable automatic repository binding or choose a distinct --proot-bind destination."
            )
        seen[guest] = host


def _base_mappings(config: ProotRunnerConfig, repo_root: Path) -> list[PathMapping]:
    result: list[PathMapping] = []
    if config.auto_bind_repo:
        result.append(PathMapping(str(repo_root.resolve()), config.repo_mount))
    for item in config.binds:
        source, guest = _parse_bind(item)
        if not Path(source).exists():
            raise ValueError(f"PRoot bind source does not exist: {source}")
        result.append(PathMapping(source, guest))
    _validate_mapping_conflicts(result)
    return sorted(result, key=lambda item: len(item.host), reverse=True)


def _stage_external_file(path: Path, stage: Path, stage_mount: str, *, dry_run: bool = False) -> str:
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    target = stage / f"{digest}-{path.name}"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        source_stat = path.stat()
        if (
            not target.exists()
            or target.stat().st_mtime_ns != source_stat.st_mtime_ns
            or target.stat().st_size != source_stat.st_size
        ):
            shutil.copy2(path, target)
    return stage_mount.rstrip("/") + "/" + target.name


def _translate_path_value(
    value: str, mappings: list[PathMapping], *, cwd: str, stage: Path, stage_mount: str, dry_run: bool = False
) -> str:
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        return value
    translated = translate_host_path(expanded, mappings, cwd=cwd)
    if translated != expanded:
        return translated
    candidate = Path(expanded)
    if candidate.is_file():
        return _stage_external_file(candidate, stage, stage_mount, dry_run=dry_run)
    if candidate.is_dir() or _is_termux_host_path(expanded):
        raise ValueError(
            f"Absolute host path is not mapped into the PRoot guest: {expanded}. "
            "Add --proot-bind HOST:GUEST or use a guest-native path via --proot-env/configuration."
        )
    # A path such as /usr/bin/python3 may intentionally name a guest-only path
    # that does not exist on the host. Preserve it rather than inventing a host
    # translation.
    return value


def _translate_token(
    token: str, mappings: list[PathMapping], *, cwd: str, stage: Path, stage_mount: str,
    dry_run: bool = False, command: bool = False,
) -> str:
    # Keep option prefixes intact while translating absolute host paths in the
    # common --flag=/path and -DNAME=/path forms.
    if "=" in token:
        prefix, value = token.split("=", 1)
        translated = _translate_path_value(
            value, mappings, cwd=cwd, stage=stage, stage_mount=stage_mount, dry_run=dry_run
        )
        if translated != value:
            return f"{prefix}={translated}"
    expanded = os.path.expanduser(token)
    candidate = Path(expanded) if os.path.isabs(expanded) else None
    if command and candidate is not None and candidate.is_file():
        if _is_termux_prefix_path(expanded):
            # Provider discovery belongs to the guest. Termux package commands
            # include both ELF binaries (clang/java) and scripts (gradle, npm,
            # etc.); neither kind may be copied into a glibc PRoot container.
            return candidate.name
        if _is_elf(candidate):
            if _is_termux_host_path(expanded):
                raise ValueError(
                    f"Native Termux ELF executable cannot be staged into a PRoot Linux guest: {expanded}. "
                    "Use a guest-installed command name or bind a guest-compatible executable."
                )
            # Conventional /usr/bin or /bin paths are commonly identical guest paths.
            # Preserve them; arbitrary host binaries elsewhere need an explicit bind.
            if not expanded.startswith(("/usr/", "/bin/", "/sbin/", "/opt/")):
                raise ValueError(
                    f"Absolute host executable is outside the PRoot mappings: {expanded}. "
                    "Use a guest command path or --proot-bind."
                )
            return token
    return _translate_path_value(
        token, mappings, cwd=cwd, stage=stage, stage_mount=stage_mount, dry_run=dry_run
    )



def _needs_devtool_python_helper(specs: Iterable[CommandSpec]) -> bool:
    return any("devtool.python_support" in str(token) for spec in specs for token in spec.argv)


def _stage_devtool_python_helper(stage: Path, *, dry_run: bool = False) -> None:
    """Stage the stdlib-only Python helper without requiring devtool in the guest."""

    if dry_run:
        return
    source_dir = Path(__file__).resolve().parent
    package_dir = stage / "pythonpath" / "devtool"
    package_dir.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "python_support.py", "xdg.py"):
        source = source_dir / name
        target = package_dir / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)


def _guest_exec_wrapper(stage: Path, stage_mount: str, *, dry_run: bool = False) -> str:
    target = stage / _GUEST_EXEC_NAME
    content = (
        "set -eu\n"
        f'PATH="${{DEVTOOL_PROOT_CLEAN_PATH:-{_DEFAULT_GUEST_PATH}}}"\n'
        "export PATH\n"
        'exec "$@"\n'
    )
    if not dry_run and (not target.exists() or target.read_text(encoding="utf-8", errors="replace") != content):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o700)
    return stage_mount.rstrip("/") + "/" + _GUEST_EXEC_NAME


def _explicit_guest_path(config: ProotRunnerConfig) -> str:
    for item in reversed(config.env):
        if item.startswith("PATH="):
            return item.split("=", 1)[1]
    return _DEFAULT_GUEST_PATH

def _guest_env(
    config: ProotRunnerConfig, spec: CommandSpec, *, mappings: list[PathMapping], cwd: str, stage: Path,
    project_env: Mapping[str, str] | None = None, dry_run: bool = False, helper_pythonpath: bool = False,
) -> list[str]:
    values: list[str] = ["DEVTOOL_EXECUTION_CONTEXT=proot-distro", f"DEVTOOL_PROOT_DISTRO={config.distro}"]
    run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
    if run_id:
        values.append(f"DEVTOOL_RUN_ID={run_id}")
    cache = config.cache_mount.rstrip("/")
    values.extend([
        f"GRADLE_USER_HOME={cache}/gradle",
        f"PIP_CACHE_DIR={cache}/pip",
        f"UV_CACHE_DIR={cache}/uv",
        f"PDM_CACHE_DIR={cache}/pdm",
        f"POETRY_CACHE_DIR={cache}/poetry",
        f"RUFF_CACHE_DIR={cache}/ruff",
        f"MYPY_CACHE_DIR={cache}/mypy",
        f"TOX_WORK_DIR={cache}/tox",
        f"PYTHONPYCACHEPREFIX={cache}/pycache",
        f"CARGO_HOME={cache}/cargo-home",
        f"CARGO_TARGET_DIR={cache}/native/cargo",
        f"SKBUILD_BUILD_DIR={cache}/native/scikit-build",
        f"npm_config_cache={cache}/npm",
        f"YARN_CACHE_FOLDER={cache}/yarn",
        f"BUN_INSTALL_CACHE_DIR={cache}/bun",
        f"XDG_CACHE_HOME={cache}/xdg",
        f"CCACHE_DIR={cache}/ccache",
    ])
    explicit_pythonpath = ""
    for item in config.env:
        if item.startswith("PYTHONPATH="):
            explicit_pythonpath = item.split("=", 1)[1]
            break
    if helper_pythonpath:
        helper_path = config.stage_mount.rstrip("/") + "/pythonpath"
        values.append(f"PYTHONPATH={helper_path + (':' + explicit_pythonpath if explicit_pythonpath else '')}")
    elif explicit_pythonpath:
        values.append(f"PYTHONPATH={explicit_pythonpath}")
    safe_keys = set(_SAFE_ENV)
    safe_keys.update(key for key in os.environ if key.startswith("LC_"))
    for key in sorted(safe_keys):
        value = os.environ.get(key)
        if value:
            values.append(f"{key}={value}")
    for item in config.env:
        if item.startswith(("PATH=", "PYTHONPATH=")):
            continue
        values.append(item)
    values.append(f"DEVTOOL_PROOT_CLEAN_PATH={_explicit_guest_path(config)}")
    merged_env = dict(project_env or {})
    merged_env.update(spec.env)
    for key, value in sorted(merged_env.items()):
        if (
            any(key == prefix or key.startswith(prefix) for prefix in _BLOCKED_PREFIXES)
            or key in _RESERVED_PROOT_ENV
            or key.startswith(("DEVTOOL_EXECUTION_", "DEVTOOL_PROOT_", "DEVTOOL_INSIDE_"))
        ):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            text = str(value)
            translated = _translate_path_value(
                text, mappings, cwd=cwd, stage=stage, stage_mount=config.stage_mount, dry_run=dry_run
            )
            values.append(f"{key}={translated}")
    return values


def wrap_specs_for_proot_distro(
    specs: list[CommandSpec], config: ProotRunnerConfig, *, repo_root: Path, dry_run: bool = False,
    project_env: Mapping[str, str] | None = None,
) -> list[CommandSpec]:
    validate_proot_config(config)
    current_environment = detect_current_environment()
    if current_environment != NATIVE_TERMUX and (config.shared_tmp or config.isolated or config.minimal):
        raise ValueError(
            "PRoot-Distro --shared-tmp, --isolated, and --minimal are available only on Termux hosts."
        )
    executable = proot_distro_available()
    if not executable and not dry_run:
        raise ValueError("proot-distro executable was not found. Install the Termux proot-distro package.")
    executable = executable or "proot-distro"
    if not dry_run:
        names = installed_distros(executable)
        if names is None:
            raise ValueError("Could not query installed PRoot-Distro containers with `proot-distro list -q`.")
        if config.distro not in names:
            installed = ", ".join(names) if names else "none"
            raise ValueError(f"PRoot-Distro container '{config.distro}' is not installed (installed: {installed}).")
    stage_root = Path(config.stage_dir).expanduser() / f"{config.distro}-{os.getpid()}"
    created_stage = False
    try:
        cache_root = Path(config.cache_dir).expanduser() / config.distro
        if not dry_run:
            stage_root.mkdir(parents=True, exist_ok=True)
            cache_root.mkdir(parents=True, exist_ok=True)
            created_stage = True
        mappings = _base_mappings(config, repo_root)
        stage_mapping = PathMapping(str(stage_root.resolve()), config.stage_mount)
        cache_mapping = PathMapping(str(cache_root.resolve()), config.cache_mount)
        _validate_mapping_conflicts([*mappings, stage_mapping, cache_mapping])
        mappings.extend([stage_mapping, cache_mapping])
        bind_pairs = [(item.host, item.guest) for item in mappings]
        helper_pythonpath = _needs_devtool_python_helper(specs)
        if helper_pythonpath:
            _stage_devtool_python_helper(stage_root, dry_run=dry_run)
        guest_exec = _guest_exec_wrapper(stage_root, config.stage_mount, dry_run=dry_run)
        wrapped: list[CommandSpec] = []
        for spec in specs:
            host_cwd = str(Path(spec.cwd or repo_root).expanduser().resolve())
            if config.cwd != config.repo_mount:
                # An explicitly distinct --proot-cwd is authoritative. This is
                # important when the repository is intentionally bound at more
                # than one guest path.
                guest_cwd = config.cwd
            else:
                guest_cwd = translate_host_path(host_cwd, mappings, cwd=str(repo_root))
                if guest_cwd == host_cwd:
                    guest_cwd = config.cwd
            argv = [
                _translate_token(
                    str(token), mappings, cwd=host_cwd, stage=stage_root, stage_mount=config.stage_mount,
                    dry_run=dry_run, command=index == 0,
                )
                for index, token in enumerate(spec.argv)
            ]
            command = [executable, "login", config.distro, "--user", config.user, "--work-dir", guest_cwd]
            if config.shared_home:
                command.append("--shared-home")
            if config.shared_tmp:
                command.append("--shared-tmp")
            if config.isolated:
                command.append("--isolated")
            if config.minimal:
                command.append("--minimal")
            for source, guest in bind_pairs:
                command.extend(["--bind", f"{source}:{guest}"])
            for item in _guest_env(
                config, spec, mappings=mappings, cwd=host_cwd, stage=stage_root, project_env=project_env, dry_run=dry_run,
                helper_pythonpath=helper_pythonpath,
            ):
                command.extend(["--env", item])
            command.extend(["--", "/bin/sh", guest_exec, *argv])
            metadata = dict(spec.metadata)
            metadata.setdefault("progress_argv", list(spec.argv))
            metadata.setdefault("progress_cwd", spec.cwd or str(repo_root))
            metadata.update({
                "execution_mode": "proot-distro",
                "proot_distro": config.distro,
                "proot_user": config.user,
                "proot_guest_cwd": guest_cwd,
                "proot_bindings": [{"host": h, "guest": g} for h, g in bind_pairs],
                "proot_stage_dir": str(stage_root),
                "proot_stage_owned": not dry_run,
            })
            wrapped.append(replace(spec, argv=command, cwd=str(repo_root), env={}, metadata=metadata))
        return wrapped
    except Exception:
        if created_stage:
            shutil.rmtree(stage_root, ignore_errors=True)
        raise


def cleanup_proot_staging(specs: Iterable[CommandSpec]) -> None:
    """Remove host-owned PRoot helper staging after the command plan completes."""

    seen: set[str] = set()
    for spec in specs:
        if not bool(spec.metadata.get("proot_stage_owned")):
            continue
        raw = str(spec.metadata.get("proot_stage_dir") or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        shutil.rmtree(Path(raw), ignore_errors=True)
