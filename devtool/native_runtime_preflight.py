"""M2: source-bound native runtime selection and pre-execution archive gate.

No overlay mutation, Git branch change, delivery session, GitHub write, or
alternative validation/apply engine. Actual execution belongs to native Devtool.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from typing import Any, Mapping, Sequence

from .launcher_runtime_compat import declared_version, require_version

_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_GITHUB = re.compile(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z", re.I)
_RUNTIME_PATHS = (
    "devtool/launcher_contract_v3.py",
    "devtool/core_cli_schema.json",
    "devtool/native_runtime_preflight.py",
)
_CACHE_NAME = re.compile(r"(?:runtime|compatible-runtime)-[0-9a-f]{40}\Z")
_TRUSTED_SELECTION: ContextVar[tuple[str | Path, ...]] = ContextVar("native_m2_verified_candidates", default=())


class NativePreflightError(ValueError):
    """Source identity or verified execution precondition did not hold."""


def _refuse(message: str) -> None:
    raise NativePreflightError("NATIVE_M2_REFUSED " + message)


def _regular_root(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().absolute()
    if path.is_symlink() or not path.is_dir():
        _refuse(label + " is missing or is a symlink")
    for part in (path, *path.parents):
        if part.is_symlink():
            _refuse(label + " contains a symlink ancestor")
    if path != path.resolve(strict=True):
        _refuse(label + " contains a path alias or traversal")
    # A selected root must itself be a real worktree, not an aliased subdirectory.
    if path.is_symlink():
        _refuse(label + " has symlink ancestry")
    return path


def _trusted_git_binary() -> str:
    # Python's installed prefix identifies Termux and system Python's package
    # environment; PATH and arbitrary GIT_EXEC_PATH never select the executable.
    paths = [Path(sys.prefix) / "bin/git", Path("/usr/bin/git"), Path("/bin/git")]
    for path in paths:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    _refuse("trusted Git executable unavailable in Python prefix or system directories")


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("GIT_") or k in {"PYTHONPATH", "PYTHONHOME"})}
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"})
    return env


def _git(root: Path, *argv: str, timeout: int = 15) -> str:
    try:
        command = [_trusted_git_binary(), "-c", "core.fsmonitor=false",
                   "-c", "core.hooksPath=/dev/null", "-C", str(root), *argv]
        done = subprocess.run(command, env=_git_env(), capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativePreflightError("NATIVE_M2_REFUSED Git identity check failed") from exc
    if done.returncode:
        _refuse("Git identity command refused: " + " ".join(argv[:2]))
    return done.stdout.strip()


def _origin(value: str) -> str:
    match = _GITHUB.fullmatch(value)
    if match is None:
        _refuse("noncanonical or untrusted GitHub origin")
    return match.group(1).casefold()


def _safe_git_root(root: Path) -> None:
    if Path(_git(root, "rev-parse", "--show-toplevel")) != root:
        _refuse("expected exact Git worktree root")
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        _refuse("not a Git worktree")


def _clean(root: Path) -> None:
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        _refuse("worktree dirty or contains untracked files")


def _sha(value: str, label: str) -> str:
    if not _GIT_SHA.fullmatch(value):
        _refuse(label + " is not an exact Git SHA-1")
    return value


@dataclass(frozen=True)
class RuntimeAttestation:
    root: Path
    commit: str
    tree: str
    version: str
    module_blob: str
    schema_blob: str
    preflight_blob: str
    detached: bool
    commit_time: int

    def as_dict(self) -> dict[str, Any]:
        return {"root": str(self.root), "commit": self.commit, "tree": self.tree,
                "version": self.version, "module_blob": self.module_blob,
                "schema_blob": self.schema_blob, "preflight_blob": self.preflight_blob,
                "detached": self.detached, "attested": True}


def attest_runtime(candidate: str | Path, compatibility: Mapping[str, Any]) -> RuntimeAttestation:
    """Corroborate a *trusted* on-disk runtime before using its code as runtime."""
    root = _regular_root(candidate, "runtime")
    _safe_git_root(root)
    if _origin(_git(root, "remote", "get-url", "origin")) != "mikeyphw/devtool":
        _refuse("runtime origin is not Mikeyphw/devtool")
    _clean(root)
    commit = _sha(_git(root, "rev-parse", "HEAD"), "runtime HEAD")
    tree = _sha(_git(root, "rev-parse", "HEAD^{tree}"), "runtime tree")
    if _git(root, "rev-parse", "HEAD^{tree}") != tree:
        _refuse("runtime tree drift")
    branch = _git(root, "branch", "--show-current")
    entries: dict[str, str] = {}
    for relative in _RUNTIME_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            _refuse("runtime source missing or symlinked: " + relative)
        blob = _sha(_git(root, "rev-parse", "HEAD:" + relative), relative)
        if _git(root, "hash-object", "--", relative) != blob:
            _refuse("runtime module/schema working bytes differ from committed blob: " + relative)
        entries[relative] = blob
    version = declared_version(root / _RUNTIME_PATHS[0])
    require_version(version, compatibility)
    seconds = _git(root, "show", "-s", "--format=%ct", "HEAD")
    if not seconds.isdigit():
        _refuse("runtime commit timestamp unverified")
    return RuntimeAttestation(root, commit, tree, version,
                              entries[_RUNTIME_PATHS[0]], entries[_RUNTIME_PATHS[1]],
                              entries[_RUNTIME_PATHS[2]], not bool(branch), int(seconds))


def _private_cache_roots() -> list[Path]:
    state = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")).expanduser()
    base = state / "devtool"
    # Cache search is optional, bounded and restricted to private directories.
    cache_dirs = [base / "launcher-runtimes", base / "launcher-native" / "launcher-runtimes"]
    found: list[Path] = []
    for directory in cache_dirs:
        if directory.is_symlink() or not directory.is_dir():
            continue
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            continue
        for entry in sorted(directory.iterdir())[:64]:
            if _CACHE_NAME.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink():
                found.append(entry)
    return found


def resolve_runtime(compatibility: Mapping[str, Any], *, target: str | Path,
                    trusted: Sequence[str | Path] = ()) -> RuntimeAttestation:
    """Latest attested compatible candidate; no path guessing/fetch/repinning.

    `trusted` is explicit operator/configured trust. An empty list uses the
    currently loaded native Devtool checkout plus private source caches.
    """
    target_root = _regular_root(target, "target")
    from_import = Path(__file__).resolve().parent.parent
    candidates = list(trusted) if trusted else [from_import, *_private_cache_roots()]
    accepted: list[RuntimeAttestation] = []
    observed: set[Path] = set()
    errors: list[str] = []
    for entry in candidates[:65]:
        path = Path(entry).expanduser().absolute()
        if path in observed:
            continue
        observed.add(path)
        if path.resolve() == target_root:
            errors.append("runtime and target would overlap")
            continue
        try:
            accepted.append(attest_runtime(path, compatibility))
        except (NativePreflightError, OSError, ValueError) as exc:
            errors.append(str(exc))
            if trusted:
                # Explicit trust selectors do not silently downgrade to another path.
                _refuse("explicitly trusted runtime rejected: " + str(exc))
    if not accepted:
        _refuse("no separate attested compatible runtime: " + "; ".join(errors[:3]))
    from .launcher_runtime_compat import parse_version
    return max(accepted, key=lambda item: (parse_version(item.version), item.commit_time, item.commit))


def verify_target(target: str | Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    """Fail before mutation for changed source, detached target and aliasing."""
    root = _regular_root(target, "target")
    _safe_git_root(root)
    _clean(root)
    repository = manifest["repository"]
    actual_origin = _origin(_git(root, "remote", "get-url", "origin"))
    if actual_origin != _origin(repository["remote"]):
        _refuse("target origin mismatch")
    actual_branch = _git(root, "branch", "--show-current")
    if actual_branch != repository["branch"] or not actual_branch:
        _refuse("detached or wrong target branch")
    head = _sha(_git(root, "rev-parse", "HEAD"), "target HEAD")
    tree = _sha(_git(root, "rev-parse", "HEAD^{tree}"), "target tree")
    if head != manifest["base_commit"] or tree != manifest["base_tree"]:
        _refuse("target HEAD/tree differs from exact artifact preimage")
    return {"root": str(root), "origin": actual_origin, "branch": actual_branch,
            "head": head, "tree": tree}


def preflight_manifest(target: str | Path, manifest: Mapping[str, Any], *,
                       trusted: Sequence[str | Path] = ()) -> dict[str, Any] | None:
    """Opt-in v3 native preflight; legacy overlays retain their original flow."""
    from .native_delivery_contract_v3 import normalize_delivery_contract
    declared = normalize_delivery_contract(manifest)
    if not trusted:
        trusted = _TRUSTED_SELECTION.get()
    if declared is None:
        return None
    source = verify_target(target, manifest)
    compatibility = manifest["delivery_contract"]["runtime"]["compatibility"]
    selected = resolve_runtime(compatibility, target=target, trusted=trusted)
    return {"schema": "devtool.native-m2-preflight.v1", "status": "VERIFIED",
            "target": source, "runtime": selected.as_dict(), "mutation_permitted": False,
            "remote_approved": False, "ci_qualified": False}


def preverify_archive(target: str | Path, artifact: str | Path, *,
                      trusted: Sequence[str | Path] = ()) -> dict[str, Any]:
    """External native archive check. Never imports/executes ZIP/PYZ members."""
    from .artifacts import read_artifact_manifest, validate_artifact
    archive = Path(artifact).expanduser().absolute()
    if (archive.is_symlink() or any(part.is_symlink() for part in archive.parents)
            or not archive.is_file() or archive != archive.resolve(strict=True)
            or not stat.S_ISREG(archive.stat().st_mode)
            or archive.suffix.lower() not in {".zip", ".pyz"}):
        _refuse("archive must be a regular ZIP/PYZ file without aliasing")
    before = archive.stat()
    if before.st_size <= 0 or before.st_size > 32 * 1024 * 1024:
        _refuse("archive size exceeds bounded executable preverification budget")
    if not zipfile.is_zipfile(archive):
        _refuse("archive is not a structurally valid ZIP/PYZ")
    contents = archive.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    manifest = read_artifact_manifest(archive)
    if archive.suffix.lower() == ".pyz":
        spec = manifest.get("executable_overlay")
        if not isinstance(spec, dict) or spec.get("profile") != "devtool.executable-overlay.v2":
            _refuse("untrusted PYZ must use canonical source-bound executable v2 profile")
    # Pass an explicit candidate to the native verifier's own opt-in v3
    # preflight without adding an environment override or weakening the gate.
    token = _TRUSTED_SELECTION.set(tuple(trusted))
    try:
        qualified = validate_artifact(Path(target), archive, artifact_type="overlay")
    finally:
        _TRUSTED_SELECTION.reset(token)
    if not qualified.ok:
        _refuse("native artifact qualification: " + "; ".join(qualified.messages[:4]))
    if manifest.get("schema_version") != 3:
        _refuse("native M2 executable preflight requires opt-in delivery schema v3")
    proof = preflight_manifest(target, manifest, trusted=trusted)
    now = archive.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        _refuse("archive changed during verification")
    return {"schema": "devtool.native-m2-archive.v1", "status": "VERIFIED",
            "sha256": digest, "size": len(contents), "artifact": str(archive), "preflight": proof,
            "code_executed": False, "mutation_permitted": False}


def run_verified_pyz(target: str | Path, artifact: str | Path, *,
                     trusted: Sequence[str | Path] = (), apply: bool = False) -> int:
    """Verify *outside* the PYZ, then run its unmodified canonical adapter.

    The private, integrity-checked snapshot prevents a path-swap race between
    verification and execution. The adapter delegates all operations to native
    Devtool; it does not implement or simulate apply/transactions/sessions.
    """
    original = Path(artifact).expanduser().absolute()
    if original.suffix.lower() != ".pyz":
        _refuse("run-verified requires a PYZ, not an ordinary ZIP")
    receipt = preverify_archive(target, original, trusted=trusted)
    runtime = Path(receipt["preflight"]["runtime"]["root"])
    target_root = Path(receipt["preflight"]["target"]["root"])
    fingerprint = receipt["sha256"]
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update({"PYTHONPATH": str(runtime), "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                "DEVTOOL_TEXTUAL_APPLY_HOST": "0", "DEVTOOL_TEXTUAL_EXECUTION_HOST": "0"})
    with tempfile.TemporaryDirectory(prefix="devtool-verified-pyz-") as private_dir:
        private = Path(private_dir)
        private.chmod(0o700)
        frozen = private / "verified.pyz"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(frozen, flags, 0o600)
        with os.fdopen(fd, "wb") as output:
            data = original.read_bytes()
            if hashlib.sha256(data).hexdigest() != fingerprint:
                _refuse("PYZ bytes changed after verification")
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if hashlib.sha256(frozen.read_bytes()).hexdigest() != fingerprint:
            _refuse("private verified PYZ snapshot integrity changed")
        # The verified bootstrap owns its CLI and native delegate; no custom
        # validation/overlay apply logic is executed from this wrapper.
        command = [sys.executable, "-P", "-B", str(frozen), "apply" if apply else "check",
                   "--repo", str(target_root)]
        if apply:
            command.append("--yes")
        return subprocess.run(command, cwd=target_root, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("check", "run-verified"):
        command = actions.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--artifact", type=Path, required=True)
        command.add_argument("--trusted-runtime", type=Path, help="Operator selected trusted Devtool worktree")
        if name == "run-verified":
            command.add_argument("--apply", action="store_true", help="Explicit native apply authorization")
    args = parser.parse_args(argv)
    try:
        trusted = (args.trusted_runtime,) if args.trusted_runtime else ()
        if args.action == "check":
            print(json.dumps(preverify_archive(args.repo, args.artifact, trusted=trusted),
                             sort_keys=True, indent=2))
            return 0
        return run_verified_pyz(args.repo, args.artifact, trusted=trusted, apply=args.apply)
    except (NativePreflightError, ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
