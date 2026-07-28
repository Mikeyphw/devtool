from __future__ import annotations

import contextlib
import fcntl
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
import uuid
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .console_utils import resolve_cli_path
from .logstore import state_dir

MANIFEST_NAMES = {".devtool-artifact.json", "artifact.devtool.json", "devtool-artifact.json"}
DELETE_MANIFEST_NAME = ".devtool-delete"
ARTIFACT_METADATA_NAMES = MANIFEST_NAMES | {DELETE_MANIFEST_NAME}
ARTIFACT_METADATA_DIRECTORY = ".devtool-artifact"
HISTORY_PATH = Path(".devtool") / "artifacts.json"
HISTORY_ENTRIES_PATH = Path(".devtool") / "artifacts.d"

SUPPORTED_ARCHIVE_SUFFIXES = (
    ".tar.zst", ".tzst",
    ".tar.xz", ".txz",
    ".tar.bz2", ".tbz2",
    ".tar.gz", ".tgz",
    ".tar", ".zip",
)
SUPPORTED_ARTIFACT_SUFFIXES = SUPPORTED_ARCHIVE_SUFFIXES
BUNDLE_MANIFEST_NAMES = {"devtool-bundle.json", ".devtool-bundle.json"}
BUNDLE_KINDS = {"devtool.overlay_bundle", "overlay_bundle", "overlay-bundle"}
_SUFFIX_VARIANT_RE = re.compile(r"^(?P<root>.+?)(?:\((?P<paren>\d+)\)|[_-](?P<sep>\d+)|(?P<bare>\d+))$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VALIDATION_EXPECTED_OUTCOMES = {
    "pass",
    "passed",
    "success",
    "succeeded",
    "skip",
    "skipped",
    "warn",
    "warning",
    "warnings",
    "fail",
    "failed",
    "error",
    "errors",
}

_HASH_CACHE_LIMIT = 2048
_HASH_CACHE: "OrderedDict[tuple[str, int, int, int], str]" = OrderedDict()
_HASH_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ArtifactVariant:
    path: Path
    root: str
    suffix: str
    number: int


def _split_artifact_archive_name(name: str) -> tuple[str, str] | None:
    """Return archive stem plus supported archive suffix, preserving filename case."""
    lower = name.lower()
    for suffix in SUPPORTED_ARTIFACT_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    return None


def _artifact_variant_number(stem: str, root: str) -> int | None:
    if stem == root:
        return 0
    match = _SUFFIX_VARIANT_RE.fullmatch(stem)
    if not match or match.group("root") != root:
        return None
    value = match.group("paren") or match.group("sep") or match.group("bare")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _artifact_family_root_candidates(path: Path, stem: str, suffix: str) -> list[str]:
    """Return possible unsuffixed artifact stems for a requested archive path.

    Browser/download-manager duplicates often add ``(1)``, ``_1``, ``-1`` or a
    bare trailing number before the archive suffix.  Separator and parenthesized
    forms are unambiguous. Bare trailing digits are only treated as a duplicate
    suffix when the unsuffixed sibling/family exists, so a legitimate artifact
    such as ``schema_v2.zip`` keeps resolving as itself.
    """
    roots = [stem]
    match = _SUFFIX_VARIANT_RE.fullmatch(stem)
    if not match:
        return roots

    root = match.group("root")
    if not root or root == stem:
        return roots

    is_bare = match.group("bare") is not None and match.group("paren") is None and match.group("sep") is None
    if not is_bare:
        return [root]

    directory = path.parent
    canonical = directory / f"{root}{suffix}"
    if canonical.exists():
        return [root]
    # Bare trailing digits are ambiguous with real artifact names.  Only treat
    # them as a duplicate suffix when another less-ambiguous family member
    # exists, e.g. ``overlay-2.zip`` or ``overlay(2).zip``.
    for entry in directory.iterdir():
        if entry.resolve() == path.resolve() or not entry.name.lower().endswith(suffix.lower()):
            continue
        entry_stem = entry.name[: -len(suffix)]
        variant_match = _SUFFIX_VARIANT_RE.fullmatch(entry_stem)
        if not variant_match or variant_match.group("root") != root:
            continue
        if variant_match.group("paren") is not None or variant_match.group("sep") is not None:
            return [root]
    return roots


def _artifact_family_variants(path: Path) -> list[ArtifactVariant]:
    split = _split_artifact_archive_name(path.name)
    if split is None or not path.parent.exists():
        return [ArtifactVariant(path, path.stem, path.suffix, 0)] if path.exists() else []

    stem, suffix = split
    roots = _artifact_family_root_candidates(path, stem, suffix)
    variants: list[ArtifactVariant] = []
    seen: set[Path] = set()
    lower_suffix = suffix.lower()
    for root in roots:
        for entry in path.parent.iterdir():
            if not entry.is_file() and not entry.is_symlink():
                continue
            if not entry.name.lower().endswith(lower_suffix):
                continue
            entry_stem = entry.name[: -len(suffix)]
            number = _artifact_variant_number(entry_stem, root)
            if number is None:
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            variants.append(ArtifactVariant(resolved, root, entry.name[-len(suffix) :], number))
    return variants


def _artifact_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _select_artifact_variant(path: Path, variants: list[ArtifactVariant]) -> Path:
    if not variants:
        return path

    split = _split_artifact_archive_name(path.name)
    requested_stem = split[0] if split else path.stem
    root_order = {root: index for index, root in enumerate(dict.fromkeys([requested_stem, *(variant.root for variant in variants)]))}

    selected_by_root: list[ArtifactVariant] = []
    for root in dict.fromkeys(variant.root for variant in variants):
        family = [variant for variant in variants if variant.root == root]
        unsuffixed = [variant for variant in family if variant.number == 0]
        suffixed = [variant for variant in family if variant.number > 0]
        if suffixed:
            highest_number = max(variant.number for variant in suffixed)
            highest = max((variant for variant in suffixed if variant.number == highest_number), key=lambda variant: _artifact_mtime(variant.path))
            if unsuffixed:
                plain = max(unsuffixed, key=lambda variant: _artifact_mtime(variant.path))
                selected_by_root.append(plain if _artifact_mtime(plain.path) > _artifact_mtime(highest.path) else highest)
            else:
                selected_by_root.append(highest)
        elif unsuffixed:
            selected_by_root.append(max(unsuffixed, key=lambda variant: _artifact_mtime(variant.path)))

    exact = path.resolve()
    for selected in selected_by_root:
        if selected.path == exact:
            return selected.path
    selected_by_root.sort(key=lambda variant: (root_order.get(variant.root, 999), -variant.number, -_artifact_mtime(variant.path)))
    return selected_by_root[0].path if selected_by_root else path


def resolve_artifact_path(artifact: str | Path) -> Path:
    """Resolve a CLI artifact path, accepting common duplicate-download suffixes.

    ``overlay.zip`` may resolve to ``overlay(1).zip``, ``overlay_1.zip``,
    ``overlay1.zip`` or ``overlay-1.zip``.  The highest numeric duplicate suffix
    wins, unless the unsuffixed archive exists and has a newer modification time.
    """
    path = resolve_cli_path(str(artifact))
    return _select_artifact_variant(path, _artifact_family_variants(path))


def artifact_cleanup_paths(artifact: str | Path) -> list[Path]:
    """Return every archive variant and sidecar that belongs to an artifact family."""
    path = resolve_artifact_path(artifact)
    variants = _artifact_family_variants(path) or ([ArtifactVariant(path, path.stem, path.suffix, 0)] if path.exists() else [])
    cleanup: list[Path] = []
    seen: set[Path] = set()
    for variant in sorted(variants, key=lambda item: (item.number, str(item.path))):
        for candidate in [variant.path, variant.path.with_name(variant.path.name + ".devtool.json"), variant.path.with_name(variant.path.name + ".manifest.json")]:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            cleanup.append(candidate)
    return cleanup



def is_artifact_metadata_member(name: str) -> bool:
    """Return True for archive-side devtool metadata that must not be copied into repos."""
    path = Path(name)
    return (
        path.name in ARTIFACT_METADATA_NAMES
        or bool(path.parts and path.parts[0] in {ARTIFACT_METADATA_DIRECTORY, ".devtool"})
    )


@dataclass(slots=True)
class ArchiveMember:
    name: str
    is_dir: bool = False
    data: bytes | None = None


@dataclass(slots=True)
class ArtifactValidation:
    ok: bool
    messages: list[str]
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BundleOverlaySpec:
    path: str
    id: str = ""
    target: str = ""
    label: str = ""
    required: bool = True
    after: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    index: int = 0


@dataclass(frozen=True, slots=True)
class MaterializedBundleOverlay:
    path: Path
    bundle_path: str
    id: str = ""
    target: str = ""
    label: str = ""
    required: bool = True
    index: int = 0


def sha256_file(path: Path) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns), int(getattr(stat, "st_ino", 0)))
    with _HASH_CACHE_LOCK:
        cached = _HASH_CACHE.get(key)
        if cached is not None:
            _HASH_CACHE.move_to_end(key)
            return cached

    h = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()

    with _HASH_CACHE_LOCK:
        # Keep one current record per path and cap process-wide memory use.
        for stale in [cache_key for cache_key in _HASH_CACHE if cache_key[0] == key[0] and cache_key != key]:
            _HASH_CACHE.pop(stale, None)
        _HASH_CACHE[key] = digest
        _HASH_CACHE.move_to_end(key)
        while len(_HASH_CACHE) > _HASH_CACHE_LIMIT:
            _HASH_CACHE.popitem(last=False)
    return digest


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value.strip()))


def _safe_rel(name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe archive path: {name}")
    return rel


def _manifest_repository(manifest: dict[str, Any]) -> dict[str, Any]:
    repository = manifest.get("repository")
    if isinstance(repository, dict):
        return repository
    legacy = str(manifest.get("repo") or manifest.get("repo_name") or manifest.get("expected_repo") or "").strip()
    return {"name": legacy} if legacy else {}


def artifact_target(manifest: dict[str, Any]) -> str:
    """Return the preferred validation target from a v2 or legacy manifest."""
    return str(manifest.get("target") or manifest.get("validate_target") or "").strip()


def artifact_solution(manifest: dict[str, Any]) -> str:
    return str(manifest.get("solution") or manifest.get("expected_solution") or "").strip()


def artifact_commit_policy(manifest: dict[str, Any]) -> tuple[bool, str, str]:
    """Return enabled, message, and strategy for artifact-driven commits."""
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    commit = apply.get("commit") if isinstance(apply.get("commit"), dict) else {}
    enabled = bool(commit.get("enabled", False))
    message = str(commit.get("message") or manifest.get("commit_message") or "").strip()
    strategy = str(commit.get("strategy") or "single").strip().lower() or "single"
    return enabled and bool(message), message, strategy


def artifact_validation_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    raw = manifest.get("validation")
    if not isinstance(raw, dict):
        return {}
    policy = dict(raw)
    policy.setdefault("required", False)
    policy.setdefault("restore", True)
    policy.setdefault("build", True)
    policy.setdefault("test", True)
    mode = str(policy.get("mode") or "").strip().lower()
    if mode:
        policy["required"] = mode == "required"
        if mode == "disabled":
            policy.update({"restore": False, "build": False, "test": False})
    policy.setdefault("rollback_on_failure", True)
    if "failure_action" not in policy:
        policy["failure_action"] = (
            "rollback" if "rollback_on_failure" in raw and bool(raw.get("rollback_on_failure")) else "pause"
        )
    policy.setdefault("allow_deferred", True)
    policy.setdefault("minimum_phase", "finalize")
    minimum_phase = str(policy.get("minimum_phase") or "finalize").strip().lower().replace("_", "-")
    # Legacy manifests may still name the removed artifact-check checkpoint.
    # Repository validation no longer inspects the already-accepted archive, so
    # the closest meaningful checkpoint is package.
    policy["minimum_phase"] = "package" if minimum_phase == "artifact-check" else minimum_phase
    policy.setdefault("test_profile", "")
    policy.setdefault("execution_environment", "")
    return policy


def artifact_validation_script(manifest: dict[str, Any]) -> dict[str, Any]:
    validation = artifact_validation_policy(manifest)
    script = validation.get("script")
    return dict(script) if isinstance(script, dict) else {}


def artifact_workspace_roots(manifest: dict[str, Any]) -> list[str]:
    roots: list[str] = []
    workspace = manifest.get("workspace")
    if isinstance(workspace, dict):
        value = str(workspace.get("root") or "").strip()
        if value:
            roots.append(value)
    workspaces = manifest.get("workspaces")
    if isinstance(workspaces, list):
        for item in workspaces:
            if not isinstance(item, dict):
                continue
            value = str(item.get("root") or "").strip()
            if value and value not in roots:
                roots.append(value)
    return roots


def artifact_workspaces(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Return normalized monorepo workspace descriptors."""
    normalized: list[dict[str, str]] = []
    singular = manifest.get("workspace")
    raw_items: list[Any] = [singular] if isinstance(singular, dict) else []
    plural = manifest.get("workspaces")
    if isinstance(plural, list):
        raw_items.extend(plural)
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        descriptor = {
            "name": str(item.get("name") or "").strip(),
            "root": str(item.get("root") or "").strip(),
            "target": str(item.get("target") or item.get("validate_target") or "").strip(),
            "solution": str(item.get("solution") or "").strip(),
        }
        if any(descriptor.values()):
            normalized.append(descriptor)
    return normalized


def artifact_inventory_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict):
        return []
    entries = inventory.get("entries")
    return [dict(item) for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []


def _validate_validation_policy(manifest: dict[str, Any], messages: list[str], *, bundle: bool) -> bool:
    raw = manifest.get("validation")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        messages.append("validation must be an object.")
        return False
    ok = True
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    mode = str(raw.get("mode") or "").strip().lower()
    phases = [bool(raw.get(name, True)) for name in ("restore", "build", "test")]
    required = mode == "required" if mode else bool(raw.get("required", False))
    if required and not any(phases) and not script:
        ok = False
        messages.append("Required validation must enable restore, build, test, or an artifact script.")
    expected = raw.get("expected")
    if expected is not None and not isinstance(expected, dict):
        ok = False
        messages.append("validation.expected must be an object.")
    elif isinstance(expected, dict):
        for key in ("restore", "build", "tests"):
            if key in expected and str(expected[key]).strip().lower() not in _VALIDATION_EXPECTED_OUTCOMES:
                ok = False
                messages.append(
                    f"validation.expected.{key} must be one of: "
                    f"{', '.join(sorted(_VALIDATION_EXPECTED_OUTCOMES))}."
                )
    if "mode" in raw:
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in {"required", "optional", "disabled"}:
            ok = False
            messages.append("validation.mode must be one of: required, optional, disabled.")
    if "allow_deferred" in raw and not isinstance(raw.get("allow_deferred"), bool):
        ok = False
        messages.append("validation.allow_deferred must be a boolean.")
    if "minimum_phase" in raw:
        minimum_phase = str(raw.get("minimum_phase") or "").strip().lower().replace("_", "-")
        allowed_phases = {
            "static", "preflight", "dependencies", "compile", "resources",
            "unit-test", "package", "artifact-check", "lint", "instrumentation", "finalize", "final",
        }
        if minimum_phase not in allowed_phases:
            ok = False
            messages.append(f"Unsupported validation.minimum_phase: {minimum_phase!r}.")
    if "rollback_on_failure" in raw and not isinstance(raw.get("rollback_on_failure"), bool):
        ok = False
        messages.append("validation.rollback_on_failure must be a boolean.")
    if "failure_action" in raw:
        action = str(raw.get("failure_action") or "").strip().lower()
        if action not in {"pause", "rollback", "keep", "prompt"}:
            ok = False
            messages.append("validation.failure_action must be one of: pause, rollback, keep, prompt.")
    if "test_profile" in raw and not isinstance(raw.get("test_profile"), str):
        ok = False
        messages.append("validation.test_profile must be a string.")
    if "execution_environment" in raw:
        environment = str(raw.get("execution_environment") or "").strip().lower()
        if environment and environment not in {"auto", "native-termux", "chroot", "desktop-linux", "android-device", "external-device"}:
            ok = False
            messages.append(f"Unsupported validation.execution_environment: {environment!r}.")
    if bundle:
        strategy = str(raw.get("strategy") or "per-overlay").strip().lower()
        if strategy not in {"per-overlay", "after-all", "both"}:
            ok = False
            messages.append(f"Unsupported bundle validation strategy: {strategy!r}.")
    return ok


def archive_suffix(path: str | Path) -> str:
    """Return the supported archive suffix for a path, or an empty string."""
    name = Path(path).name.lower()
    for suffix in SUPPORTED_ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def _tar_mode_for_suffix(suffix: str, *, writing: bool = False) -> str:
    if suffix in {".tar.gz", ".tgz"}:
        return "w:gz" if writing else "r:gz"
    if suffix in {".tar.xz", ".txz"}:
        return "w:xz" if writing else "r:xz"
    if suffix in {".tar.bz2", ".tbz2"}:
        return "w:bz2" if writing else "r:bz2"
    if suffix == ".tar":
        return "w" if writing else "r"
    raise RuntimeError(f"Unsupported tar suffix: {suffix}")


@contextlib.contextmanager
def open_tar_archive(path: str | Path):
    """Open any supported tar archive, including tar.zst via zstd when needed."""
    archive_path = resolve_artifact_path(path)
    suffix = archive_suffix(archive_path)
    if suffix in {".tar.zst", ".tzst"}:
        zstd = shutil.which("zstd")
        if not zstd:
            raise RuntimeError("tar.zst support requires the zstd command on PATH")
        tmp = tempfile.NamedTemporaryFile(prefix="devtool-tarzst-", suffix=".tar", delete=False)
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                proc = subprocess.run([zstd, "-dc", str(archive_path)], stdout=tmp, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError(f"Failed to decompress tar.zst archive: {stderr or proc.returncode}")
            with tarfile.open(tmp_path, "r") as tf:
                yield tf
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return
    mode = _tar_mode_for_suffix(suffix, writing=False)
    with tarfile.open(archive_path, mode) as tf:
        yield tf


def _write_tar_zst(tar_path: Path, zst_path: Path) -> None:
    zstd = shutil.which("zstd")
    if not zstd:
        raise RuntimeError("tar.zst creation requires the zstd command on PATH")
    proc = subprocess.run([zstd, "-q", "-f", "-o", str(zst_path), str(tar_path)], stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"Failed to create tar.zst archive: {stderr or proc.returncode}")


def write_archive_entries(output: str | Path, entries: dict[str, bytes]) -> Path:
    """Write a supported archive from an archive-name -> bytes mapping."""
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = archive_suffix(output_path)
    if not suffix:
        raise RuntimeError(f"Unsupported archive type: {output_path}")
    if suffix == ".zip":
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                _safe_rel(name)
                zf.writestr(name, data)
        return output_path
    tar_output = output_path
    cleanup_tar: Path | None = None
    if suffix in {".tar.zst", ".tzst"}:
        tmp = tempfile.NamedTemporaryFile(prefix="devtool-bundle-", suffix=".tar", delete=False)
        tmp.close()
        tar_output = Path(tmp.name)
        cleanup_tar = tar_output
        mode = "w"
    else:
        mode = _tar_mode_for_suffix(suffix, writing=True)
    try:
        with tarfile.open(tar_output, mode) as tf:
            for name, data in entries.items():
                _safe_rel(name)
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = int(datetime.now(timezone.utc).timestamp())
                import io
                tf.addfile(info, io.BytesIO(data))
        if suffix in {".tar.zst", ".tzst"}:
            _write_tar_zst(tar_output, output_path)
    finally:
        if cleanup_tar is not None:
            try:
                cleanup_tar.unlink()
            except OSError:
                pass
    return output_path


def list_archive_members(artifact: str | Path, *, include_data: bool = False) -> list[ArchiveMember]:
    path = resolve_artifact_path(artifact)
    members: list[ArchiveMember] = []
    suffix = archive_suffix(path)
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                _safe_rel(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise RuntimeError(f"Unsafe zip symlink member: {info.filename}")
                data = None if info.is_dir() or not include_data else zf.read(info.filename)
                members.append(ArchiveMember(info.filename.rstrip("/"), info.is_dir(), data))
        return members
    if suffix and suffix != ".zip":
        with open_tar_archive(path) as tf:
            for member in tf.getmembers():
                _safe_rel(member.name)
                if member.islnk() or member.issym() or member.isdev():
                    raise RuntimeError(f"Unsafe tar member: {member.name}")
                data = None
                if member.isfile() and include_data:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        with fh:
                            data = fh.read()
                members.append(ArchiveMember(member.name.rstrip("/"), member.isdir(), data))
        return members
    raise RuntimeError(f"Unsupported artifact type: {path}")



def _archive_file_data(artifact: str | Path) -> dict[str, bytes]:
    data: dict[str, bytes] = {}
    for member in list_archive_members(artifact, include_data=True):
        if not member.is_dir and member.data is not None:
            data[member.name] = member.data
    return data


def _implicit_overlay_bundle_manifest(artifact: str | Path) -> dict[str, Any]:
    """Recognize legacy wrapper archives that contain only nested overlays.

    Older bundle producers sometimes omitted ``devtool-bundle.json`` and simply
    placed ordered overlay archives at the root. Treating that wrapper as a
    normal overlay copies the inner ZIP/TAR files into the target repository.
    Detect this shape conservatively: every non-metadata payload member must be
    a supported archive and at least one nested archive must be present.
    """
    try:
        members = [
            member for member in list_archive_members(artifact, include_data=False)
            if not member.is_dir and not is_artifact_metadata_member(member.name)
        ]
    except Exception:
        return {}
    if not members or any(not archive_suffix(member.name) for member in members):
        return {}
    ordered = sorted((member.name for member in members), key=lambda value: value.casefold())
    return {
        "kind": "devtool.overlay_bundle",
        "version": 1,
        "name": Path(artifact).name,
        "description": "Implicit legacy wrapper bundle detected by devtool.",
        "implicit": True,
        "apply_order": [{"path": name, "label": Path(name).stem} for name in ordered],
        "stop_on_failure": True,
        "validate_each": True,
        "validate_final": True,
    }


def read_overlay_bundle_manifest(artifact: str | Path) -> dict[str, Any]:
    """Read an explicit bundle manifest or synthesize one for legacy wrappers."""
    try:
        for member in list_archive_members(artifact, include_data=True):
            basename = Path(member.name).name
            if basename not in (BUNDLE_MANIFEST_NAMES | MANIFEST_NAMES) or member.data is None:
                continue
            data = json.loads(member.data.decode("utf-8"))
            if not isinstance(data, dict):
                continue
            kind = str(data.get("kind") or data.get("type") or "").strip().lower()
            if basename in BUNDLE_MANIFEST_NAMES or kind in BUNDLE_KINDS or isinstance(data.get("apply_order"), list) or isinstance(data.get("overlays"), list):
                return data
    except Exception:
        return {}
    return _implicit_overlay_bundle_manifest(artifact)


def is_overlay_bundle(artifact: str | Path) -> bool:
    manifest = read_overlay_bundle_manifest(artifact)
    if not manifest:
        return False
    kind = str(manifest.get("kind") or manifest.get("type") or "").strip().lower()
    return kind in BUNDLE_KINDS or isinstance(manifest.get("apply_order"), list)


def _bundle_overlay_spec_from_item(item: Any, index: int) -> BundleOverlaySpec:
    """Normalize a bundle apply_order entry.

    Backward-compatible string entries are accepted as simple overlay paths.
    Preferred object entries may specify path, target, and label:
    {"path": "overlays/001.zip", "target": "s3mo", "label": "S3MO"}
    """
    if isinstance(item, str):
        name = item.strip()
        overlay_id = Path(name).stem
        target = ""
        label = ""
        required = True
        after: tuple[str, ...] = ()
        requires: tuple[str, ...] = ()
    elif isinstance(item, dict):
        name = str(item.get("path") or item.get("artifact") or item.get("overlay") or "").strip()
        overlay_id = str(item.get("id") or Path(name).stem).strip()
        target = str(item.get("target") or item.get("validate_target") or "").strip()
        label = str(item.get("label") or item.get("name") or item.get("description") or "").strip()
        required = bool(item.get("required", True))
        after = tuple(str(value).strip() for value in item.get("after", []) if str(value).strip()) if isinstance(item.get("after"), list) else ()
        requires = tuple(str(value).strip() for value in item.get("requires", []) if str(value).strip()) if isinstance(item.get("requires"), list) else ()
    else:
        raise RuntimeError(f"Overlay bundle apply_order item #{index} must be a string path or object.")
    if not name:
        raise RuntimeError(f"Overlay bundle apply_order item #{index} is missing a path.")
    _safe_rel(name)
    if not archive_suffix(name):
        raise RuntimeError(f"Bundle member is not a supported archive: {name}")
    if not overlay_id:
        raise RuntimeError(f"Overlay bundle item #{index} is missing a stable id.")
    return BundleOverlaySpec(
        path=name,
        id=overlay_id,
        target=target,
        label=label,
        required=required,
        after=after,
        requires=requires,
        index=index,
    )


def _bundle_overlay_specs(bundle: str | Path, manifest: dict[str, Any] | None = None) -> list[BundleOverlaySpec]:
    manifest = manifest or read_overlay_bundle_manifest(bundle)
    raw_order = manifest.get("apply_order") or manifest.get("overlays")
    if not isinstance(raw_order, list):
        raise RuntimeError("Overlay bundle manifest must contain an apply_order list.")
    specs: list[BundleOverlaySpec] = []
    for index, item in enumerate(raw_order, 1):
        spec = _bundle_overlay_spec_from_item(item, index)
        specs.append(spec)
    if not specs:
        raise RuntimeError("Overlay bundle apply_order is empty.")
    ids = [spec.id for spec in specs]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Overlay bundle ids must be unique.")
    by_id = {spec.id: spec for spec in specs}
    for spec in specs:
        missing = [value for value in (*spec.after, *spec.requires) if value not in by_id]
        if missing:
            raise RuntimeError(f"Overlay {spec.id!r} references unknown dependencies: {', '.join(missing)}")

    # Stable topological sort: explicit dependencies first, original array order as fallback.
    remaining = {spec.id: set((*spec.after, *spec.requires)) for spec in specs}
    ordered: list[BundleOverlaySpec] = []
    while remaining:
        ready = [spec for spec in specs if spec.id in remaining and not remaining[spec.id]]
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise RuntimeError(f"Overlay bundle dependency cycle detected: {cycle}")
        for spec in ready:
            ordered.append(spec)
            remaining.pop(spec.id, None)
            for deps in remaining.values():
                deps.discard(spec.id)
    return [BundleOverlaySpec(
        path=spec.path,
        id=spec.id,
        target=spec.target,
        label=spec.label,
        required=spec.required,
        after=spec.after,
        requires=spec.requires,
        index=index,
    ) for index, spec in enumerate(ordered, 1)]


def _bundle_apply_order(bundle: str | Path, manifest: dict[str, Any] | None = None) -> list[str]:
    return [spec.path for spec in _bundle_overlay_specs(bundle, manifest)]


def _target_from_nested_overlay_manifest(overlay: Path) -> str:
    try:
        manifest = read_artifact_manifest(overlay)
    except Exception:
        return ""
    for key in ("validate_target", "target"):
        value = str(manifest.get(key) or "").strip()
        if value:
            return value
    return ""


def materialize_overlay_bundle_entries(bundle: str | Path, target_dir: str | Path) -> tuple[dict[str, Any], list[MaterializedBundleOverlay]]:
    """Extract nested overlay archives listed by a bundle manifest into target_dir.

    Returns structured entries that preserve per-overlay target/label metadata.
    Target resolution order is: entry.target -> nested overlay
    validate_target/target -> blank (caller must use an explicit CLI target or solution).
    """
    manifest = read_overlay_bundle_manifest(bundle)
    if not manifest:
        raise RuntimeError("Archive is not an overlay bundle: missing devtool-bundle.json")
    specs = _bundle_overlay_specs(bundle, manifest)
    files = _archive_file_data(bundle)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    materialized: list[MaterializedBundleOverlay] = []
    missing_optional: set[str] = set()
    for spec in specs:
        data = files.get(spec.path)
        if data is None:
            if spec.required:
                raise RuntimeError(f"Overlay bundle lists missing required member: {spec.path}")
            missing_optional.add(spec.id)
            continue
        out = target / spec.path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        resolved_target = spec.target or _target_from_nested_overlay_manifest(out)
        materialized.append(
            MaterializedBundleOverlay(
                path=out,
                bundle_path=spec.path,
                id=spec.id,
                target=resolved_target,
                label=spec.label,
                required=spec.required,
                index=spec.index,
            )
        )
    for spec in specs:
        unavailable = [dependency for dependency in spec.requires if dependency in missing_optional]
        if unavailable and spec.id not in missing_optional:
            raise RuntimeError(f"Overlay {spec.id!r} requires missing optional overlay(s): {', '.join(unavailable)}")
    return manifest, materialized


def materialize_overlay_bundle(bundle: str | Path, target_dir: str | Path) -> tuple[dict[str, Any], list[Path]]:
    """Extract nested overlay archives listed by a bundle manifest into target_dir."""
    manifest, entries = materialize_overlay_bundle_entries(bundle, target_dir)
    return manifest, [entry.path for entry in entries]


def overlay_bundle_targets(bundle: str | Path) -> list[str]:
    """Return distinct per-overlay validation targets declared by a bundle."""
    targets: list[str] = []
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-targets-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            target = entry.target.strip()
            if target and target not in targets:
                targets.append(target)
    return targets

def overlay_bundle_conflicts(bundle: str | Path) -> dict[str, list[str]]:
    """Return payload files written by more than one nested overlay in apply order."""
    writers: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-conflicts-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            try:
                members = list_archive_members(entry.path, include_data=False)
            except Exception:
                continue
            name = entry.label or Path(entry.bundle_path).name
            if entry.target:
                name = f"{name} [{entry.target}]"
            for member in members:
                if member.is_dir or is_artifact_metadata_member(member.name):
                    continue
                writers.setdefault(member.name, []).append(name)
    return {path: names for path, names in writers.items() if len(names) > 1}


def validate_overlay_bundle(
    repo_root: Path,
    bundle: str | Path,
    *,
    fail_on_conflict: bool = False,
    ignore_checksum: bool = False,
) -> ArtifactValidation:
    manifest = read_overlay_bundle_manifest(bundle)
    messages: list[str] = []
    ok = True
    if not manifest:
        return ArtifactValidation(False, ["Archive is not an overlay bundle: missing devtool-bundle.json"], {})
    kind = str(manifest.get("kind") or manifest.get("type") or "").strip().lower()
    if kind and kind not in BUNDLE_KINDS:
        ok = False
        messages.append(f"Bundle manifest kind/type is {kind!r}, expected 'devtool.overlay_bundle'.")
    try:
        schema_version = int(manifest.get("schema_version", manifest.get("version", 1)) or 1)
    except (TypeError, ValueError):
        schema_version = 0
        ok = False
        messages.append("Bundle schema_version must be an integer.")
    if schema_version > 2:
        ok = False
        messages.append(f"Unsupported bundle schema_version {schema_version}; maximum supported version is 2.")
    if not _validate_repository_identity(repo_root, manifest, messages):
        ok = False
    if not _validate_validation_policy(manifest, messages, bundle=True):
        ok = False
    try:
        specs = _bundle_overlay_specs(bundle, manifest)
        order = [spec.path for spec in specs]
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)
    try:
        members = {m.name for m in list_archive_members(bundle, include_data=False) if not m.is_dir}
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)
    required_paths = {spec.path for spec in specs if spec.required}
    missing = [name for name in order if name not in members and name in required_paths]
    if missing:
        ok = False
        messages.append("Bundle apply_order lists missing overlays: " + ", ".join(missing[:10]))
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-validate-") as tmp:
        try:
            _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        except Exception as exc:
            return ArtifactValidation(False, [str(exc)], manifest)
        for entry in entries:
            validation = validate_artifact(
                repo_root,
                entry.path,
                artifact_type="overlay",
                check_source_state=False,
                ignore_checksum=ignore_checksum,
            )
            label = entry.label or Path(entry.bundle_path).name
            target_note = f" target={entry.target}" if entry.target else ""
            prefix = f"[{entry.index}/{len(entries)}] {label}{target_note}"
            if validation.ok:
                messages.append(f"{prefix}: valid")
            else:
                ok = False
                messages.append(f"{prefix}: invalid")
                messages.extend(f"  {msg}" for msg in validation.messages)
    conflicts = overlay_bundle_conflicts(bundle)
    if conflicts:
        lines = []
        for path, writers in sorted(conflicts.items())[:20]:
            lines.append(f"  {path}: " + " -> ".join(writers))
        conflict_text = "Bundle conflict report (later overlay wins):\n" + "\n".join(lines)
        if len(conflicts) > 20:
            conflict_text += f"\n  ... {len(conflicts) - 20} more conflict(s)"
        messages.append(conflict_text)
        apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
        conflict_policy = str(apply.get("conflict_policy") or "warn").strip().lower()
        if fail_on_conflict or conflict_policy == "fail":
            ok = False
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    if apply.get("atomic", True) is not True:
        ok = False
        messages.append("Overlay bundles currently require apply.atomic=true.")
    if str(apply.get("path_policy") or "repository-relative") != "repository-relative":
        ok = False
        messages.append("Only apply.path_policy='repository-relative' is supported.")
    if bool(apply.get("requires_clean_worktree", False)) and _git_text(repo_root, "status", "--porcelain"):
        ok = False
        messages.append("Bundle requires a clean Git worktree.")
    commit_enabled, commit_message, commit_strategy = artifact_commit_policy(manifest)
    if commit_strategy not in {"single", "per-overlay"}:
        ok = False
        messages.append(f"Unsupported bundle commit strategy: {commit_strategy!r}.")
    if bool(apply.get("atomic", True)) and commit_strategy == "per-overlay" and commit_enabled:
        ok = False
        messages.append("Atomic bundles must use apply.commit.strategy='single'.")
    if isinstance(apply.get("commit"), dict) and bool(apply["commit"].get("enabled", False)) and not commit_message:
        ok = False
        messages.append("Enabled bundle commits require a non-empty message.")
    script = artifact_validation_script(manifest)
    if script:
        script_path = str(script.get("path") or "").strip()
        script_sha = str(script.get("sha256") or "").strip().lower()
        runner = str(script.get("runner") or "bash").strip()
        try:
            rel = _safe_rel(script_path)
            if not rel.parts or rel.parts[0] != ARTIFACT_METADATA_DIRECTORY:
                raise RuntimeError("Validation scripts must live under .devtool-artifact/.")
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
        member_data = next((member.data for member in list_archive_members(bundle, include_data=True) if member.name == script_path and not member.is_dir), None)
        if member_data is None:
            ok = False
            messages.append(f"Bundle validation script is missing from archive: {script_path}")
        elif not ignore_checksum:
            if not script_sha:
                ok = False
                messages.append("validation.script.sha256 is required.")
            elif not _is_sha256(script_sha):
                ok = False
                messages.append("validation.script.sha256 is not a valid SHA-256.")
            elif hashlib.sha256(member_data).hexdigest().lower() != script_sha:
                ok = False
                messages.append("Bundle validation script SHA-256 mismatch.")
        if runner not in {"bash", "sh", "python3"}:
            ok = False
            messages.append(f"Unsupported validation script runner: {runner!r}.")
        timeout = script.get("timeout_seconds", 900)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            ok = False
            messages.append("validation.script.timeout_seconds must be a positive integer.")
    if ignore_checksum:
        messages.append("Warning: checksum verification skipped by --ignore-checksum.")
    if ok:
        messages.insert(0, f"Overlay bundle validation passed ({len(order)} overlay(s)).")
    return ArtifactValidation(ok, messages, manifest)


def preview_overlay_bundle(repo_root: Path, bundle: str | Path) -> dict[str, Any]:
    changes: list[dict[str, str]] = []
    total_files = 0
    manifest: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-preview-") as tmp:
        manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            preview = preview_artifact(repo_root, entry.path, snapshot=False)
            total_files += int(preview.get("files", 0))
            for change in preview.get("changes", []):
                if not isinstance(change, dict):
                    continue
                updated = {str(k): str(v) for k, v in change.items()}
                updated["overlay"] = Path(entry.bundle_path).name
                updated["overlay_path"] = entry.bundle_path
                updated["overlay_index"] = str(entry.index)
                if entry.target:
                    updated["target"] = entry.target
                if entry.label:
                    updated["label"] = entry.label
                changes.append(updated)
    return {
        "artifact": str(resolve_artifact_path(bundle)),
        "snapshot": False,
        "bundle": True,
        "bundle_name": str(manifest.get("name") or Path(bundle).name),
        "files": total_files,
        "changes": changes,
        "counts": {
            kind: sum(1 for change in changes if change.get("kind") == kind)
            for kind in sorted({str(change.get("kind", "")) for change in changes})
        },
    }


def diff_overlay_bundle(repo_root: Path, bundle: str | Path, *, context: int = 3, max_lines: int = 4000) -> str:
    sections: list[str] = []
    remaining = max_lines
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-diff-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            if remaining <= 0:
                sections.append("... bundle diff truncated")
                break
            text = diff_artifact(repo_root, entry.path, snapshot=False, context=context, max_lines=remaining)
            lines = text.splitlines()
            remaining -= len(lines)
            label = entry.label or Path(entry.bundle_path).name
            target_note = f" target={entry.target}" if entry.target else ""
            sections.append(f"# [{entry.index}/{len(entries)}] {label}{target_note}\n" + text.rstrip())
    return "\n\n".join(section for section in sections if section).rstrip() + "\n"


def create_overlay_bundle(
    output: str | Path,
    overlays: list[str | Path],
    *,
    name: str = "",
    description: str = "",
    targets: list[str] | None = None,
    repository: dict[str, str] | None = None,
    commit_message: str = "",
    validation_required: bool = False,
    validation_strategy: str = "per-overlay",
    validation_script: str | Path | None = None,
    validation_runner: str = "bash",
    validation_timeout_seconds: int = 900,
) -> Path:
    if not overlays:
        raise RuntimeError("No overlays were provided for the bundle.")
    entries: dict[str, bytes] = {}
    apply_order: list[dict[str, Any]] = []
    used_names: set[str] = set()
    normalized_targets = list(targets or [])
    if normalized_targets and len(normalized_targets) != len(overlays):
        raise RuntimeError("--targets must provide exactly one target per overlay, or be omitted.")
    for index, raw in enumerate(overlays, 1):
        path = resolve_artifact_path(raw)
        if not path.exists():
            raise RuntimeError(f"Overlay not found: {raw}")
        if not archive_suffix(path):
            raise RuntimeError(f"Unsupported overlay archive type: {path}")
        arcname = f"overlays/{index:03d}_{path.name}"
        while arcname in used_names:
            arcname = f"overlays/{index:03d}_{len(used_names)}_{path.name}"
        used_names.add(arcname)
        entries[arcname] = path.read_bytes()
        nested_manifest = read_artifact_manifest(path)
        item: dict[str, Any] = {
            "id": str(nested_manifest.get("artifact_id") or path.stem),
            "path": arcname,
            "label": str(nested_manifest.get("name") or path.stem),
            "required": True,
        }
        if normalized_targets:
            target = normalized_targets[index - 1].strip()
            if target and target != "-":
                item["target"] = target
        else:
            inferred = _target_from_nested_overlay_manifest(path)
            if inferred:
                item["target"] = inferred
        apply_order.append(item)
    if validation_strategy not in {"per-overlay", "after-all", "both"}:
        raise RuntimeError(f"Unsupported bundle validation strategy: {validation_strategy}")
    if validation_runner not in {"bash", "sh", "python3"}:
        raise RuntimeError(f"Unsupported validation script runner: {validation_runner}")

    bundle_manifest: dict[str, Any] = {
        "schema_version": 2,
        "type": "overlay_bundle",
        "kind": "devtool.overlay_bundle",
        "name": name or Path(output).name,
        "description": description,
        "target_mode": "per_overlay",
        "overlays": apply_order,
        "apply_order": apply_order,
        "apply": {
            "atomic": True,
            "conflict_policy": "fail",
            "requires_clean_worktree": False,
            "commit": {
                "enabled": bool(commit_message.strip()),
                "strategy": "single",
                "message": commit_message.strip(),
            },
        },
        "validation": {
            "required": bool(validation_required),
            "strategy": validation_strategy,
            "rollback_on_failure": True,
            "failure_action": "pause",
        },
    }
    clean_repository = {str(key): str(value).strip() for key, value in (repository or {}).items() if str(value).strip()}
    if clean_repository:
        bundle_manifest["repository"] = clean_repository
    if validation_script:
        script_path = Path(validation_script).expanduser().resolve()
        if not script_path.is_file():
            raise RuntimeError(f"Bundle validation script not found: {validation_script}")
        script_data = script_path.read_bytes()
        suffix = script_path.suffix or ".sh"
        archive_script_path = f"{ARTIFACT_METADATA_DIRECTORY}/validate-bundle{suffix}"
        entries[archive_script_path] = script_data
        bundle_manifest["validation"]["script"] = {
            "path": archive_script_path,
            "runner": validation_runner,
            "sha256": hashlib.sha256(script_data).hexdigest(),
            "timeout_seconds": int(validation_timeout_seconds),
        }
    entries["devtool-bundle.json"] = (json.dumps(bundle_manifest, indent=2) + "\n").encode("utf-8")
    return write_archive_entries(output, entries)

def read_artifact_manifest(artifact: str | Path) -> dict[str, Any]:
    bundle_manifest = read_overlay_bundle_manifest(artifact)
    if bundle_manifest and (str(bundle_manifest.get("kind") or bundle_manifest.get("type") or "").strip().lower() in BUNDLE_KINDS or isinstance(bundle_manifest.get("apply_order"), list)):
        data = dict(bundle_manifest)
        data.setdefault("type", "overlay_bundle")
        data.setdefault("artifact_type", "overlay_bundle")
        return data
    artifact_path = resolve_artifact_path(artifact)
    sidecars = [
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ]
    for candidate in sidecars:
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    try:
        for member in list_archive_members(artifact_path, include_data=True):
            if Path(member.name).name not in MANIFEST_NAMES or member.data is None:
                continue
            data = json.loads(member.data.decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _normalized_remote(value: str) -> str:
    text = value.strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if text.startswith("git@") and ":" in text:
        host_path = text[4:]
        host, path = host_path.split(":", 1)
        text = f"https://{host}/{path}"
    return text.casefold()


def _git_text(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _validate_repository_identity(repo_root: Path, manifest: dict[str, Any], messages: list[str]) -> bool:
    repository = _manifest_repository(manifest)
    if not repository:
        return True
    ok = True
    expected_name = str(repository.get("name") or "").strip()
    if expected_name and expected_name not in {repo_root.name, str(repo_root)}:
        ok = False
        messages.append(f"Manifest expects repository {expected_name!r}, current repository is {repo_root.name!r}.")

    expected_remote = str(repository.get("remote") or "").strip()
    current_remote = _git_text(repo_root, "remote", "get-url", "origin") if expected_remote else ""
    if expected_remote and current_remote and _normalized_remote(expected_remote) != _normalized_remote(current_remote):
        if bool(repository.get("enforce_remote", False)):
            ok = False
            messages.append(f"Repository remote mismatch: expected {expected_remote!r}, found {current_remote!r}.")
        else:
            messages.append(f"Warning: repository remote differs from artifact metadata ({current_remote!r}).")

    expected_branch = str(repository.get("branch") or "").strip()
    current_branch = _git_text(repo_root, "branch", "--show-current") if expected_branch else ""
    if expected_branch and current_branch and expected_branch != current_branch:
        if bool(repository.get("enforce_branch", False)):
            ok = False
            messages.append(f"Repository branch mismatch: expected {expected_branch!r}, found {current_branch!r}.")
        else:
            messages.append(f"Warning: current branch is {current_branch!r}; artifact metadata names {expected_branch!r}.")
    return ok


def _validate_inventory(
    repo_root: Path,
    manifest: dict[str, Any],
    members: list[ArchiveMember],
    messages: list[str],
    *,
    check_source_state: bool = True,
    ignore_checksum: bool = False,
) -> bool:
    entries = artifact_inventory_entries(manifest)
    if not entries:
        return True
    ok = True
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    try:
        format_version = int(inventory.get("format", 1) or 1)
    except (TypeError, ValueError):
        messages.append("Inventory format must be an integer.")
        return False
    if format_version != 1:
        messages.append(f"Unsupported inventory format: {format_version}.")
        return False

    member_map = {member.name: member for member in members if not member.is_dir and not is_artifact_metadata_member(member.name)}
    declared_payload: set[str] = set()
    seen: set[str] = set()
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    conflict_policy = str(apply.get("conflict_policy") or "fail").strip().lower()

    for index, entry in enumerate(entries, 1):
        path = str(entry.get("path") or "").strip()
        action = str(entry.get("action") or "").strip().lower()
        if not path:
            ok = False
            messages.append(f"Inventory entry #{index} is missing path.")
            continue
        try:
            rel = _safe_rel(path)
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
            continue
        if is_artifact_metadata_member(path):
            ok = False
            messages.append(f"Inventory entry cannot target reserved artifact metadata: {path}")
            continue
        if path in seen:
            ok = False
            messages.append(f"Inventory contains duplicate path: {path}")
            continue
        seen.add(path)
        if action not in {"add", "modify", "delete"}:
            ok = False
            messages.append(f"Inventory entry {path!r} has unsupported action {action!r}.")
            continue

        target = repo_root / rel
        source_sha = str(entry.get("source_sha256") or "").strip().lower()
        if not ignore_checksum:
            if action in {"modify", "delete"} and not source_sha:
                ok = False
                messages.append(f"Inventory {action} entry requires source_sha256: {path}")
            elif source_sha and not _is_sha256(source_sha):
                ok = False
                messages.append(f"Inventory source_sha256 is not a valid SHA-256 for {path}.")
        if check_source_state:
            if action in {"modify", "delete"}:
                if not target.exists() or not target.is_file():
                    ok = False
                    messages.append(f"Inventory expects existing file for {action}: {path}")
                elif not ignore_checksum and source_sha and sha256_file(target).lower() != source_sha:
                    ok = False
                    messages.append(f"Source SHA-256 mismatch for {path}.")
            elif action == "add" and target.exists() and conflict_policy == "fail":
                ok = False
                messages.append(f"Inventory add conflicts with existing path: {path}")

        if action == "delete":
            continue
        declared_payload.add(path)
        member = member_map.get(path)
        if member is None:
            ok = False
            messages.append(f"Inventory lists payload missing from archive: {path}")
            continue
        expected_size = entry.get("size")
        expected_sha = str(entry.get("sha256") or "").strip().lower()
        if not ignore_checksum and not expected_sha:
            ok = False
            messages.append(f"Inventory {action} entry requires sha256: {path}")
        if expected_size is None:
            ok = False
            messages.append(f"Inventory {action} entry requires size: {path}")
        elif not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            ok = False
            messages.append(f"Inventory size must be a non-negative integer for {path}.")
            expected_size = None
        if not ignore_checksum and expected_sha and not _is_sha256(expected_sha):
            ok = False
            messages.append(f"Inventory sha256 is not a valid SHA-256 for {path}.")
        if expected_size is not None or (expected_sha and not ignore_checksum):
            # members passed by validate_artifact include data when inventory is present.
            payload = member.data or b""
            if expected_size is not None and len(payload) != expected_size:
                ok = False
                messages.append(f"Payload size mismatch for {path}: expected {expected_size}, found {len(payload)}.")
            if not ignore_checksum and expected_sha and hashlib.sha256(payload).hexdigest().lower() != expected_sha:
                ok = False
                messages.append(f"Payload SHA-256 mismatch for {path}.")

    undeclared = sorted(set(member_map) - declared_payload)
    if undeclared:
        ok = False
        messages.append("Archive payload is missing from inventory: " + ", ".join(undeclared[:10]))
    return ok


def validate_artifact(
    repo_root: Path,
    artifact: str | Path,
    *,
    artifact_type: str = "overlay",
    check_source_state: bool = True,
    ignore_checksum: bool = False,
) -> ArtifactValidation:
    if artifact_type in {"overlay", "overlay_bundle"} and is_overlay_bundle(artifact):
        return validate_overlay_bundle(repo_root, artifact, ignore_checksum=ignore_checksum)
    manifest = read_artifact_manifest(artifact)
    messages: list[str] = []
    ok = True

    try:
        members = list_archive_members(
            artifact,
            include_data=bool(artifact_inventory_entries(manifest) or artifact_validation_script(manifest)),
        )
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)

    if not members:
        return ArtifactValidation(False, ["Artifact archive is empty."], manifest)

    try:
        schema_version = int(manifest.get("schema_version", 1) or 1) if manifest else 1
    except (TypeError, ValueError):
        schema_version = 0
        ok = False
        messages.append("Artifact schema_version must be an integer.")
    if schema_version > 2:
        ok = False
        messages.append(f"Unsupported artifact schema_version {schema_version}; maximum supported version is 2.")

    if not _validate_repository_identity(repo_root, manifest, messages):
        ok = False
    if not _validate_validation_policy(manifest, messages, bundle=False):
        ok = False

    expected_type = str(manifest.get("type") or manifest.get("artifact_type") or "").strip().lower()
    if expected_type and expected_type not in {artifact_type, "patch" if artifact_type == "overlay" else artifact_type}:
        ok = False
        messages.append(f"Manifest type is {expected_type!r}, but command expects {artifact_type!r}.")

    expected_solution = artifact_solution(manifest)
    if expected_solution:
        solution_path = repo_root / expected_solution
        names = {m.name for m in members}
        if not solution_path.exists() and expected_solution not in names:
            messages.append(f"Manifest references solution {expected_solution!r}; it is not present before apply and is not included in the artifact.")

    declared_files = manifest.get("files")
    if isinstance(declared_files, list):
        member_files = {m.name for m in members if not m.is_dir}
        missing = [str(item) for item in declared_files if str(item) not in member_files]
        if missing:
            ok = False
            messages.append("Manifest lists files missing from archive: " + ", ".join(missing[:10]))

    for root in artifact_workspace_roots(manifest):
        try:
            workspace = _safe_rel(root)
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
            continue
        if not (repo_root / workspace).exists() and not any(member.name == root or member.name.startswith(root.rstrip("/") + "/") for member in members):
            messages.append(f"Warning: workspace root {root!r} does not exist before apply and is not included in the artifact.")

    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    if apply.get("atomic", True) is not True:
        ok = False
        messages.append("Overlay artifacts currently require apply.atomic=true.")
    if str(apply.get("path_policy") or "repository-relative") != "repository-relative":
        ok = False
        messages.append("Only apply.path_policy='repository-relative' is supported.")
    conflict_policy = str(apply.get("conflict_policy") or "fail").strip().lower()
    if conflict_policy not in {"fail", "overwrite"}:
        ok = False
        messages.append(f"Unsupported apply.conflict_policy: {conflict_policy!r}.")
    commit_enabled, commit_message, commit_strategy = artifact_commit_policy(manifest)
    if commit_strategy not in {"single"}:
        ok = False
        messages.append("Single overlay artifacts only support apply.commit.strategy='single'.")
    if isinstance(apply.get("commit"), dict) and bool(apply["commit"].get("enabled", False)) and not commit_message:
        ok = False
        messages.append("Enabled artifact commits require a non-empty message.")
    if bool(apply.get("requires_clean_worktree", False)):
        status = _git_text(repo_root, "status", "--porcelain")
        if status:
            ok = False
            messages.append("Artifact requires a clean Git worktree.")

    if not _validate_inventory(
        repo_root,
        manifest,
        members,
        messages,
        check_source_state=check_source_state,
        ignore_checksum=ignore_checksum,
    ):
        ok = False

    script = artifact_validation_script(manifest)
    if script:
        script_path = str(script.get("path") or "").strip()
        runner = str(script.get("runner") or "bash").strip()
        script_sha = str(script.get("sha256") or "").strip().lower()
        if not script_path:
            ok = False
            messages.append("validation.script.path is required.")
        else:
            try:
                rel = _safe_rel(script_path)
                if not rel.parts or rel.parts[0] != ARTIFACT_METADATA_DIRECTORY:
                    raise RuntimeError("Validation scripts must live under .devtool-artifact/.")
            except RuntimeError as exc:
                ok = False
                messages.append(str(exc))
            script_member = next((member for member in members if member.name == script_path and not member.is_dir), None)
            if script_member is None:
                ok = False
                messages.append(f"Validation script is missing from archive: {script_path}")
            elif not ignore_checksum:
                if not script_sha:
                    ok = False
                    messages.append("validation.script.sha256 is required.")
                elif not _is_sha256(script_sha):
                    ok = False
                    messages.append("validation.script.sha256 is not a valid SHA-256.")
                elif hashlib.sha256(script_member.data or b"").hexdigest().lower() != script_sha:
                    ok = False
                    messages.append("Validation script SHA-256 mismatch.")
        if runner not in {"bash", "sh", "python3"}:
            ok = False
            messages.append(f"Unsupported validation script runner: {runner!r}.")
        timeout = script.get("timeout_seconds", 900)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            ok = False
            messages.append("validation.script.timeout_seconds must be a positive integer.")

    if ignore_checksum:
        messages.append("Warning: checksum verification skipped by --ignore-checksum.")
    if not messages:
        messages.append("Artifact validation passed.")
    return ArtifactValidation(ok, messages, manifest)



def read_delete_manifest(artifact: str | Path) -> list[str]:
    """Read overlay deletion entries from .devtool-delete without treating it as payload."""
    entries: list[str] = []
    manifest = read_artifact_manifest(artifact)
    for item in artifact_inventory_entries(manifest):
        if str(item.get("action") or "").strip().lower() == "delete":
            path = str(item.get("path") or "").strip()
            if path:
                entries.append(path)
    try:
        for member in list_archive_members(artifact, include_data=True):
            if Path(member.name).name != DELETE_MANIFEST_NAME or member.data is None:
                continue
            text = member.data.decode("utf-8").strip()
            if not text:
                continue
            # Allow either a JSON string list or simple newline-delimited paths.
            if text.startswith("["):
                try:
                    data = json.loads(text)
                    if isinstance(data, list):
                        entries.extend(str(item).strip() for item in data if str(item).strip())
                        continue
                except Exception:
                    pass
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append(line)
    except Exception:
        return []
    return list(dict.fromkeys(entries))

def _bytes_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preview_artifact(repo_root: Path, artifact: str | Path, *, snapshot: bool = False) -> dict[str, Any]:
    if not snapshot and is_overlay_bundle(artifact):
        return preview_overlay_bundle(repo_root, artifact)
    members = list_archive_members(artifact, include_data=True)
    file_members = [m for m in members if not m.is_dir]
    incoming_files = {Path(m.name): m for m in file_members if not is_artifact_metadata_member(m.name)}
    changes: list[dict[str, str]] = []

    if snapshot:
        existing_files = {
            p.relative_to(repo_root)
            for p in repo_root.rglob("*")
            if p.is_file() and ".git" not in p.parts and HISTORY_PATH.parts[0] not in p.parts
        }
        incoming_paths = set(incoming_files)
        for rel in sorted(existing_files - incoming_paths):
            changes.append({"kind": "delete", "path": str(rel)})
    else:
        for delete_path in read_delete_manifest(artifact):
            changes.append({"kind": "delete", "path": delete_path})

    for rel, member in sorted(incoming_files.items(), key=lambda item: str(item[0])):
        target = repo_root / rel
        if not target.exists():
            changes.append({"kind": "add", "path": str(rel)})
            continue
        if target.is_dir():
            changes.append({"kind": "replace-dir", "path": str(rel)})
            continue
        current_hash = sha256_file(target)
        incoming_hash = _bytes_sha(member.data or b"")
        if current_hash != incoming_hash:
            changes.append({"kind": "modify", "path": str(rel)})
        else:
            changes.append({"kind": "unchanged", "path": str(rel)})

    return {
        "artifact": str(resolve_artifact_path(artifact)),
        "snapshot": snapshot,
        "files": len(incoming_files),
        "changes": changes,
        "counts": {
            kind: sum(1 for change in changes if change["kind"] == kind)
            for kind in sorted({change["kind"] for change in changes})
        },
    }


def _is_binary_data(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _decode_text_lines(data: bytes) -> list[str]:
    return data.decode("utf-8").splitlines(keepends=True)


def diff_artifact(repo_root: Path, artifact: str | Path, *, snapshot: bool = False, context: int = 3, max_lines: int = 4000) -> str:
    """Return a repo-relative unified diff for the payload changes in an artifact."""
    if not snapshot and is_overlay_bundle(artifact):
        return diff_overlay_bundle(repo_root, artifact, context=context, max_lines=max_lines)
    members = list_archive_members(artifact, include_data=True)
    file_members = [m for m in members if not m.is_dir]
    incoming_files = {Path(m.name): m for m in file_members if not is_artifact_metadata_member(m.name)}
    hunks: list[str] = []
    truncated = False

    def append_line(line: str) -> None:
        nonlocal truncated
        if len(hunks) >= max_lines:
            truncated = True
            return
        hunks.append(line.rstrip("\n"))

    def append_binary_notice(path: Path, kind: str) -> None:
        append_line(f"diff --git a/{path} b/{path}")
        append_line(f"Binary files differ ({kind}): {path}")
        append_line("")

    def append_text_diff(path: Path, before: bytes, after: bytes, *, fromfile: str | None = None, tofile: str | None = None) -> None:
        if _is_binary_data(before) or _is_binary_data(after):
            append_binary_notice(path, "modified" if before and after else ("added" if after else "deleted"))
            return
        diff_lines = difflib.unified_diff(
            _decode_text_lines(before),
            _decode_text_lines(after),
            fromfile=fromfile or f"a/{path}",
            tofile=tofile or f"b/{path}",
            n=context,
        )
        emitted = False
        for line in diff_lines:
            emitted = True
            append_line(line)
            if truncated:
                return
        if emitted:
            append_line("")

    delete_paths: list[Path] = []
    if snapshot:
        existing_files = {
            p.relative_to(repo_root)
            for p in repo_root.rglob("*")
            if p.is_file() and ".git" not in p.parts and HISTORY_PATH.parts[0] not in p.parts
        }
        delete_paths = sorted(existing_files - set(incoming_files))
    else:
        delete_paths = sorted(_safe_rel(path) for path in read_delete_manifest(artifact))

    for rel in delete_paths:
        target = repo_root / rel
        if not target.exists() or not target.is_file():
            continue
        append_text_diff(rel, target.read_bytes(), b"", fromfile=f"a/{rel}", tofile="/dev/null")
        if truncated:
            break

    if not truncated:
        for rel, member in sorted(incoming_files.items(), key=lambda item: str(item[0])):
            target = repo_root / rel
            after = member.data or b""
            if target.exists() and target.is_file():
                before = target.read_bytes()
                if _bytes_sha(before) == _bytes_sha(after):
                    continue
                append_text_diff(rel, before, after)
            elif target.exists() and target.is_dir():
                append_line(f"diff --git a/{rel} b/{rel}")
                append_line(f"Cannot diff directory replacement: {rel}")
                append_line("")
            else:
                append_text_diff(rel, b"", after, fromfile="/dev/null", tofile=f"b/{rel}")
            if truncated:
                break

    if truncated:
        hunks.append(f"... diff truncated after {max_lines} line(s)")
    if not hunks:
        return "Artifact diff: no textual changes."
    return "\n".join(hunks).rstrip() + "\n"


def format_preview(preview: dict[str, Any], *, limit: int = 200) -> str:
    symbols = {
        "add": "+",
        "modify": "~",
        "delete": "-",
        "replace-dir": "!",
        "unchanged": "=",
    }
    label = "Overlay bundle preview" if preview.get("bundle") else "Artifact preview"
    lines = [f"{label}: {preview.get('artifact', '-')}"]
    if preview.get("bundle_name"):
        lines.append(f"Bundle: {preview.get('bundle_name')}")
    counts = preview.get("counts", {})
    if counts:
        lines.append("Changes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    changes = list(preview.get("changes", []))
    for change in changes[:limit]:
        kind = str(change.get("kind", ""))
        overlay = str(change.get("overlay") or "")
        suffix = f"  [{overlay}]" if overlay else ""
        lines.append(f"{symbols.get(kind, '?')} {kind:11} {change.get('path', '-')}{suffix}")
    if len(changes) > limit:
        lines.append(f"... {len(changes) - limit} more change(s) omitted")
    return "\n".join(lines)


def history_file(repo_root: Path) -> Path:
    return repo_root / HISTORY_PATH


def history_entries_dir(repo_root: Path) -> Path:
    return repo_root / HISTORY_ENTRIES_PATH


def _history_entry_files(repo_root: Path) -> list[Path]:
    directory = history_entries_dir(repo_root)
    return sorted(directory.glob("*.json")) if directory.exists() else []


def load_history(repo_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    # New append-only records are authoritative and safe for interrupted or repeated runs.
    for entry_path in _history_entry_files(repo_root):
        try:
            item = json.loads(entry_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        key = str(item.get("history_id") or item.get("sha256") or entry_path.name)
        if key in seen:
            continue
        seen.add(key)
        entries.append(item)

    # Import legacy/index-only entries for backward compatibility.
    path = history_file(repo_root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("artifacts", []) if isinstance(data, dict) else data
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("history_id") or item.get("sha256") or json.dumps(item, sort_keys=True))
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(item)
        except Exception:
            pass
    entries.sort(key=lambda item: str(item.get("applied_at") or ""))
    return entries


def save_history(repo_root: Path, entries: list[dict[str, Any]]) -> Path:
    path = history_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({"schema_version": 2, "artifacts": entries}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def history_lock_file(repo_root: Path) -> Path:
    """Return the external lock used to serialize artifact history writes.

    The lock is runtime state, not repository content. Keeping it below the
    devtool state directory prevents an otherwise clean checkout from gaining
    an untracked ``.devtool/artifacts.lock`` file.
    """
    identity = hashlib.sha256(str(repo_root.expanduser().resolve()).encode("utf-8")).hexdigest()[:24]
    return state_dir() / "artifact-history-locks" / f"{identity}.lock"


def _cleanup_legacy_history_lock(repo_root: Path) -> None:
    legacy = repo_root / ".devtool" / "artifacts.lock"
    if not legacy.exists() or not legacy.is_file():
        return

    # Never remove a deliberately tracked repository file. Old devtool versions
    # created this as untracked runtime state, which is safe to remove after the
    # external lock has serialized the current write.
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", ".devtool/artifacts.lock"],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        tracked = None
    if tracked is None or tracked.returncode != 0:
        legacy.unlink(missing_ok=True)


def append_history(repo_root: Path, entry: dict[str, Any]) -> Path:
    directory = history_entries_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = history_lock_file(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            history_id = str(entry.get("history_id") or uuid.uuid4().hex)
            entry = dict(entry)
            entry["history_id"] = history_id
            stamp = re.sub(r"[^0-9A-Za-z]+", "", str(entry.get("applied_at") or ""))[:20] or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path = directory / f"{stamp}-{history_id}.json"
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            save_history(repo_root, load_history(repo_root))
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    _cleanup_legacy_history_lock(repo_root)
    return path


def make_history_entry(
    repo_root: Path,
    artifact: str | Path,
    *,
    artifact_type: str,
    status: str,
    validation_status: str = "not-run",
    commit_message: str = "",
    commit_hash: str = "",
    preview: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = resolve_artifact_path(artifact)
    digest = sha256_file(path) if path.exists() else ""
    return {
        "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact": str(path),
        "artifact_name": path.name,
        "type": artifact_type,
        "sha256": digest,
        "status": status,
        "validation_status": validation_status,
        "commit_message": commit_message,
        "commit_hash": commit_hash,
        "file_count": int((preview or {}).get("files", 0)),
        "change_counts": (preview or {}).get("counts", {}),
        "manifest": manifest or {},
    }


def latest_commit_hash(repo_root: Path) -> str:
    import subprocess

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""
