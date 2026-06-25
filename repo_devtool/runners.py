from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path


from .config import generate_template
from .console_utils import create_console, is_termux_environment, resolve_cli_path
from .diagnostics import parse_issues
from .logstore import write_log
from .models import CommandSpec, OperationResult, ProjectConfig

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


def _prepare_termux_dotnet(argv: list[str]) -> tuple[list[str], str | None]:
    if not argv or argv[0] != "dotnet" or not is_termux_environment():
        return argv, None

    dotnet_path = _resolve_termux_dotnet()
    if not dotnet_path or not shutil.which("proot"):
        return argv, None

    tmpdir = tempfile.mkdtemp(prefix="devtool-termux-dotnet-")
    etc_dir = Path(tmpdir) / "etc"
    etc_dir.mkdir(parents=True, exist_ok=True)
    (etc_dir / "os-release").write_text(_TERMUX_DOTNET_OS_RELEASE, encoding="utf-8")

    return ["proot", "-b", f"{etc_dir}:/etc", dotnet_path, *argv[1:]], tmpdir



def build_specs(config: ProjectConfig, action: str) -> list[CommandSpec]:
    if action == "validate":
        return build_specs(config, "restore") + build_specs(config, "build") + build_specs(config, "test")
    if action == "wine-validate":
        return build_specs(config, "restore") + build_specs(config, "build") + build_specs(config, "wine-test")

    if config.kind == "dotnet":
        return _dotnet_specs(config, action)
    if config.kind == "python":
        return _python_specs(config, action)

    # unknown kind relies on explicit commands
    return _generic_specs(config, action)


def _maybe_explicit(command_name: str, text: str, env: dict[str, str] | None = None) -> list[CommandSpec]:
    if text:
        return [CommandSpec(command_name, shlex.split(text), env=env or {})]
    return []


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


def run_specs(
    config: ProjectConfig,
    specs: list[CommandSpec],
    *,
    verbose: bool,
    dry_run: bool = False,
    continue_on_error: bool = False,
) -> tuple[list[OperationResult], str]:
    results: list[OperationResult] = []
    raw_sections: list[str] = []

    for spec in specs:
        effective_argv, cleanup_dir = _prepare_termux_dotnet(spec.argv)

        if dry_run:
            output = "$ " + " ".join(effective_argv)
            log_path = str(write_log(output))
            results.append(OperationResult(spec.name, effective_argv, 0, 0.0, output, [], skipped=True, log_path=log_path))
            raw_sections.append(output)
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            continue

        env = os.environ.copy()
        env.update(config.env)
        env.update(spec.env)

        started = time.perf_counter()
        try:
            if verbose:
                console.print(f"[cyan]$ {' '.join(shlex.quote(a) for a in effective_argv)}[/cyan]")
                proc = subprocess.run(
                    effective_argv,
                    cwd=spec.cwd or str(config.repo_root),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                output = proc.stdout
                if output:
                    print(output, end="")
            else:
                with console.status(f"[bold cyan]Running {spec.name}[/bold cyan]", spinner="dots"):
                    proc = subprocess.run(
                        effective_argv,
                        cwd=spec.cwd or str(config.repo_root),
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    output = proc.stdout
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        duration = time.perf_counter() - started
        issues = parse_issues(config.repo_root, spec.name, output)
        command_line = "$ " + " ".join(shlex.quote(a) for a in effective_argv)
        raw_text = command_line + "\n" + output
        log_path = str(write_log(raw_text))
        result = OperationResult(spec.name, effective_argv, proc.returncode, duration, output, issues, log_path=log_path)
        results.append(result)
        raw_sections.append(raw_text)

        if proc.returncode != 0 and not continue_on_error:
            break

    return results, "\n\n".join(raw_sections)


def apply_overlay(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False) -> OperationResult:
    artifact_path = resolve_cli_path(artifact)
    argv = ["unzip", "-o", str(artifact_path), "-d", str(repo_root)]
    return _apply_archive(repo_root, artifact_path, argv, delete_artifact=True, snapshot=False, force=force, yes=yes, dry_run=dry_run)


def apply_snapshot(repo_root: Path, artifact: str, *, force: bool = False, yes: bool = False, dry_run: bool = False) -> OperationResult:
    artifact_path = resolve_cli_path(artifact)
    return _apply_archive(repo_root, artifact_path, ["snapshot", str(artifact_path)], delete_artifact=True, snapshot=True, force=force, yes=yes, dry_run=dry_run)


def _apply_archive(repo_root: Path, artifact_path: Path, argv: list[str], *, delete_artifact: bool, snapshot: bool, force: bool, yes: bool, dry_run: bool) -> OperationResult:
    start = time.perf_counter()
    if dry_run:
        text = "$ " + " ".join(argv)
        path = str(write_log(text))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 0, 0.0, text, [], skipped=True, log_path=path)

    if not artifact_path.exists():
        output = f"Artifact not found: {artifact_path}"
        path = str(write_log(output))
        return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, 1, 0.0, output, [], log_path=path)

    try:
        if snapshot:
            tmpdir = Path(tempfile.mkdtemp(prefix="devtool-snapshot-"))
            try:
                _extract_archive(artifact_path, tmpdir)
                _sync_snapshot(tmpdir, repo_root)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            _extract_archive(artifact_path, repo_root)
        if delete_artifact:
            artifact_path.unlink(missing_ok=True)
        output = f"Applied {'snapshot' if snapshot else 'overlay'} from {artifact_path}"
        rc = 0
    except Exception as exc:
        output = str(exc)
        rc = 1
    duration = time.perf_counter() - start
    path = str(write_log(output))
    return OperationResult("apply-snapshot" if snapshot else "apply-overlay", argv, rc, duration, output, [], log_path=path)


def _extract_archive(artifact_path: Path, target: Path) -> None:
    if artifact_path.suffix == ".zip":
        with zipfile.ZipFile(artifact_path) as zf:
            _safe_extract_zip(zf, target)
        return
    if artifact_path.suffix == ".tar" or artifact_path.name.endswith((".tar.gz", ".tgz")):
        mode = "r:gz" if artifact_path.name.endswith((".tar.gz", ".tgz")) else "r"
        with tarfile.open(artifact_path, mode) as tf:
            _safe_extract_tar(tf, target)
        return
    raise RuntimeError(f"Unsupported artifact type: {artifact_path}")


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.infolist():
        member_path = _safe_destination_path(target, Path(member.filename))
        if member.is_dir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue
        member_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member, "r") as source, member_path.open("wb") as dest:
            shutil.copyfileobj(source, dest)


def _safe_extract_tar(archive: tarfile.TarFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.getmembers():
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


def _safe_destination_path(base: Path, member_path: Path) -> Path:
    if member_path.is_absolute():
        raise RuntimeError(f"Unsafe archive path: {member_path}")
    resolved = (base / member_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe archive path: {member_path}") from exc
    return resolved


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


def git_commit(repo_root: Path, message: str, *, verbose: bool = False, dry_run: bool = False) -> tuple[list[OperationResult], str]:
    specs = [
        CommandSpec("git-add", ["git", "add", "-A"]),
        CommandSpec("git-commit", ["git", "commit", "-m", message]),
    ]
    config = ProjectConfig(repo_root=repo_root, kind="unknown")
    return run_specs(config, specs, verbose=verbose, dry_run=dry_run)


def write_template_config(repo_root: Path, *, force: bool = False) -> Path:
    repo_root = repo_root.expanduser().resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    target = repo_root / ".devtool.toml"
    if target.exists() and not force:
        raise SystemExit(f"{target} already exists. Use --force to overwrite.")
    target.write_text(generate_template(repo_root), encoding="utf-8")
    return target
