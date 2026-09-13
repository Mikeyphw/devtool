from __future__ import annotations

"""Runner-neutral build/package artifact collection, verification and registry flow."""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, Iterable
import zipfile

from .artifact_registry import register_paths
from .diagnostics import relativize
from .models import Issue, ProjectConfig

_ARCHIVE_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tzst",
    ".zip", ".whl", ".jar", ".war", ".ear", ".aar", ".apk", ".aab", ".crate", ".nupkg", ".snupkg", ".deb", ".rpm",
)


@dataclass(slots=True)
class ArtifactCandidate:
    path: Path
    state: str = "discovered"
    producer_task: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _options(config: ProjectConfig) -> dict[str, Any]:
    raw = config.runner_options.get("artifacts", {})
    return dict(raw) if isinstance(raw, dict) else {}


def configured_output_dir(config: ProjectConfig) -> Path | None:
    options = _options(config)
    value = str(options.get("output_directory") or options.get("output_dir") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve()


def _suffix(path: Path) -> str:
    lower = path.name.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return path.suffix.lower()


def _is_recent(path: Path, started_at: float) -> bool:
    try:
        return path.stat().st_mtime + 2.0 >= started_at
    except OSError:
        return False


_TRUE_STRINGS = {"1", "true", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "no", "n", "off", ""}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Return an explicit boolean without treating arbitrary strings as truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _dedupe(candidates: Iterable[ArtifactCandidate]) -> list[ArtifactCandidate]:
    by_path: dict[Path, ArtifactCandidate] = {}
    priority = {"produced": 4, "reused": 3, "preexisting": 2, "discovered": 1}
    for candidate in candidates:
        path = candidate.path.expanduser().resolve()
        if not path.is_file():
            continue
        normalized = ArtifactCandidate(path, candidate.state, candidate.producer_task, dict(candidate.metadata))
        current = by_path.get(path)
        if current is None or priority.get(normalized.state, 0) > priority.get(current.state, 0):
            by_path[path] = normalized
    return sorted(by_path.values(), key=lambda value: str(value.path))


def _gradle_event_candidates(output: str, project_root: Path) -> list[ArtifactCandidate]:
    result: list[ArtifactCandidate] = []
    for line in (output or "").splitlines():
        text = line.strip()
        if not text.startswith("DEVTOOL_EVENT "):
            continue
        try:
            event = json.loads(text[len("DEVTOOL_EVENT "):])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "task-finish":
            continue
        task = str(event.get("task") or "")
        outcome = str(event.get("outcome") or "")
        did_work = _coerce_bool(event.get("didWork", False), False)
        state = "produced" if did_work and outcome == "success" else "reused" if outcome in {"up-to-date", "skipped"} else "discovered"
        paths = event.get("artifactOutputs") or event.get("artifact_outputs") or []
        if not isinstance(paths, list):
            continue
        for raw in paths:
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = project_root / path
            result.append(ArtifactCandidate(path.resolve(), state, task, {"gradle_outcome": outcome, "gradle_did_work": did_work}))
    return result


def _glob_files(root: Path, patterns: Iterable[str], *, started_at: float, allow_cached: bool = False) -> list[ArtifactCandidate]:
    recent_items: list[ArtifactCandidate] = []
    cached_items: list[ArtifactCandidate] = []
    if not root.exists():
        return recent_items
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if _is_recent(path, started_at):
                recent_items.append(ArtifactCandidate(resolved, "produced"))
            elif allow_cached:
                cached_items.append(ArtifactCandidate(resolved, "preexisting"))
    # Cached outputs are an UP-TO-DATE fallback, not peers of newly produced
    # outputs. This prevents one package run from rediscovering an entire
    # historical output directory when current artifacts are available.
    return recent_items if recent_items else cached_items


def _python_candidates(project_root: Path, result_metadata: dict[str, Any], started_at: float) -> list[ArtifactCandidate]:
    packages = result_metadata.get("packages")
    candidates: list[ArtifactCandidate] = []
    if isinstance(packages, dict):
        for item in packages.get("items", []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("path") or "").strip()
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = project_root / path
            candidates.append(ArtifactCandidate(
                path.resolve(),
                "produced" if _is_recent(path, started_at) else "reused",
                metadata={"python_package": dict(item)},
            ))
        if candidates:
            return candidates
    return _glob_files(project_root / "dist", ("*.whl", "*.tar.gz", "*.zip"), started_at=started_at, allow_cached=True)


def _node_candidates(project_root: Path, output: str, started_at: float) -> list[ArtifactCandidate]:
    candidates: list[ArtifactCandidate] = []
    # npm pack --json emits an array of objects containing filename.
    for match in re.findall(r"(?:^|\n)(\[\s*\{.*?\}\s*\])(?:\n|$)", output or "", re.DOTALL):
        try:
            payload = json.loads(match)
        except json.JSONDecodeError:
            continue
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict) or not item.get("filename"):
                continue
            path = (project_root / str(item["filename"])).resolve()
            candidates.append(ArtifactCandidate(path, "produced" if _is_recent(path, started_at) else "reused", metadata={"node_package": item}))
    for line in (output or "").splitlines():
        token = line.strip().strip("'\"")
        if token.endswith(".tgz"):
            path = Path(token)
            if not path.is_absolute():
                path = project_root / path
            candidates.append(ArtifactCandidate(path.resolve(), "produced" if _is_recent(path, started_at) else "discovered"))
    candidates.extend(_glob_files(project_root, ("*.tgz",), started_at=started_at, allow_cached=False))
    return candidates


def _parse_go_buildinfo(lines: Iterable[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    settings: dict[str, str] = {}
    dependency_count = 0
    for index, raw in enumerate(str(line) for line in lines if str(line).strip()):
        stripped = raw.rstrip("\n").strip()
        if index == 0 and ": " in stripped:
            metadata["go_version"] = stripped.rsplit(": ", 1)[-1]
            continue
        fields = stripped.split("\t")
        if not fields:
            continue
        if fields[0] == "path" and len(fields) >= 2:
            metadata["package_path"] = fields[1]
        elif fields[0] == "mod" and len(fields) >= 2:
            metadata["module_path"] = fields[1]
            if len(fields) >= 3:
                metadata["module_version"] = fields[2]
        elif fields[0] == "dep" and len(fields) >= 2:
            dependency_count += 1
        elif fields[0] == "build" and len(fields) >= 2:
            setting = fields[1]
            if "=" in setting:
                key, value = setting.split("=", 1)
                settings[key.lstrip("-")] = value
    if dependency_count:
        metadata["dependency_count"] = dependency_count
    if settings:
        metadata["settings"] = settings
        for key in ("GOOS", "GOARCH", "vcs", "vcs.revision", "vcs.time", "vcs.modified"):
            if key in settings:
                metadata[key.replace(".", "_").lower()] = settings[key]
    return metadata


def _go_local_buildinfo(path: Path) -> dict[str, Any]:
    go = shutil.which("go")
    if not go:
        return {}
    try:
        proc = subprocess.run(
            [go, "version", "-m", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    return _parse_go_buildinfo(proc.stdout.splitlines())


def _go_event_candidates(output: str, project_root: Path) -> list[ArtifactCandidate]:
    artifacts: dict[str, tuple[str, str, dict[str, Any]]] = {}
    legacy_buildinfo: dict[str, list[str]] = {}
    for raw_line in (output or "").splitlines():
        if raw_line.startswith("DEVTOOL_GO_ARTIFACT_V2\t"):
            parts = raw_line.split("\t", 12)
            if len(parts) != 13:
                continue
            (
                _, raw_path, package_path, output_name, go_version,
                module_path, module_version, goos, goarch, vcs,
                vcs_revision, vcs_time, vcs_modified,
            ) = parts
            settings = {
                key: value
                for key, value in {
                    "GOOS": goos,
                    "GOARCH": goarch,
                    "vcs": vcs,
                    "vcs.revision": vcs_revision,
                    "vcs.time": vcs_time,
                    "vcs.modified": vcs_modified,
                }.items()
                if value
            }
            build_info: dict[str, Any] = {
                "package_path": package_path,
                **({"go_version": go_version} if go_version else {}),
                **({"module_path": module_path} if module_path else {}),
                **({"module_version": module_version} if module_version else {}),
            }
            if settings:
                build_info["settings"] = settings
                for key, value in settings.items():
                    if key in {"GOOS", "GOARCH", "vcs", "vcs.revision", "vcs.time", "vcs.modified"}:
                        build_info[key.replace(".", "_").lower()] = value
            artifacts[raw_path] = (package_path, output_name, build_info)
        elif raw_line.startswith("DEVTOOL_GO_ARTIFACT\t"):
            parts = raw_line.split("\t", 3)
            if len(parts) != 4:
                continue
            _, raw_path, package_path, output_name = parts
            artifacts[raw_path] = (package_path, output_name, {})
        elif raw_line.startswith("DEVTOOL_GO_BUILDINFO\t"):
            parts = raw_line.split("\t", 2)
            if len(parts) != 3:
                continue
            _, raw_path, info = parts
            legacy_buildinfo.setdefault(raw_path, []).append(info)

    candidates: list[ArtifactCandidate] = []
    for raw_path, (package_path, output_name, build_info) in artifacts.items():
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = project_root / path
        metadata: dict[str, Any] = {
            "go_package": package_path,
            "go_output_name": output_name,
        }
        if not build_info:
            build_info = _parse_go_buildinfo(legacy_buildinfo.get(raw_path, []))
        if build_info:
            metadata["go_build_info"] = build_info
        candidates.append(ArtifactCandidate(path.resolve(), "produced", package_path, metadata))
    return candidates


def _go_executable_format(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return ""
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"MZ"):
        return "pe"
    if head.startswith(b"\x00asm"):
        return "wasm"
    if head[:4] in {
        bytes.fromhex("feedface"), bytes.fromhex("feedfacf"),
        bytes.fromhex("cefaedfe"), bytes.fromhex("cffaedfe"),
        bytes.fromhex("cafebabe"), bytes.fromhex("bebafeca"),
    }:
        return "mach-o"
    # Go also supports Plan 9 and AIX. Their binaries are still conclusively
    # identified by `go version -m`; keep the structural check permissive when
    # embedded Go build information is available.
    return "unknown"


def _verify_go_binary(path: Path, candidate_metadata: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], dict[str, Any], list[Issue]]:
    valid, checks, issues = _basic_verify(path)
    metadata = dict(candidate_metadata)
    build_info = metadata.get("go_build_info")
    if not isinstance(build_info, dict) or not build_info:
        build_info = _go_local_buildinfo(path)
        if build_info:
            metadata["go_build_info"] = build_info

    executable_format = _go_executable_format(path)
    metadata["executable_format"] = executable_format
    if (executable_format and executable_format != "unknown") or build_info:
        checks.append({"name": "executable_format", "status": "passed", "detail": executable_format or "identified by Go build info"})
    else:
        checks.append({"name": "executable_format", "status": "failed", "detail": "unrecognized executable format"})
        valid = False

    if build_info:
        checks.append({"name": "go_build_info", "status": "passed", "detail": str(build_info.get("go_version") or "embedded Go build info")})
        goos = str(build_info.get("goos") or "")
        if os.name == "posix" and goos not in {"windows", "js", "wasip1"}:
            executable = os.access(path, os.X_OK)
            checks.append({"name": "executable_mode", "status": "passed" if executable else "failed", "detail": "POSIX executable bit"})
            valid = valid and executable
    else:
        checks.append({"name": "go_build_info", "status": "passed", "detail": "go tool unavailable; structural executable validation used"})

    metadata["go_package"] = str(candidate_metadata.get("go_package") or (build_info.get("package_path") if isinstance(build_info, dict) else ""))
    metadata["go_output_name"] = str(candidate_metadata.get("go_output_name") or path.name)
    return valid, checks, metadata, issues


def _configured_candidates(config: ProjectConfig, project_root: Path, started_at: float) -> list[ArtifactCandidate]:
    options = _options(config)
    candidates: list[ArtifactCandidate] = []
    raw_paths = options.get("paths") or options.get("artifact_paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    for raw in raw_paths if isinstance(raw_paths, (list, tuple)) else []:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = project_root / path
        if path.is_file():
            candidates.append(ArtifactCandidate(path.resolve(), "produced" if _is_recent(path, started_at) else "reused", metadata={"declared": True}))

    raw_directories = options.get("directories") or options.get("artifact_directories") or []
    if isinstance(raw_directories, str):
        raw_directories = [raw_directories]
    patterns = options.get("patterns") or ["*"]
    if isinstance(patterns, str):
        patterns = [patterns]
    recursive = _coerce_bool(options.get("recursive", True), True)
    for raw in raw_directories if isinstance(raw_directories, (list, tuple)) else []:
        directory = Path(str(raw)).expanduser()
        if not directory.is_absolute():
            directory = project_root / directory
        if not directory.is_dir():
            continue
        for pattern in patterns if isinstance(patterns, (list, tuple)) else ["*"]:
            iterator = directory.rglob(str(pattern)) if recursive else directory.glob(str(pattern))
            for path in iterator:
                if path.is_file():
                    candidates.append(ArtifactCandidate(path.resolve(), "produced" if _is_recent(path, started_at) else "reused", metadata={"declared": True}))
    return candidates


def _runner_candidates(
    config: ProjectConfig,
    *,
    project_root: Path,
    result_metadata: dict[str, Any],
    output: str,
    started_at: float,
) -> list[ArtifactCandidate]:
    kind = str(config.kind or result_metadata.get("runner") or "").lower()
    event_candidates = _gradle_event_candidates(output, project_root) if kind in {"android", "gradle"} else []
    if kind == "python":
        return _python_candidates(project_root, result_metadata, started_at)
    if kind == "node":
        return _node_candidates(project_root, output, started_at)
    if kind == "rust":
        return _glob_files(project_root / "target" / "package", ("*.crate",), started_at=started_at, allow_cached=True)
    if kind == "go":
        event_candidates = _go_event_candidates(output, project_root)
        if event_candidates:
            return event_candidates
        directories = result_metadata.get("artifact_directories") or []
        if isinstance(directories, str):
            directories = [directories]
        candidates: list[ArtifactCandidate] = []
        allow_cached = _coerce_bool(result_metadata.get("artifact_allow_cached", False), False)
        for raw in directories if isinstance(directories, (list, tuple)) else []:
            directory = Path(str(raw)).expanduser()
            if not directory.is_absolute():
                directory = project_root / directory
            candidates.extend(_glob_files(directory, ("*",), started_at=started_at, allow_cached=allow_cached))
        for raw in result_metadata.get("artifact_paths", []) if isinstance(result_metadata.get("artifact_paths"), (list, tuple)) else []:
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = project_root / path
            candidates.append(ArtifactCandidate(path.resolve(), "produced" if _is_recent(path, started_at) else "reused"))
        return candidates
    if kind == "maven":
        # Maven reactor builds place distributables in each module's target
        # directory, not only the repository root target/.
        return _glob_files(
            project_root,
            ("target/*.jar", "target/*.war", "target/*.ear", "target/*.zip",
             "**/target/*.jar", "**/target/*.war", "**/target/*.ear", "**/target/*.zip"),
            started_at=started_at, allow_cached=True,
        )
    if kind == "dotnet":
        return _glob_files(project_root, ("**/*.nupkg", "**/*.snupkg"), started_at=started_at, allow_cached=True)
    if kind == "cmake":
        package_dir = result_metadata.get("artifact_directory") or result_metadata.get("package_directory")
        base = Path(str(package_dir)).expanduser() if package_dir else project_root
        if not base.is_absolute():
            base = project_root / base
        return _glob_files(base, ("*.zip", "*.tar.gz", "*.tar.xz", "*.tgz", "*.deb", "*.rpm"), started_at=started_at, allow_cached=True)
    if kind == "meson":
        build_dir = Path(str(result_metadata.get("build_directory") or project_root / "build"))
        return _glob_files(build_dir / "meson-dist", ("*",), started_at=started_at, allow_cached=True)
    if kind == "gradle":
        if event_candidates:
            return event_candidates
        # Structured events can be disabled or unavailable for an explicit
        # Gradle command. Keep packaging functional with a conservative
        # distributable-only fallback rather than reporting a false missing
        # artifact on successful JVM Gradle builds.
        return _glob_files(
            project_root,
            ("**/build/libs/*.jar", "**/build/distributions/*.zip",
             "**/build/distributions/*.tar", "**/build/distributions/*.tar.gz",
             "**/build/distributions/*.tgz"),
            started_at=started_at, allow_cached=True,
        )
    if kind == "android":
        return event_candidates
    return []


def _verify_tar_zst(path: Path, checks: list[dict[str, Any]]) -> bool:
    try:
        head = path.read_bytes()[:4]
    except OSError as exc:
        checks.append({"name": "archive_integrity", "status": "failed", "detail": str(exc)})
        return False
    if head != bytes.fromhex("28b52ffd"):
        checks.append({"name": "archive_integrity", "status": "failed", "detail": "invalid zstd frame magic for tar.zst artifact"})
        return False
    zstd = shutil.which("zstd") or shutil.which("unzstd")
    if not zstd:
        checks.append({"name": "archive_integrity", "status": "failed", "detail": "tar.zst verification requires zstd support"})
        return False
    try:
        proc = subprocess.run(
            [zstd, "-dc", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append({"name": "archive_integrity", "status": "failed", "detail": str(exc)})
        return False
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [f"zstd exited {proc.returncode}"]
        checks.append({"name": "archive_integrity", "status": "failed", "detail": detail[0]})
        return False
    try:
        import io
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
            for _ in archive:
                pass
    except (tarfile.TarError, OSError) as exc:
        checks.append({"name": "archive_integrity", "status": "failed", "detail": f"invalid tar payload after zstd decompression: {exc}"})
        return False
    checks.append({"name": "archive_integrity", "status": "passed", "detail": "zstd-compressed tar"})
    return True


def _basic_verify(path: Path) -> tuple[bool, list[dict[str, Any]], list[Issue]]:
    checks: list[dict[str, Any]] = []
    issues: list[Issue] = []
    if not path.is_file():
        checks.append({"name": "exists", "status": "failed", "detail": "artifact does not exist"})
        return False, checks, issues
    size = path.stat().st_size
    if size <= 0:
        checks.append({"name": "non_empty", "status": "failed", "detail": "artifact is empty"})
        return False, checks, issues
    checks.extend([
        {"name": "exists", "status": "passed", "detail": ""},
        {"name": "non_empty", "status": "passed", "detail": ""},
    ])
    suffix = _suffix(path)
    try:
        if suffix in {".zip", ".whl", ".jar", ".war", ".ear", ".aar", ".apk", ".aab", ".nupkg", ".snupkg"}:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                if bad:
                    raise ValueError(f"corrupt ZIP member: {bad}")
            checks.append({"name": "archive_integrity", "status": "passed", "detail": ""})
        elif suffix in {".tar.zst", ".tzst"}:
            if not _verify_tar_zst(path, checks):
                return False, checks, issues
        elif suffix in {".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".crate"}:
            with tarfile.open(path, "r:*") as archive:
                # Iteration validates headers without extracting untrusted content.
                for _ in archive:
                    pass
            checks.append({"name": "archive_integrity", "status": "passed", "detail": ""})
        elif suffix == ".deb":
            if path.read_bytes()[:8] != b"!<arch>\n":
                raise ValueError("invalid Debian/ar archive magic")
            checks.append({"name": "archive_integrity", "status": "passed", "detail": "Debian ar container"})
        elif suffix == ".rpm":
            if path.read_bytes()[:4] != bytes.fromhex("edabeedb"):
                raise ValueError("invalid RPM package magic")
            checks.append({"name": "archive_integrity", "status": "passed", "detail": "RPM lead magic"})
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        checks.append({"name": "archive_integrity", "status": "failed", "detail": str(exc)})
        return False, checks, issues
    return True, checks, issues


def _safe_output_name(source: Path, base_name: str, index: int, total: int) -> str:
    suffix = _suffix(source)
    if not base_name:
        return source.name
    requested = Path(base_name).name
    requested_lower = requested.lower()
    if total == 1 and any(requested_lower.endswith(value) for value in _ARCHIVE_SUFFIXES):
        return requested
    for known in _ARCHIVE_SUFFIXES:
        if requested_lower.endswith(known):
            requested = requested[: -len(known)]
            break
    if total == 1:
        return f"{requested}{suffix}"
    stem = source.name[: -len(suffix)] if suffix else source.stem
    classifier = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or str(index)
    return f"{requested}-{classifier}{suffix}"


def _collision_destination(path: Path, policy: str) -> Path:
    if not path.exists() or policy == "replace":
        return path
    if policy == "fail":
        raise FileExistsError(f"Artifact destination already exists: {path}")
    suffix = _suffix(path)
    stem = path.name[: -len(suffix)] if suffix else path.stem
    serial = 2
    while True:
        candidate = path.with_name(f"{stem}-{serial}{suffix}")
        if not candidate.exists():
            return candidate
        serial += 1


def atomic_copy_artifact(source: Path, destination: Path, *, collision: str = "unique") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _collision_destination(destination, collision)
    try:
        if destination.resolve() == source.resolve():
            return destination
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        shutil.copy2(source, temporary)
        # copy2() closes its destination but does not make a durability
        # guarantee. Flush the copied bytes to storage before hash validation
        # and the final atomic rename so a reported artifact cannot be a
        # partially persisted file after a sudden interruption.
        sync_fd = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        if _sha256(source) != _sha256(temporary):
            raise OSError(f"Artifact copy checksum mismatch: {source} -> {destination}")
        os.replace(temporary, destination)
        if os.name == "posix":
            dir_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _copy_to_managed_output(config: ProjectConfig, candidates: list[ArtifactCandidate]) -> list[ArtifactCandidate]:
    output_dir = configured_output_dir(config)
    if output_dir is None or not candidates:
        return candidates

    options = _options(config)
    base_name = str(options.get("output_name") or "").strip()
    policy = str(options.get("collision") or "unique").strip().lower()
    if policy not in {"unique", "replace", "fail"}:
        policy = "unique"

    existing_managed: list[ArtifactCandidate] = []
    outside: list[ArtifactCandidate] = []
    managed_hashes: set[str] = set()
    for candidate in candidates:
        try:
            candidate.path.relative_to(output_dir)
        except ValueError:
            outside.append(candidate)
            continue
        existing_managed.append(candidate)
        try:
            managed_hashes.add(_sha256(candidate.path))
        except OSError:
            pass

    copied: list[ArtifactCandidate] = list(existing_managed)
    total = len(candidates)
    used_destinations = {candidate.path.resolve() for candidate in existing_managed}

    def destination_not_used(path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if resolved not in used_destinations:
            used_destinations.add(resolved)
            return path
        suffix = _suffix(path)
        stem = path.name[: -len(suffix)] if suffix else path.stem
        serial = 2
        while True:
            candidate = path.with_name(f"{stem}-{serial}{suffix}")
            resolved_candidate = candidate.resolve(strict=False)
            if resolved_candidate not in used_destinations and not candidate.exists():
                used_destinations.add(resolved_candidate)
                return candidate
            serial += 1

    for index, candidate in enumerate(outside, 1):
        try:
            digest = _sha256(candidate.path)
        except OSError:
            digest = ""
        if digest and digest in managed_hashes:
            continue
        destination = output_dir / _safe_output_name(candidate.path, base_name, index, total)
        destination = _collision_destination(destination, policy)
        destination = destination_not_used(destination)
        copy_policy = "replace" if policy == "replace" else ("fail" if destination.exists() else "unique")
        copied_path = atomic_copy_artifact(candidate.path, destination, collision=copy_policy)
        metadata = dict(candidate.metadata)
        metadata["source_path"] = str(candidate.path)
        metadata["copied"] = copied_path != candidate.path
        copied.append(ArtifactCandidate(copied_path, candidate.state, candidate.producer_task, metadata))
        if digest:
            managed_hashes.add(digest)
    return _dedupe(copied)


def collect_and_process_artifacts(
    config: ProjectConfig,
    *,
    project_root: Path,
    started_at: float,
    spec_name: str,
    spec_metadata: dict[str, Any],
    result_metadata: dict[str, Any],
    process_output: str,
    returncode: int,
) -> tuple[dict[str, Any], list[Issue]]:
    """Collect, verify, export-ready copy, and immutably register artifacts."""
    options = _options(config)
    configured_verify = options.get("verify")
    verify_requested = _coerce_bool(spec_metadata.get("verify_artifacts", False), False)
    if configured_verify is not None:
        verify_requested = _coerce_bool(configured_verify, False)
    collect_requested = bool(
        _coerce_bool(spec_metadata.get("collect_artifacts", False), False)
        or verify_requested
        or options.get("paths")
        or options.get("artifact_paths")
        or options.get("directories")
        or options.get("artifact_directories")
    )
    if not collect_requested:
        return {}, []

    kind = str(config.kind or spec_metadata.get("runner") or "").lower()
    issues: list[Issue] = []
    candidates: list[ArtifactCandidate]

    if kind == "android":
        from .android_artifacts import collect_and_verify_android_artifacts
        event_candidates = _gradle_event_candidates(process_output, project_root)
        event_paths = [candidate.path for candidate in event_candidates]
        android_metadata, android_issues = collect_and_verify_android_artifacts(
            config,
            project_root=project_root,
            started_at=started_at,
            tasks=spec_metadata.get("gradle_tasks") or (),
            candidate_paths=event_paths,
            register=False,
            verify=verify_requested,
        )
        event_by_path = {str(item.path): item for item in event_candidates}
        candidates = []
        for item in android_metadata.get("items", []):
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("path") or "")).expanduser()
            if not path.is_absolute():
                path = config.repo_root / path
            path = path.resolve()
            event = event_by_path.get(str(path))
            state = event.state if event else ("produced" if _is_recent(path, started_at) and not android_metadata.get("used_cached_outputs") else "reused")
            candidates.append(ArtifactCandidate(path, state, event.producer_task if event else "", {"verification": dict(item)}))
        issues.extend(android_issues)
        required = _coerce_bool(android_metadata.get("required", False), False)
        external = android_metadata.get("external_verification", {})
    else:
        candidates = _dedupe([
            *_configured_candidates(config, project_root, started_at),
            *_runner_candidates(
                config,
                project_root=project_root,
                result_metadata=result_metadata,
                output=process_output,
                started_at=started_at,
            ),
        ])
        required = _coerce_bool(options.get("required", spec_metadata.get("artifact_required", spec_name == "package")), spec_name == "package")
        external = {}

    candidates = _dedupe(candidates)
    copy_enabled = _coerce_bool(spec_metadata.get("copy_artifacts", True), True)
    if returncode == 0 and copy_enabled:
        try:
            candidates = _copy_to_managed_output(config, candidates)
        except (OSError, FileExistsError) as exc:
            issues.append(Issue("error", "artifact-copy", relativize(config.repo_root, str(configured_output_dir(config) or project_root)), "ARTIFACT_COPY", str(exc)))

    records: list[dict[str, Any]] = []
    python_items: dict[str, dict[str, Any]] = {}
    packages = result_metadata.get("packages")
    if isinstance(packages, dict):
        for item in packages.get("items", []):
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("path") or "")).expanduser()
            if not path.is_absolute():
                path = project_root / path
            python_items[str(path.resolve())] = item

    for candidate in candidates:
        source_path = Path(str(candidate.metadata.get("source_path") or candidate.path)).resolve()
        verification = candidate.metadata.get("verification")
        package_record = python_items.get(str(source_path)) or python_items.get(str(candidate.path))
        if isinstance(verification, dict):
            valid = _coerce_bool(verification.get("valid", True), True)
            checks = list(verification.get("checks") or [])
            semantic_type = str(verification.get("type") or _suffix(candidate.path).lstrip("."))
            semantic_metadata = dict(verification)
        elif isinstance(package_record, dict):
            valid = _coerce_bool(package_record.get("valid", True), True)
            checks = list(package_record.get("checks") or [])
            semantic_type = str(package_record.get("type") or _suffix(candidate.path).lstrip("."))
            semantic_metadata = dict(package_record)
        elif kind == "go":
            semantic_type = "go-binary"
            if verify_requested:
                valid, checks, semantic_metadata, basic_issues = _verify_go_binary(candidate.path, candidate.metadata)
                issues.extend(basic_issues)
            else:
                semantic_metadata = dict(candidate.metadata)
                build_info = semantic_metadata.get("go_build_info")
                if not isinstance(build_info, dict) or not build_info:
                    build_info = _go_local_buildinfo(candidate.path)
                    if build_info:
                        semantic_metadata["go_build_info"] = build_info
                semantic_metadata["executable_format"] = _go_executable_format(candidate.path)
                valid = candidate.path.is_file() and candidate.path.stat().st_size > 0
                checks = [{
                    "name": "exists_non_empty",
                    "status": "passed" if valid else "failed",
                    "detail": "structural verification disabled",
                }]
        elif verify_requested:
            valid, checks, basic_issues = _basic_verify(candidate.path)
            issues.extend(basic_issues)
            semantic_type = _suffix(candidate.path).lstrip(".") or "file"
            semantic_metadata = {}
        else:
            valid = candidate.path.is_file() and candidate.path.stat().st_size > 0
            checks = [{"name": "exists_non_empty", "status": "passed" if valid else "failed", "detail": "verification disabled"}]
            semantic_type = _suffix(candidate.path).lstrip(".") or "file"
            semantic_metadata = {}
        if not valid and not any(issue.file == relativize(config.repo_root, str(candidate.path)) for issue in issues):
            issues.append(Issue("error", "artifact-verify", relativize(config.repo_root, str(candidate.path)), "ARTIFACT_INVALID", "Artifact verification failed."))
        records.append({
            "path": relativize(config.repo_root, str(candidate.path)),
            "source_path": relativize(config.repo_root, str(source_path)),
            "type": semantic_type,
            "size": candidate.path.stat().st_size if candidate.path.is_file() else 0,
            "sha256": _sha256(candidate.path) if candidate.path.is_file() else "",
            "valid": valid,
            "variant": str(semantic_metadata.get("variant") or config.variant or ""),
            "state": candidate.state if returncode == 0 else ("preexisting" if candidate.state != "produced" else "uncommitted-output"),
            "producer_task": candidate.producer_task,
            "checks": checks,
            "metadata": semantic_metadata,
        })

    if required and not records:
        issues.append(Issue(
            "error", "artifact-verify", relativize(config.repo_root, str(project_root)),
            "ARTIFACT_MISSING", "Command completed without a required distributable artifact.",
        ))

    registered: list[dict[str, Any]] = []
    registration_warning = ""
    register_enabled = _coerce_bool(spec_metadata.get("register_artifacts", True), True)
    artifact_errors = [issue for issue in issues if issue.severity == "error"]
    if returncode == 0 and register_enabled and records and not artifact_errors:
        try:
            item_metadata: dict[str, dict[str, Any]] = {}
            paths: list[Path] = []
            for record in records:
                path = Path(str(record["path"])).expanduser()
                if not path.is_absolute():
                    path = config.repo_root / path
                path = path.resolve()
                paths.append(path)
                semantic_metadata = record.get("metadata")
                semantic_metadata = dict(semantic_metadata) if isinstance(semantic_metadata, dict) else {}
                item_metadata[str(path)] = {
                    **record,
                    **semantic_metadata,
                    "producer": kind,
                    "command": spec_name,
                    "target": str(config.selected_target or ""),
                    "variant": str(config.variant or ""),
                    "gradle_tasks": list(spec_metadata.get("gradle_tasks") or []),
                    "verification_requested": verify_requested,
                    "verification_status": "valid" if record.get("valid") else "invalid",
                }
            registered = register_paths(
                config.repo_root,
                paths,
                metadata={
                    "producer": kind,
                    "command": spec_name,
                    "target": str(config.selected_target or ""),
                    "variant": str(config.variant or ""),
                    "run_id": os.environ.get("DEVTOOL_RUN_ID", ""),
                },
                item_metadata=item_metadata,
            )
            registry_ids = {str(item.get("path") or ""): str(item.get("id") or "") for item in registered}
            for record in records:
                record["registry_id"] = registry_ids.get(str(record.get("path") or ""), "")
        except Exception as exc:  # registry failure should remain visible but not hide build output
            registration_warning = str(exc)
    elif returncode != 0:
        registration_warning = "command failed; artifacts were retained only as diagnostics and not registered"
    elif not register_enabled:
        registration_warning = "artifact registration deferred to a later package verification phase"
    elif artifact_errors:
        registration_warning = "artifact verification failed; invalid outputs were not registered"

    metadata: dict[str, Any] = {
        "count": len(records),
        "valid": sum(1 for item in records if item.get("valid")),
        "invalid": sum(1 for item in records if not item.get("valid")),
        "required": required,
        "verification_requested": verify_requested,
        "external_verification": external,
        "items": records,
        "registry": {
            "registered": len(registered),
            "ids": [str(item.get("id") or "") for item in registered],
        },
    }
    if registration_warning:
        metadata["registry"]["warning"] = registration_warning
    return metadata, issues


def format_artifact_summary(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    lines = [
        f"Devtool artifacts: {metadata.get('valid', 0)} valid, {metadata.get('invalid', 0)} invalid, {metadata.get('count', 0)} total."
    ]
    for item in metadata.get("items", [])[:12]:
        if not isinstance(item, dict):
            continue
        digest = str(item.get("sha256") or "")[:12]
        size = int(item.get("size") or 0)
        state = str(item.get("state") or "")
        validity = "verified" if item.get("valid") and metadata.get("verification_requested") else "valid" if item.get("valid") else "INVALID"
        variant = str(item.get("variant") or "").strip()
        registry_id = str(item.get("registry_id") or "").strip()
        details = [f"sha256={digest}", state, validity]
        if variant:
            details.append(f"variant={variant}")
        if registry_id:
            details.append(f"registry={registry_id}")
        lines.append(f"- {item.get('path')} [{item.get('type') or 'file'}] {size} B " + " ".join(value for value in details if value))
    if int(metadata.get("count") or 0) > 12:
        lines.append(f"- ... and {int(metadata['count']) - 12} more artifact(s).")
    registry = metadata.get("registry")
    if isinstance(registry, dict) and registry.get("warning"):
        lines.append(f"Devtool artifact registry: {registry['warning']}")
    return "\n".join(lines) + "\n"
