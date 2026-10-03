from __future__ import annotations

"""PRoot-Distro command-phase execution backend.

Devtool remains on the host.  Only planned external commands enter the guest,
so the guest never needs devtool installed.
"""

from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import subprocess
import uuid
from typing import Iterable, Mapping

from .execution_environments import NATIVE_TERMUX, PathMapping, detect_current_environment, translate_host_path
from .models import CommandSpec, ProjectConfig

_SAFE_ENV = {"CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ"}
_BLOCKED_ENV_EXACT = {
    "GRADLE_USER_HOME", "JAVA_HOME", "HOME", "PATH", "PWD", "TMPDIR",
    "UV_CACHE_DIR", "PIP_CACHE_DIR", "PDM_CACHE_DIR", "POETRY_CACHE_DIR",
    "RUFF_CACHE_DIR", "MYPY_CACHE_DIR", "TOX_WORK_DIR", "CARGO_HOME", "CARGO_TARGET_DIR",
    "PYTHONPYCACHEPREFIX", "SKBUILD_BUILD_DIR",
    "npm_config_cache", "YARN_CACHE_FOLDER", "BUN_INSTALL_CACHE_DIR",
    "XDG_CACHE_HOME", "CCACHE_DIR",
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
}
_BLOCKED_ENV_PREFIXES = ("ANDROID_", "DEVTOOL_BUILD_", "DEVTOOL_CHROOT_", "DEVTOOL_SSH_")

_DEFAULT_GUEST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_GUEST_EXEC_NAME = "devtool-guest-exec.sh"
_RESERVED_PROOT_ENV = {
    "DEVTOOL_EXECUTION_CONTEXT",
    "DEVTOOL_PROOT_DISTRO",
    "DEVTOOL_PROOT_CLEAN_PATH",
    "DEVTOOL_RUN_ID",
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



def _proot_stage_run_id() -> str:
    return str(os.environ.get("DEVTOOL_RUN_ID") or f"proot-stage-{os.getpid()}").strip()


def _proot_stage_session(stage_root: str | Path) -> str:
    path = Path(stage_root).expanduser().resolve(strict=False)
    return f"proot-stage:{path.name}"


def _proot_stage_descriptor(stage_root: str | Path, *, repo_root: str | Path, run_id: str, session: str) -> "GeneratedOutputDescriptor":
    from .cleanup_lifecycle import CleanupScope, GeneratedOutputDescriptor

    return GeneratedOutputDescriptor.create(
        Path(stage_root).expanduser().resolve(strict=False),
        owner="DIST",
        provider="proot-distro",
        category="provider-staging",
        producer_id=run_id or session or "proot-stage",
        lifecycle="transient",
        rebuild_cost="low",
        scope=CleanupScope.PROJECT,
        destination="local",
        provenance={
            "run_id": run_id,
            "session": session,
            "provider": "proot-distro",
            "authority": "RUN/LIFE",
            "normal_ingress": "proot-distro",
        },
        managed=True,
        published=False,
    )


def _register_proot_stage_resource(repo_root: str | Path, stage_root: str | Path, *, run_id: str, session: str) -> str:
    from .provider_resource_registry import register_provider_resources

    descriptor = _proot_stage_descriptor(stage_root, repo_root=repo_root, run_id=run_id, session=session)
    register_provider_resources(repo_root, (descriptor,))
    return descriptor.resource_id


def _release_and_cleanup_proot_stage(repo_root: str | Path, stage_root: str | Path, *, run_id: str = "", session: str = "", reason: str = "proot staging completed") -> tuple[bool, str]:
    from .cleanup_lifecycle import (
        CleanupScope,
        apply_cleanup_plan,
        build_cleanup_ownership_view,
        compile_cleanup_plan,
        persist_cleanup_receipt,
    )
    from .provider_resource_registry import release_provider_resources

    root = Path(repo_root).expanduser().resolve(strict=False)
    path = Path(stage_root).expanduser().resolve(strict=False)
    resolved_run = str(run_id or _proot_stage_run_id()).strip()
    resolved_session = str(session or _proot_stage_session(path)).strip()
    descriptor = _proot_stage_descriptor(path, repo_root=root, run_id=resolved_run, session=resolved_session)
    release_provider_resources(
        root,
        run_id=resolved_run,
        session=resolved_session,
        reason=reason,
    )
    ownership = build_cleanup_ownership_view(
        root,
        (descriptor,),
        policy_generation="life-r03b-proot-stage-v1",
        strict_authorities=True,
    )
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT, destination="local")
    receipt = apply_cleanup_plan(plan, ownership=ownership, confirm=True)
    receipt_path = persist_cleanup_receipt(root, receipt, run_id=resolved_run)
    if path.exists() or path.is_symlink():
        row = next((item for item in receipt.results if item.resource_id == descriptor.resource_id), None)
        detail = str(getattr(row, "reason", "") or receipt.status or "not removed")
        return False, f"PRoot staging retained at {path}: LIFE cleanup did not remove it ({detail}); receipt {receipt_path}"
    return True, f"PRoot staging removed through LIFE receipt {receipt.receipt_id}"

def _normalize_guest_path(value: str, *, label: str = "PRoot guest path") -> str:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw or not raw.startswith("/"):
        raise ValueError(f"{label} destination must be absolute and non-empty: {value}")
    # POSIX permits implementation-defined semantics for //foo. PRoot bind
    # destinations must have one canonical spelling so overlap checks cannot
    # be bypassed with repeated slashes, dot segments, or parent segments.
    normalized = posixpath.normpath("/" + raw.lstrip("/"))
    if not normalized.startswith("/"):
        raise ValueError(f"{label} must be absolute: {value}")
    return normalized


def _guest_paths_overlap(left: str, right: str) -> bool:
    a = _normalize_guest_path(left)
    b = _normalize_guest_path(right)
    return a == b or a == "/" or b == "/" or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _managed_env_name(key: str) -> bool:
    # The DEVTOOL_* namespace is owned by the host-side orchestrator.  Allowing
    # arbitrary project/config values to populate it lets a guest spoof run,
    # backend, cache, or recovery identity even when individual variables have
    # not yet been enumerated here.
    return (
        key.startswith("DEVTOOL_")
        or key in _BLOCKED_ENV_EXACT
        or any(key.startswith(prefix) for prefix in _BLOCKED_ENV_PREFIXES)
    )


def validate_proot_config(config: ProotRunnerConfig) -> None:
    if not config.distro:
        raise ValueError("PRoot-Distro execution requires --proot-distro DISTRO or [proot].distro.")
    if config.isolated and config.minimal:
        raise ValueError("PRoot-Distro --isolated and --minimal are mutually exclusive.")
    config.cwd = _normalize_guest_path(config.cwd)
    config.repo_mount = _normalize_guest_path(config.repo_mount)
    config.stage_mount = _normalize_guest_path(config.stage_mount)
    config.cache_mount = _normalize_guest_path(config.cache_mount)
    for index, bind in enumerate(list(config.binds)):
        if not str(bind).strip():
            raise ValueError("PRoot bind must have a non-empty source")
        if ":" in bind:
            src, dst = bind.rsplit(":", 1)
            if not src.strip() or not dst.strip():
                raise ValueError(f"PRoot bind must have non-empty source and destination: {bind}")
            config.binds[index] = f"{src}:{_normalize_guest_path(dst, label='PRoot bind destination')}"
    for item in config.env:
        if "=" not in item or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", item):
            raise ValueError(f"PRoot environment entry must be NAME=VALUE: {item}")
        key = item.split("=", 1)[0]
        controlled_guest_override = key in {"PATH", "PYTHONPATH"}
        if (
            (not controlled_guest_override and _managed_env_name(key))
            or key in _RESERVED_PROOT_ENV
            or key.startswith((
                "DEVTOOL_EXECUTION_", "DEVTOOL_PROOT_", "DEVTOOL_INSIDE_", "DEVTOOL_CHROOT_", "DEVTOOL_SSH_"
            ))
        ):
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
        return resolved, _normalize_guest_path(guest, label="PRoot bind destination")
    resolved = str(Path(value).expanduser().resolve())
    # A destination omitted by the user is meaningful only when the host path
    # is itself a valid absolute POSIX path. Canonicalize it independently of
    # pathlib so guest comparisons are lexical and deterministic.
    return resolved, _normalize_guest_path(resolved, label="PRoot bind destination")


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
    canonical: list[tuple[str, str]] = []
    for mapping in mappings:
        guest = _normalize_guest_path(mapping.guest)
        host = str(Path(mapping.host).expanduser().resolve(strict=False))
        for previous_guest, previous_host in canonical:
            if guest == previous_guest and host == previous_host:
                continue
            if _guest_paths_overlap(guest, previous_guest):
                raise ValueError(
                    f"PRoot guest destinations overlap and would be bound from both {previous_host} and {host}: {previous_guest} / {guest}. "
                    "Overlapping binds can shadow repository, cache, or stage content; choose disjoint destinations."
                )
        canonical.append((guest, host))


def _host_paths_overlap(left: Path, right: Path) -> bool:
    a = left.expanduser().resolve(strict=False)
    b = right.expanduser().resolve(strict=False)
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def _mapping_covers_host_path(mapping: PathMapping, path: Path) -> bool:
    host = Path(mapping.host).expanduser().resolve(strict=False)
    target = path.expanduser().resolve(strict=False)
    try:
        target.relative_to(host)
        return True
    except ValueError:
        return False


def _base_mappings(config: ProotRunnerConfig, repo_root: Path) -> list[PathMapping]:
    result: list[PathMapping] = []
    resolved_repo = repo_root.expanduser().resolve()
    if config.auto_bind_repo:
        result.append(PathMapping(str(resolved_repo), config.repo_mount))
    for item in config.binds:
        source, guest = _parse_bind(item)
        if not Path(source).exists():
            raise ValueError(f"PRoot bind source does not exist: {source}")
        result.append(PathMapping(source, guest))
    _validate_mapping_conflicts(result)
    if not any(_mapping_covers_host_path(mapping, resolved_repo) for mapping in result):
        raise ValueError(
            f"PRoot mappings do not cover the active repository {resolved_repo}. "
            "Devtool does not support validating an unrelated independent guest checkout; "
            "enable automatic repository binding or bind the repository explicitly."
        )
    return sorted(result, key=lambda item: len(Path(item.host).parts), reverse=True)


def _stage_external_file(path: Path, stage: Path, stage_mount: str, *, dry_run: bool = False) -> str:
    # Stage identity follows immutable content, not a mutable host pathname.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
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
    # Canonicalize a host path before matching it against repository/bind
    # mappings.  Otherwise /repo/../outside can prefix-match /repo and escape
    # the guest repository after translation.  ``resolve(strict=False)`` also
    # resolves existing symlink prefixes without requiring the final path to
    # exist.
    canonical = str(Path(expanded).resolve(strict=False))
    translated = translate_host_path(canonical, mappings, cwd=cwd)
    if translated != canonical:
        return _normalize_guest_path(translated, label="translated PRoot guest path")
    candidate = Path(canonical)
    if candidate.is_file():
        try:
            executable = bool(candidate.stat().st_mode & 0o111)
        except OSError:
            executable = False
        if executable:
            raise ValueError(
                f"Executable host file is not a typed PRoot data input and will not be staged: {expanded}. "
                "Install/select the tool inside the guest or bind a guest-compatible tool explicitly."
            )
        return _stage_external_file(candidate, stage, stage_mount, dry_run=dry_run)
    if candidate.is_dir() or _is_termux_host_path(expanded):
        raise ValueError(
            f"Absolute host path is not mapped into the PRoot guest: {canonical}. "
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
        'if [ -n "${DEVTOOL_PROOT_SECRET_ENV_FILE:-}" ]; then\n'
        '  [ -f "$DEVTOOL_PROOT_SECRET_ENV_FILE" ] || { echo "Devtool protected PRoot secret file is missing" >&2; exit 78; }\n'
        '  . "$DEVTOOL_PROOT_SECRET_ENV_FILE"\n'
        '  unset DEVTOOL_PROOT_SECRET_ENV_FILE\n'
        'fi\n'
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
            _managed_env_name(key)
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
    stage_root = Path(config.stage_dir).expanduser() / f"{config.distro}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    created_stage = False
    try:
        cache_root = Path(config.cache_dir).expanduser() / config.distro
        # Validate every mapping/overlap boundary before creating a managed
        # directory. Invalid isolation configuration is therefore zero-mutation.
        mappings = _base_mappings(config, repo_root)
        resolved_stage = stage_root.resolve(strict=False)
        resolved_cache = cache_root.resolve(strict=False)
        if _host_paths_overlap(resolved_stage, resolved_cache):
            raise ValueError("PRoot managed stage and cache directories must be disjoint")
        # Managed stage/cache roots must not alias any ordinary bind, especially
        # the repository bind.  If they overlap, the guest can reach the same
        # managed bytes through two names and mutate/retain them outside the
        # lifecycle Devtool controls.
        for mapping in mappings:
            mapped_host = Path(mapping.host).expanduser().resolve(strict=False)
            if _host_paths_overlap(resolved_stage, mapped_host):
                raise ValueError(
                    f"PRoot managed stage directory overlaps a bound host path: {resolved_stage} / {mapped_host}"
                )
            if _host_paths_overlap(resolved_cache, mapped_host):
                raise ValueError(
                    f"PRoot managed cache directory overlaps a bound host path: {resolved_cache} / {mapped_host}"
                )
        stage_mapping = PathMapping(str(resolved_stage), config.stage_mount)
        cache_mapping = PathMapping(str(resolved_cache), config.cache_mount)
        _validate_mapping_conflicts([*mappings, stage_mapping, cache_mapping])
        mappings.extend([stage_mapping, cache_mapping])
        proot_run_id = _proot_stage_run_id()
        proot_session = _proot_stage_session(stage_root)
        provider_resource_id = ""
        if not dry_run:
            stage_root.mkdir(parents=True, exist_ok=True)
            created_stage = True
            cache_root.mkdir(parents=True, exist_ok=True)
            provider_resource_id = _register_proot_stage_resource(
                repo_root, stage_root, run_id=proot_run_id, session=proot_session
            )
        bind_pairs = [(item.host, item.guest) for item in mappings]
        federated_guest_root = translate_host_path(str(Path(repo_root).expanduser().resolve()), mappings, cwd=str(repo_root))
        if federated_guest_root == str(Path(repo_root).expanduser().resolve()):
            federated_guest_root = config.repo_mount
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
            # Secret-like environment values are never passed through
            # ``proot-distro --env KEY=value`` because that serializes them in
            # the host process argv.  Stage them in the already-private managed
            # operation directory and pass only the non-secret guest filename.
            from .ssh_resilience import split_sensitive_env
            config_env_map: dict[str, str] = {}
            config_env_passthrough: list[str] = []
            for raw_item in config.env:
                if "=" not in str(raw_item):
                    config_env_passthrough.append(str(raw_item))
                    continue
                key, value = str(raw_item).split("=", 1)
                config_env_map[key] = value
            public_config_env, sensitive_config_env = split_sensitive_env(config_env_map)
            public_project_env, sensitive_project_env = split_sensitive_env(dict(project_env or {}))
            public_spec_env, sensitive_spec_env = split_sensitive_env(dict(spec.env or {}))
            sensitive_env = {**sensitive_config_env, **sensitive_project_env, **sensitive_spec_env}
            safe_config = replace(
                config,
                env=[*config_env_passthrough, *(f"{key}={value}" for key, value in sorted(public_config_env.items()))],
            )
            safe_spec = replace(spec, env=public_spec_env)
            secret_guest_path = ""
            if sensitive_env:
                secret_name = f"secret-env-{uuid.uuid4().hex[:12]}.sh"
                secret_host_path = stage_root / secret_name
                secret_guest_path = config.stage_mount.rstrip("/") + "/" + secret_name
                if not dry_run:
                    lines = [f"export {key}={shlex.quote(str(value))}" for key, value in sorted(sensitive_env.items())]
                    secret_host_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    secret_host_path.chmod(0o600)
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
                safe_config, safe_spec, mappings=mappings, cwd=host_cwd, stage=stage_root, project_env=public_project_env, dry_run=dry_run,
                helper_pythonpath=helper_pythonpath,
            ):
                command.extend(["--env", item])
            if secret_guest_path:
                command.extend(["--env", f"DEVTOOL_PROOT_SECRET_ENV_FILE={secret_guest_path}"])
            metadata = dict(spec.metadata)
            from .federation_transport import attach_federation_transport_metadata, build_federation_attestation_shell
            metadata = attach_federation_transport_metadata(
                metadata,
                local_root=str(Path(repo_root).expanduser().resolve()),
                execution_environment="proot-distro",
                remote_root=federated_guest_root,
                path_mapping="proot-bind",
            )
            attestation_probe = build_federation_attestation_shell(metadata, remote_root=federated_guest_root)
            if attestation_probe:
                command.extend([
                    "--", "/bin/sh", "-c",
                    attestation_probe + '; exec /bin/sh "$@"',
                    "devtool-federation-attest", guest_exec, *argv,
                ])
            else:
                command.extend(["--", "/bin/sh", guest_exec, *argv])
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
                "proot_repository_root": str(Path(repo_root).expanduser().resolve(strict=False)),
                "proot_stage_lifecycle_authority": "LIFE",
                "proot_stage_provider_resource_id": provider_resource_id,
                "proot_run_id": proot_run_id,
                "proot_provider_session": proot_session,
            })
            if sensitive_env:
                metadata["proot_sensitive_env_keys"] = sorted(sensitive_env)
                metadata["proot_sensitive_env_staged"] = True
            if bool(metadata.get("android_signing_backend_secret_pending")):
                from .android.signing import SIGNING_ENV_KEYS
                signing_keys = sorted(key for key in sensitive_env if key in SIGNING_ENV_KEYS)
                if not signing_keys:
                    raise ValueError(
                        "Android signing required protected PRoot secret staging, but no signing secret reached the backend"
                    )
                metadata["android_signing_backend_secret_pending"] = False
                metadata["android_signing_backend_secret_staged"] = True
                metadata["android_signing_secret_transport_backend"] = "proot-distro"
                metadata["android_signing_secret_keys"] = signing_keys
            wrapped.append(replace(spec, argv=command, cwd=str(repo_root), env={}, metadata=metadata))
        return wrapped
    except Exception as exc:
        if created_stage:
            try:
                ok, detail = _release_and_cleanup_proot_stage(
                    repo_root,
                    stage_root,
                    run_id=locals().get("proot_run_id", ""),
                    session=locals().get("proot_session", ""),
                    reason="proot staging setup failed",
                )
            except Exception as cleanup_exc:  # pragma: no cover - defensive note preservation
                detail = f"PRoot staging retained at {stage_root}: LIFE cleanup failed during setup: {cleanup_exc}"
                ok = False
            if not ok and hasattr(exc, "add_note"):
                exc.add_note(detail)
        raise


def cleanup_proot_staging(specs: Iterable[CommandSpec], *, retain: bool = False) -> list[str]:
    """Release completed host-owned PRoot staging through LIFE authority.

    R03B treats PRoot staging as a concrete provider resource: successful
    completion releases the active provider-resource reference, then LIFE
    revalidates ownership/snapshot/tracked-source protection before removing the
    local stage tree.  Missing repository authority fails closed by retaining the
    directory instead of falling back to direct ``rmtree``.
    """

    seen: set[str] = set()
    warnings: list[str] = []
    for spec in specs:
        if not bool(spec.metadata.get("proot_stage_owned")):
            continue
        raw = str(spec.metadata.get("proot_stage_dir") or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw)
        if retain:
            if path.exists():
                warnings.append(
                    f"PRoot staging retained at {path}: execution did not complete successfully"
                )
            continue
        if not (path.exists() or path.is_symlink()):
            continue
        repo_root = str(spec.metadata.get("proot_repository_root") or spec.cwd or "").strip()
        if not repo_root:
            warnings.append(
                f"PRoot staging retained at {path}: repository cleanup authority is unavailable"
            )
            continue
        try:
            ok, detail = _release_and_cleanup_proot_stage(
                repo_root,
                path,
                run_id=str(spec.metadata.get("proot_run_id") or ""),
                session=str(spec.metadata.get("proot_provider_session") or ""),
                reason="proot staging completed",
            )
        except Exception as exc:
            warnings.append(f"PRoot staging retained at {path}: LIFE cleanup failed: {exc}")
            continue
        if not ok:
            warnings.append(detail)
    return warnings
