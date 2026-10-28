from __future__ import annotations

import contextlib
from . import host_file_lock as fcntl
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .console_utils import resolve_cli_path
from .logstore import state_dir
from .artifact_base_contract import path_source_sha256, validate_base_contract, validate_base_contract_shape

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
_VALIDATION_TASK_KEYS = ("tasks", "gradle_tasks", "test_tasks", "test_task", "task")
_FINALIZE_PHASE_ALIASES = {
    "before_validation": "before_validation",
    "before-validation": "before_validation",
    "before_validator": "before_validation",
    "before-validator": "before_validation",
    "pre_validation": "before_validation",
    "pre-validation": "before_validation",
    "after_validation": "after_validation",
    "after-validation": "after_validation",
    "post_validation": "after_validation",
    "post-validation": "after_validation",
    "after_validator": "after_validation",
    "after-validator": "after_validation",
    "before_commit": "before_commit",
    "before-commit": "before_commit",
    "pre_commit": "before_commit",
    "pre-commit": "before_commit",
    "after_commit": "after_commit",
    "after-commit": "after_commit",
    "post_commit": "after_commit",
    "post-commit": "after_commit",
}

_HOOK_CAPABILITIES = {
    "repo_read", "repo_write", "artifact_read", "artifact_private_copy",
    "subprocess", "network", "environment", "secrets",
}
_HOOK_PROTOCOLS = {"legacy-env", "json-v1"}


_PREFLIGHT_PHASE_ALIASES = {
    "before": "before_extract",
    "before_extract": "before_extract",
    "before-extract": "before_extract",
    "pre_extract": "before_extract",
    "pre-extract": "before_extract",
    "after": "after_extract",
    "after_extract": "after_extract",
    "after-extract": "after_extract",
    "post_extract": "after_extract",
    "post-extract": "after_extract",
}


def _durable_atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace *path* and make the replacement durable on POSIX.

    ``os.replace`` gives us atomic name replacement, but atomicity alone does
    not satisfy the artifact-history durability contract.  Flush and fsync the
    temporary file before the rename, then fsync the containing directory so a
    completed operation record survives a crash after the rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(path.parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def _explicit_artifact_type(manifest: dict[str, Any]) -> str:
    """Return the first explicit artifact-type marker, if one is declared."""
    for key in ("kind", "type", "artifact_type"):
        if key in manifest:
            value = str(manifest.get(key) or "").strip().lower()
            if value:
                return value
    return ""


def _normalize_validation_tasks(value: Any) -> list[str]:
    """Normalize overlay-declared validation tasks.

    The canonical manifest field is ``validation.tasks``.  A few aliases are
    accepted for compatibility with CLI terms and Android/Gradle wording.
    Strings may be comma-separated; lists are flattened only one level by
    reusing the string parser for each item.
    """

    if value is None:
        return []
    if isinstance(value, str):
        return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if isinstance(value, (list, tuple)):
        tasks: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("validation.tasks entries must be strings.")
            tasks.extend(_normalize_validation_tasks(item))
        return list(dict.fromkeys(tasks))
    raise ValueError("validation.tasks must be a string or list of strings.")


def _normalize_validation_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"validation.{field} entries must be strings.")
            text = item.strip()
            if text:
                values.append(text)
        return list(dict.fromkeys(values))
    raise ValueError(f"validation.{field} must be a string or list of strings.")


def _validation_tasks_from_mapping(mapping: dict[str, Any]) -> list[str]:
    for key in _VALIDATION_TASK_KEYS:
        if key in mapping:
            return _normalize_validation_tasks(mapping.get(key))
    return []


def validation_tasks_declaration(mapping: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return task-field presence separately from its normalized value."""
    for key in _VALIDATION_TASK_KEYS:
        if key in mapping:
            return True, _normalize_validation_tasks(mapping.get(key))
    return False, []


def artifact_validation_task_declaration(manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    raw = manifest.get("validation")
    if not isinstance(raw, dict):
        return False, []
    return validation_tasks_declaration(raw)


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
            # A symlinked duplicate is not a valid consumed source. Resolving
            # it here would erase the original leaf identity before cleanup.
            if entry.is_symlink() or not entry.is_file():
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
    path = resolve_cli_path(str(artifact), preserve_leaf=True)
    if path.is_symlink():
        raise ValueError("artifact source symlinks are not accepted")
    chosen = _select_artifact_variant(path, _artifact_family_variants(path))
    if chosen.is_symlink():
        raise ValueError("selected artifact variant is a symlink")
    return chosen


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
    """Return True for archive-side devtool metadata protected by default.

    Root-level manifests and root-level ``.devtool-delete`` are metadata.  Files
    merely named ``.devtool-delete`` in normal subdirectories are payload, not
    destructive manifests.  The reserved metadata directories remain protected
    at every depth under their root names.
    """
    try:
        path = _safe_rel(name)
    except RuntimeError:
        return False
    parts = path.parts
    if not parts:
        return False
    if len(parts) == 1 and parts[0] in ARTIFACT_METADATA_NAMES:
        return True
    return parts[0] in {ARTIFACT_METADATA_DIRECTORY, ".devtool"}



def is_allowed_artifact_metadata_payload_member(name: str) -> bool:
    """Return True for repo-owned validator assets that overlays may update.

    Most members under ``.devtool-artifact`` are artifact metadata and should
    never be copied into the target repository. Some repositories intentionally
    keep their own validation entrypoint under ``.devtool-artifact`` though, so
    inventory-declared overlays may touch the validator script and the dedicated
    validation directory without opening the rest of the artifact metadata
    namespace.
    """
    try:
        rel = _safe_rel(name)
    except RuntimeError:
        return False
    parts = rel.parts
    if parts == (ARTIFACT_METADATA_DIRECTORY, "validate.sh"):
        return True
    return len(parts) >= 3 and parts[0] == ARTIFACT_METADATA_DIRECTORY and parts[1] == "validation"


def artifact_inventory_metadata_payload_paths(manifest: dict[str, Any]) -> set[str]:
    """Return inventory-declared metadata paths that should be treated as payload."""
    allowed: set[str] = set()
    for entry in artifact_inventory_entries(manifest):
        path = str(entry.get("path") or "").strip()
        action = str(entry.get("action") or "").strip().lower()
        if action == "delete":
            continue
        if path and is_allowed_artifact_metadata_payload_member(path):
            allowed.add(path)
    return allowed


def _is_skipped_artifact_payload_member(
    name: str,
    *,
    allowed_metadata_payloads: set[str] | None = None,
) -> bool:
    if is_ignored_artifact_payload_member(name):
        return True
    if is_artifact_metadata_member(name):
        return not (
            allowed_metadata_payloads
            and name in allowed_metadata_payloads
            and is_allowed_artifact_metadata_payload_member(name)
        )
    return False

def is_ignored_artifact_payload_member(name: str) -> bool:
    """Return True for generated cache payloads that overlays should ignore."""
    path = Path(name)
    parts = path.parts
    if not parts:
        return False
    return (
        ".pytest_cache" in parts
        or "__pycache__" in parts
        or path.suffix.lower() in {".pyc", ".pyo"}
    )


def is_skipped_artifact_payload_member(name: str) -> bool:
    """Return True for archive members excluded from overlay payload handling."""
    return _is_skipped_artifact_payload_member(name)


@dataclass(slots=True)
class ArchiveMember:
    name: str
    is_dir: bool = False
    data: bytes | None = None
    mode: int | None = None


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
    validation_tasks: tuple[str, ...] = ()
    validation_tasks_declared: bool = False
    sha256: str = ""
    size: int | None = None
    index: int = 0


@dataclass(frozen=True, slots=True)
class MaterializedBundleOverlay:
    path: Path
    bundle_path: str
    id: str = ""
    target: str = ""
    label: str = ""
    required: bool = True
    validation_tasks: tuple[str, ...] = ()
    validation_tasks_declared: bool = False
    sha256: str = ""
    size: int | None = None
    index: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactManifestResolution:
    manifest: dict[str, Any]
    present: bool
    valid: bool
    source: str = ""
    error: str = ""
    candidates: tuple[str, ...] = ()


def sha256_file(path: Path) -> str:
    """Hash artifact/source bytes without metadata-based integrity caching.

    Source and payload digests are used as security/integrity evidence.  A
    caller can rewrite a file and restore size/mtime (and retain its inode), so
    metadata is not a sound cache key for these checks.  Always hash the bytes.
    """
    resolved = path.resolve()
    h = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value.strip()))


def _safe_rel(name: str) -> Path:
    """Return a canonical repository-relative archive path or fail closed.

    Archive names are POSIX-style regardless of host OS.  Normalize harmless
    ``.`` segments, but reject traversal, absolute paths, Windows drive prefixes,
    backslashes, NUL bytes, and the repository Git control directory.  Returning
    a canonical path lets validation detect duplicate spellings like
    ``./a.txt`` and ``a.txt`` before extraction mutates the working tree.
    """
    raw = str(name or "").strip()
    if not raw:
        raise RuntimeError("Unsafe archive path: empty path")
    if "\x00" in raw or "\\" in raw:
        raise RuntimeError(f"Unsafe archive path: {name}")
    rel = PurePosixPath(raw)
    if rel.is_absolute():
        raise RuntimeError(f"Unsafe archive path: {name}")
    parts: list[str] = []
    for part in rel.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise RuntimeError(f"Unsafe archive path: {name}")
        if not parts and (part.endswith(":") or re.fullmatch(r"[A-Za-z]:.*", part)):
            raise RuntimeError(f"Unsafe archive path: {name}")
        parts.append(part)
    if not parts:
        raise RuntimeError("Unsafe archive path: empty path")
    if parts[0] == ".git":
        raise RuntimeError(f"Unsafe archive path: {name}")
    return Path(*parts)


def _archive_member_is_root_directory(name: str, *, is_dir: bool) -> bool:
    """Return True for harmless archive root-directory marker entries.

    Some tar creators include a directory member named ``.`` or ``./`` when an
    archive is created from a directory root.  That member does not map to a
    repository payload path and should be ignored, while empty or root-named
    non-directory members must still fail closed.
    """
    if not is_dir:
        return False
    raw = str(name or "").strip()
    if "\x00" in raw or "\\" in raw:
        return False
    if not raw:
        return True
    rel = PurePosixPath(raw)
    if rel.is_absolute():
        return False
    return all(part in {"", "."} for part in rel.parts)


def _canonical_archive_path(name: str) -> str:
    return _safe_rel(name).as_posix()


def _manifest_repository(manifest: dict[str, Any]) -> dict[str, Any]:
    repository = manifest.get("repository")
    if isinstance(repository, dict):
        return repository
    legacy = str(manifest.get("repo") or manifest.get("repo_name") or manifest.get("expected_repo") or "").strip()
    return {"name": legacy} if legacy else {}


def artifact_target(manifest: dict[str, Any]) -> str:
    """Return the preferred validation target from a v2 or legacy manifest."""
    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    return str(manifest.get("target") or manifest.get("validate_target") or validation.get("target") or "").strip()


def artifact_solution(manifest: dict[str, Any]) -> str:
    return str(manifest.get("solution") or manifest.get("expected_solution") or "").strip()


def artifact_commit_policy(manifest: dict[str, Any]) -> tuple[bool, str, str]:
    """Return enabled, message, and strategy for artifact-driven commits.

    Devtool v2 overlays should declare commits under ``apply.commit``.  Older
    hand-written overlays used either a top-level ``commit`` object, a ``git``
    object, or the legacy ``commit_message`` string.  Keep accepting those
    legacy fields so no-validation replay of older overlay series still
    preserves the intended per-overlay Git history.
    """

    def _commit_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _message_from(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    def _policy_from(mapping: dict[str, Any], *, message_keys: tuple[str, ...] = ("message",)) -> tuple[bool | None, str, str] | None:
        message = ""
        for key in message_keys:
            message = _message_from(mapping.get(key))
            if message:
                break
        has_enabled = "enabled" in mapping or "commit" in mapping
        if "enabled" in mapping:
            enabled_value = mapping.get("enabled") is True
        elif "commit" in mapping:
            enabled_value = mapping.get("commit") is True
        else:
            enabled_value = None
        strategy = str(mapping.get("strategy") or "single").strip().lower() or "single"
        if not message and not has_enabled and "strategy" not in mapping:
            return None
        return enabled_value, message, strategy

    apply = _commit_dict(manifest.get("apply"))
    candidates: list[tuple[bool | None, str, str]] = []

    apply_commit = _commit_dict(apply.get("commit"))
    policy = _policy_from(apply_commit)
    if policy is not None:
        candidates.append(policy)

    top_commit = manifest.get("commit")
    if isinstance(top_commit, dict):
        policy = _policy_from(top_commit)
        if policy is not None:
            candidates.append(policy)
    elif isinstance(top_commit, str):
        candidates.append((None, top_commit.strip(), "single"))

    git_commit = _commit_dict(manifest.get("git"))
    policy = _policy_from(git_commit, message_keys=("message", "commit_message"))
    if policy is not None:
        candidates.append(policy)

    commit_message = _message_from(manifest.get("commit_message"))
    if commit_message:
        candidates.append((None, commit_message, "single"))

    if not candidates:
        return False, "", "single"

    enabled_value, message, strategy = candidates[0]
    # A real commit message is itself an affirmative commit request. Explicit
    # false remains a strong opt-out, and CLI --no-commit still wins later.
    enabled = bool(message) if enabled_value is None else enabled_value
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
    # ``failure_action`` is the canonical failure/recovery authority. Preserve
    # the legacy boolean as a compatibility projection, never as a second
    # independent decision that can contradict the selected action.
    if "failure_action" in raw:
        action = str(raw.get("failure_action") or "").strip().lower()
        policy["failure_action"] = action
        policy["rollback_on_failure"] = action == "rollback"
    else:
        rollback = raw.get("rollback_on_failure") is True
        policy["rollback_on_failure"] = rollback
        policy["failure_action"] = "rollback" if rollback else "pause"
    policy.setdefault("allow_deferred", True)
    policy.setdefault("minimum_phase", "finalize")
    minimum_phase = str(policy.get("minimum_phase") or "finalize").strip().lower().replace("_", "-")
    # Legacy manifests may still name the removed artifact-check checkpoint.
    # Repository validation no longer inspects the already-accepted archive, so
    # the closest meaningful checkpoint is package.
    policy["minimum_phase"] = "package" if minimum_phase == "artifact-check" else minimum_phase
    policy.setdefault("test_profile", "")
    policy.setdefault("execution_environment", "")
    try:
        tasks = _validation_tasks_from_mapping(policy)
    except ValueError:
        # Keep policy accessors total; static validation reports the precise
        # task-shape error without letting helper accessors crash callers.
        tasks = []
    if tasks:
        policy["tasks"] = tasks
    for key in ("pytest_tests", "pytest_markers", "pytest_args"):
        if key in policy:
            policy[key] = _normalize_validation_string_list(policy.get(key), key)
    if "pytest_k" in policy:
        policy["pytest_k"] = str(policy.get("pytest_k") or "").strip()
    if "pytest_include_defaults" in policy:
        policy["pytest_include_defaults"] = bool(policy.get("pytest_include_defaults"))
    return policy


def artifact_validation_pytest_selection(manifest: dict[str, Any]) -> dict[str, Any]:
    policy = artifact_validation_policy(manifest)
    payload: dict[str, Any] = {}
    for key in ("pytest_tests", "pytest_markers", "pytest_k", "pytest_args", "pytest_include_defaults"):
        if key in policy:
            payload[key] = policy[key]
    return payload


def artifact_validation_tasks(manifest: dict[str, Any]) -> list[str]:
    return list(artifact_validation_policy(manifest).get("tasks") or [])


def artifact_validation_script(manifest: dict[str, Any]) -> dict[str, Any]:
    validation = artifact_validation_policy(manifest)
    script = validation.get("script")
    return dict(script) if isinstance(script, dict) else {}


def _artifact_hook_policy(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "validation":
        return artifact_validation_policy(manifest)
    raw = manifest.get(kind)
    return dict(raw) if isinstance(raw, dict) else {}


def _artifact_hook_write_paths(manifest: dict[str, Any], kind: str) -> list[str]:
    policy = _artifact_hook_policy(manifest, kind)
    raw = policy.get("write_paths")
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError(f"{kind}.write_paths must be a string or list of strings.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{kind}.write_paths entries must be strings.")
        text = item.strip().replace("\\", "/")
        if not text:
            raise ValueError(f"{kind}.write_paths entries must not be empty.")
        if text == ".git" or text.startswith(".git/"):
            raise ValueError(f"{kind}.write_paths may not target Git metadata: {text}")
        try:
            rel = _safe_rel(text)
        except RuntimeError as exc:
            raise ValueError(f"Unsafe {kind}.write_paths path: {text}") from exc
        if rel.parts and rel.parts[0] == ".git":
            raise ValueError(f"{kind}.write_paths may not target Git metadata: {text}")
        value = rel.as_posix()
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def artifact_hook_capabilities(manifest: dict[str, Any], kind: str) -> list[str]:
    """Return normalized declared capabilities for an embedded artifact hook."""
    policy = _artifact_hook_policy(manifest, kind)
    raw = policy.get("capabilities")
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError(f"{kind}.capabilities must be a string or list of strings.")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{kind}.capabilities entries must be strings.")
        value = item.strip().lower().replace("-", "_")
        if not value:
            raise ValueError(f"{kind}.capabilities entries must not be empty.")
        if value not in _HOOK_CAPABILITIES:
            raise ValueError(
                f"Unsupported {kind}.capabilities capability {item!r}; expected one of: "
                + ", ".join(sorted(_HOOK_CAPABILITIES)) + "."
            )
        if value not in normalized:
            normalized.append(value)
    return normalized


def artifact_hook_protocol(manifest: dict[str, Any], kind: str, *, default: str = "legacy-env") -> str:
    policy = _artifact_hook_policy(manifest, kind)
    script = policy.get("script") if isinstance(policy.get("script"), dict) else {}
    value = str(script.get("protocol") or default).strip().lower().replace("_", "-")
    return value


def artifact_validation_write_paths(manifest: dict[str, Any]) -> list[str]:
    return _artifact_hook_write_paths(manifest, "validation")


def artifact_finalize_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the optional post-apply finalization hook declaration."""
    return _artifact_hook_policy(manifest, "finalize")


def artifact_finalize_phases(manifest: dict[str, Any]) -> list[str]:
    """Return normalized finalization phases in deterministic order."""
    policy = artifact_finalize_policy(manifest)
    if not policy:
        return []
    raw = policy.get("phases", policy.get("phase", "before_validation"))
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError("finalize.phases must be a string or list of strings.")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError("finalize.phases entries must be strings.")
        key = item.strip().lower()
        phase = _FINALIZE_PHASE_ALIASES.get(key, key)
        if phase not in {"before_validation", "after_validation", "before_commit", "after_commit"}:
            raise ValueError(f"Unsupported finalize phase: {item!r}.")
        if phase not in normalized:
            normalized.append(phase)
    return normalized


def artifact_finalize_script(manifest: dict[str, Any]) -> dict[str, Any]:
    policy = artifact_finalize_policy(manifest)
    script = policy.get("script")
    return dict(script) if isinstance(script, dict) else {}


def artifact_finalize_write_paths(manifest: dict[str, Any]) -> list[str]:
    return _artifact_hook_write_paths(manifest, "finalize")


def artifact_preflight_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the optional artifact preflight declaration."""
    return _artifact_hook_policy(manifest, "preflight")


def artifact_preflight_phases(manifest: dict[str, Any]) -> list[str]:
    """Return normalized extraction phases; legacy ``phase`` remains supported."""
    policy = artifact_preflight_policy(manifest)
    if not policy:
        return []
    raw = policy.get("phases", policy.get("phase", "before_extract"))
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list):
        raise ValueError("preflight.phases must be a string or list of strings.")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError("preflight.phases entries must be strings.")
        key = item.strip().lower()
        phase = _PREFLIGHT_PHASE_ALIASES.get(key, key)
        if phase not in {"before_extract", "after_extract"}:
            raise ValueError(f"Unsupported preflight phase: {item!r}.")
        if phase not in normalized:
            normalized.append(phase)
    return normalized


def artifact_preflight_phase(manifest: dict[str, Any]) -> str:
    """Backward-compatible singular phase accessor."""
    phases = artifact_preflight_phases(manifest)
    return phases[0] if phases else ""


def artifact_preflight_script(manifest: dict[str, Any]) -> dict[str, Any]:
    policy = artifact_preflight_policy(manifest)
    script = policy.get("script")
    return dict(script) if isinstance(script, dict) else {}


def artifact_preflight_write_paths(manifest: dict[str, Any]) -> list[str]:
    return _artifact_hook_write_paths(manifest, "preflight")


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


def _validate_inventory_shape(manifest: dict[str, Any], messages: list[str]) -> bool:
    """Validate declared inventory structure before consuming normalized entries."""
    if "inventory" not in manifest:
        return True
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict):
        messages.append("inventory must be an object.")
        return False
    if "entries" not in inventory:
        messages.append("inventory.entries is required when inventory is declared.")
        return False
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        messages.append("inventory.entries must be a list.")
        return False
    bad = [index for index, item in enumerate(entries, 1) if not isinstance(item, dict)]
    if bad:
        messages.append("inventory.entries must contain only objects (invalid entries: " + ", ".join(map(str, bad[:10])) + ").")
        return False
    return True


def _schema_version(manifest: dict[str, Any], messages: list[str], *, bundle: bool = False) -> int | None:
    key_value = manifest.get("schema_version", manifest.get("version", 1) if bundle else 1)
    if not isinstance(key_value, int) or isinstance(key_value, bool):
        messages.append(("Bundle" if bundle else "Artifact") + " schema_version must be a positive integer.")
        return None
    if key_value <= 0:
        messages.append(("Bundle" if bundle else "Artifact") + " schema_version must be greater than zero.")
        return None
    if key_value > 2:
        messages.append(
            f"Unsupported {'bundle' if bundle else 'artifact'} schema_version {key_value}; maximum supported version is 2."
        )
        return None
    return key_value


def _validate_validation_policy(manifest: dict[str, Any], messages: list[str], *, bundle: bool) -> bool:
    raw = manifest.get("validation")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        messages.append("validation must be an object.")
        return False
    ok = True
    script_value = raw.get("script")
    if script_value is not None and not isinstance(script_value, dict):
        messages.append("validation.script must be an object.")
        ok = False
    script = script_value if isinstance(script_value, dict) else {}
    mode = str(raw.get("mode") or "").strip().lower()
    for boolean_key in ("required", "restore", "build", "test", "rollback_on_failure", "allow_deferred"):
        if boolean_key in raw and not isinstance(raw.get(boolean_key), bool):
            messages.append(f"validation.{boolean_key} must be a boolean.")
            ok = False
    tasks_declared = False
    declared_tasks: list[str] = []
    try:
        tasks_declared, declared_tasks = validation_tasks_declaration(raw)
    except ValueError:
        # The detailed task-shape error is emitted by the validation loop below.
        pass
    phases = [raw.get(name, True) if isinstance(raw.get(name, True), bool) else False for name in ("restore", "build", "test")]
    required = mode == "required" if mode else (raw.get("required", False) if isinstance(raw.get("required", False), bool) else False)
    raw_tests = raw.get("tests")
    has_declared_validation_tests = isinstance(raw_tests, list) and bool(raw_tests)
    raw_refs = raw.get("refs")
    has_declared_validation_refs = isinstance(raw_refs, list) and bool(raw_refs)
    if required and not any(phases) and not script and not (tasks_declared and declared_tasks) and not has_declared_validation_tests and not has_declared_validation_refs:
        ok = False
        messages.append(
            "Required validation must enable restore, build, test, explicit validation tasks, "
            "validation.tests, validation.refs, or an artifact script."
        )

    pytest_selection_declared = any(
        bool(raw.get(key))
        for key in ("pytest_tests", "pytest_markers", "pytest_k", "pytest_args", "test_profile")
    ) or raw.get("pytest_include_defaults") is True
    if raw.get("test") is False and pytest_selection_declared and not (tasks_declared and declared_tasks):
        ok = False
        messages.append(
            "Pytest/test-profile selection cannot be declared when validation.test=false unless "
            "a non-empty validation.tasks plan owns test execution."
        )
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
        if mode not in {"required", "optional", "normal", "disabled"}:
            ok = False
            messages.append("validation.mode must be one of: required, optional, normal, disabled.")
    if "minimum_phase" in raw:
        minimum_phase = str(raw.get("minimum_phase") or "").strip().lower().replace("_", "-")
        allowed_phases = {
            "static", "preflight", "dependencies", "compile", "resources",
            "unit-test", "package", "artifact-check", "lint", "instrumentation", "finalize", "final",
        }
        if minimum_phase not in allowed_phases:
            ok = False
            messages.append(f"Unsupported validation.minimum_phase: {minimum_phase!r}.")
    if "maximum_warnings" in raw:
        maximum_warnings = raw.get("maximum_warnings")
        if not isinstance(maximum_warnings, int) or isinstance(maximum_warnings, bool) or maximum_warnings < 0:
            ok = False
            messages.append("validation.maximum_warnings must be a non-negative integer.")
    if "failure_action" in raw:
        action = str(raw.get("failure_action") or "").strip().lower()
        if action not in {"pause", "rollback", "keep", "prompt"}:
            ok = False
            messages.append("validation.failure_action must be one of: pause, rollback, keep, prompt.")
    if "test_profile" in raw and not isinstance(raw.get("test_profile"), str):
        ok = False
        messages.append("validation.test_profile must be a string.")
    for key in ("pytest_tests", "pytest_markers", "pytest_args"):
        if key not in raw:
            continue
        try:
            _normalize_validation_string_list(raw.get(key), key)
        except ValueError as exc:
            ok = False
            messages.append(str(exc))
    if "pytest_k" in raw and not isinstance(raw.get("pytest_k"), str):
        ok = False
        messages.append("validation.pytest_k must be a string.")
    if "pytest_include_defaults" in raw and not isinstance(raw.get("pytest_include_defaults"), bool):
        ok = False
        messages.append("validation.pytest_include_defaults must be a boolean.")
    for key in _VALIDATION_TASK_KEYS:
        if key not in raw:
            continue
        try:
            _normalize_validation_tasks(raw.get(key))
        except ValueError as exc:
            ok = False
            messages.append(str(exc).replace("validation.tasks", f"validation.{key}"))
    if "execution_environment" in raw:
        environment = str(raw.get("execution_environment") or "").strip().lower()
        if environment and environment not in {"auto", "native-termux", "chroot", "desktop-linux", "android-device", "external-device"}:
            ok = False
            messages.append(f"Unsupported validation.execution_environment: {environment!r}.")
    try:
        artifact_hook_capabilities(manifest, "validation")
        artifact_validation_write_paths(manifest)
    except ValueError as exc:
        ok = False
        messages.append(str(exc))
    if script:
        protocol = str(script.get("protocol") or "legacy-env").strip().lower().replace("_", "-")
        if protocol not in _HOOK_PROTOCOLS:
            ok = False
            messages.append("validation.script.protocol must be one of: legacy-env, json-v1.")
    try:
        artifact_validation_tests(manifest)
        artifact_validation_refs(manifest)
    except ValueError as exc:
        ok = False
        messages.append(str(exc))
    if bundle:
        strategy = str(raw.get("strategy") or "per-overlay").strip().lower()
        if strategy not in {"per-overlay", "after-all", "both"}:
            ok = False
            messages.append(f"Unsupported bundle validation strategy: {strategy!r}.")
    return ok


def _validate_preflight_policy(manifest: dict[str, Any], messages: list[str]) -> bool:
    raw = manifest.get("preflight")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        messages.append("preflight must be an object.")
        return False

    ok = True
    try:
        artifact_preflight_phases(manifest)
    except ValueError as exc:
        ok = False
        if "phase" in raw and "phases" not in raw:
            messages.append("preflight.phase must be one of: before_extract, after_extract.")
        else:
            messages.append(str(exc))
    try:
        artifact_hook_capabilities(manifest, "preflight")
    except ValueError as exc:
        ok = False
        messages.append(str(exc))

    script_value = raw.get("script")
    if not isinstance(script_value, dict):
        ok = False
        messages.append("preflight.script must be an object.")
        script: dict[str, Any] = {}
    else:
        script = script_value

    try:
        artifact_preflight_write_paths(manifest)
    except (RuntimeError, ValueError) as exc:
        ok = False
        messages.append(str(exc))

    if script:
        script_path = str(script.get("path") or "").strip()
        if not script_path:
            ok = False
            messages.append("preflight.script.path is required.")
        else:
            try:
                rel = _safe_rel(script_path)
                if not rel.parts or rel.parts[0] != ARTIFACT_METADATA_DIRECTORY:
                    raise RuntimeError("Preflight scripts must live under .devtool-artifact/.")
            except RuntimeError as exc:
                ok = False
                if script_path.startswith("../") or "/../" in script_path or Path(script_path).is_absolute():
                    messages.append("Preflight scripts must live under .devtool-artifact/.")
                else:
                    messages.append(str(exc))

        runner = str(script.get("runner") or "bash").strip()
        if runner not in {"bash", "sh", "python3"}:
            ok = False
            messages.append(f"Unsupported preflight script runner: {runner!r}.")

        timeout = script.get("timeout_seconds", 300)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            ok = False
            messages.append("preflight.script.timeout_seconds must be a positive integer.")

        sha = str(script.get("sha256") or "").strip().lower()
        if not sha:
            ok = False
            messages.append("preflight.script.sha256 is required.")
        elif not _is_sha256(sha):
            ok = False
            messages.append("preflight.script.sha256 is not a valid SHA-256.")
        protocol = str(script.get("protocol") or "legacy-env").strip().lower().replace("_", "-")
        if protocol not in _HOOK_PROTOCOLS:
            ok = False
            messages.append("preflight.script.protocol must be one of: legacy-env, json-v1.")
    return ok


def _validate_finalize_policy(manifest: dict[str, Any], messages: list[str]) -> bool:
    raw = manifest.get("finalize")
    if raw is None:
        return True
    if not isinstance(raw, dict):
        messages.append("finalize must be an object.")
        return False
    ok = True
    try:
        artifact_finalize_phases(manifest)
    except ValueError as exc:
        ok = False
        messages.append(str(exc))
    try:
        artifact_finalize_write_paths(manifest)
        artifact_hook_capabilities(manifest, "finalize")
    except (RuntimeError, ValueError) as exc:
        ok = False
        messages.append(str(exc))
    script_value = raw.get("script")
    if not isinstance(script_value, dict):
        messages.append("finalize.script must be an object.")
        return False
    script = script_value
    script_path = str(script.get("path") or "").strip()
    if not script_path:
        ok = False
        messages.append("finalize.script.path is required.")
    else:
        try:
            rel = _safe_rel(script_path)
            if not rel.parts or rel.parts[0] != ARTIFACT_METADATA_DIRECTORY:
                raise RuntimeError("Finalize scripts must live under .devtool-artifact/.")
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
    runner = str(script.get("runner") or "python3").strip()
    if runner not in {"bash", "sh", "python3"}:
        ok = False
        messages.append(f"Unsupported finalize script runner: {runner!r}.")
    timeout = script.get("timeout_seconds", 300)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        ok = False
        messages.append("finalize.script.timeout_seconds must be a positive integer.")
    sha = str(script.get("sha256") or "").strip().lower()
    if not sha:
        ok = False
        messages.append("finalize.script.sha256 is required.")
    elif not _is_sha256(sha):
        ok = False
        messages.append("finalize.script.sha256 is not a valid SHA-256.")
    protocol = str(script.get("protocol") or "json-v1").strip().lower().replace("_", "-")
    if protocol != "json-v1":
        ok = False
        messages.append("finalize.script.protocol must be 'json-v1'.")
    return ok


def _validate_embedded_script_member(
    members: Iterable[ArchiveMember],
    spec: dict[str, Any],
    messages: list[str],
    *,
    label: str,
    ignore_checksum: bool,
) -> bool:
    """Validate an embedded metadata script against the archive payload."""
    if not spec:
        return True
    ok = True
    script_path = str(spec.get("path") or "").strip()
    if not script_path:
        return False
    member = next((item for item in members if item.name == script_path and not item.is_dir), None)
    if member is None:
        messages.append(f"{label} script is missing from archive: {script_path}")
        return False
    if not ignore_checksum:
        expected = str(spec.get("sha256") or "").strip().lower()
        if expected and member.data is not None:
            actual = hashlib.sha256(member.data).hexdigest().lower()
            if actual != expected:
                ok = False
                messages.append(f"{label} script SHA-256 mismatch.")
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


def _member_collision_guard(
    seen: dict[str, bool],
    name: str,
    *,
    is_dir: bool,
) -> None:
    if name in seen:
        if not (seen[name] and is_dir):
            raise RuntimeError(f"Duplicate normalized archive path: {name}")
        return
    for existing, existing_is_dir in seen.items():
        if name.startswith(existing + "/") and not existing_is_dir:
            raise RuntimeError(f"Archive contains file/child path collision: {existing} and {name}")
        if existing.startswith(name + "/") and not is_dir:
            raise RuntimeError(f"Archive contains file/child path collision: {name} and {existing}")
    seen[name] = is_dir


def list_archive_members(artifact: str | Path, *, include_data: bool = False) -> list[ArchiveMember]:
    path = resolve_artifact_path(artifact)
    members: list[ArchiveMember] = []
    seen: dict[str, bool] = {}
    suffix = archive_suffix(path)
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                is_dir = info.is_dir()
                raw_name = info.filename.rstrip("/")
                if _archive_member_is_root_directory(raw_name, is_dir=is_dir):
                    continue
                normalized = _canonical_archive_path(raw_name)
                mode_type = (info.external_attr >> 16) & 0o170000
                if mode_type == 0o120000:
                    raise RuntimeError(f"Unsafe zip symlink member: {info.filename}")
                _member_collision_guard(seen, normalized, is_dir=is_dir)
                data = None if is_dir or not include_data else zf.read(info.filename)
                mode = (info.external_attr >> 16) & 0o7777
                members.append(ArchiveMember(normalized, is_dir, data, mode or None))
        return members
    if suffix and suffix != ".zip":
        with open_tar_archive(path) as tf:
            for member in tf.getmembers():
                is_dir = member.isdir()
                raw_name = member.name.rstrip("/")
                if _archive_member_is_root_directory(raw_name, is_dir=is_dir):
                    continue
                normalized = _canonical_archive_path(raw_name)
                if member.islnk() or member.issym() or member.isdev():
                    raise RuntimeError(f"Unsafe tar member: {member.name}")
                if not is_dir and not member.isfile():
                    raise RuntimeError(f"Unsupported tar member type: {member.name}")
                _member_collision_guard(seen, normalized, is_dir=is_dir)
                data = None
                if member.isfile() and include_data:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        with fh:
                            data = fh.read()
                members.append(ArchiveMember(normalized, is_dir, data, member.mode & 0o7777))
        return members
    raise RuntimeError(f"Unsupported artifact type: {path}")



def _archive_file_data(artifact: str | Path) -> dict[str, bytes]:
    data: dict[str, bytes] = {}
    for member in list_archive_members(artifact, include_data=True):
        if not member.is_dir and member.data is not None:
            data[member.name] = member.data
    return data


def resolve_artifact_manifest(artifact: str | Path) -> ArtifactManifestResolution:
    """Resolve exactly one declared manifest, failing closed on ambiguity/malformed data.

    Sidecars and embedded manifests are intentionally treated as one namespace.
    A producer must not be able to make inspection see one declaration while
    apply/validation sees another.
    """
    artifact_path = resolve_artifact_path(artifact)
    candidates: list[tuple[str, bytes]] = []
    for sidecar in (
        artifact_path.with_name(artifact_path.name + ".devtool.json"),
        artifact_path.with_name(artifact_path.name + ".manifest.json"),
    ):
        if sidecar.exists():
            try:
                candidates.append((str(sidecar), sidecar.read_bytes()))
            except OSError as exc:
                return ArtifactManifestResolution({}, True, False, str(sidecar), f"Cannot read artifact manifest {sidecar}: {exc}", (str(sidecar),))
    try:
        for member in list_archive_members(artifact_path, include_data=True):
            if member.data is None:
                continue
            if member.name in (MANIFEST_NAMES | BUNDLE_MANIFEST_NAMES):
                candidates.append((member.name, member.data))
    except Exception as exc:
        return ArtifactManifestResolution({}, False, False, error=str(exc))

    names = tuple(name for name, _data in candidates)
    if not candidates:
        return ArtifactManifestResolution({}, False, True)
    if len(candidates) != 1:
        return ArtifactManifestResolution(
            {}, True, False, candidates=names,
            error="Artifact declares multiple manifests; exactly one sidecar or embedded manifest is allowed: " + ", ".join(names),
        )
    source, raw = candidates[0]
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return ArtifactManifestResolution({}, True, False, source, f"Invalid JSON in artifact manifest {source}: {exc}", names)
    if not isinstance(data, dict):
        return ArtifactManifestResolution({}, True, False, source, f"Artifact manifest {source} must contain a JSON object.", names)
    return ArtifactManifestResolution(dict(data), True, True, source, candidates=names)


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
            if not member.is_dir and not is_skipped_artifact_payload_member(member.name)
        ]
    except Exception:
        return {}
    if not members or any(not archive_suffix(member.name) for member in members):
        return {}
    # Archive member order is the only declaration legacy wrappers possess;
    # preserve it rather than re-sorting names and changing execution order.
    ordered = [member.name for member in members]
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
    resolution = resolve_artifact_manifest(artifact)
    if not resolution.valid:
        return {}
    if resolution.present:
        data = resolution.manifest
        kind = _explicit_artifact_type(data)
        source_basename = Path(resolution.source.split("!/")[-1]).name
        if kind:
            return data if kind in BUNDLE_KINDS else {}
        if source_basename in BUNDLE_MANIFEST_NAMES or isinstance(data.get("apply_order"), list) or isinstance(data.get("overlays"), list):
            return data
        # An explicit non-bundle manifest is authoritative.  Do not reclassify
        # an overlay merely because its payload happens to contain archives.
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
        validation_tasks: tuple[str, ...] = ()
        validation_tasks_declared = False
        expected_sha = ""
        expected_size = None
    elif isinstance(item, dict):
        name = str(item.get("path") or item.get("artifact") or item.get("overlay") or "").strip()
        overlay_id = str(item.get("id") or Path(name).stem).strip()
        target = str(item.get("target") or item.get("validate_target") or "").strip()
        label = str(item.get("label") or item.get("name") or item.get("description") or "").strip()
        required_value = item.get("required", True)
        if not isinstance(required_value, bool):
            raise RuntimeError(f"Overlay bundle item #{index} required must be a boolean.")
        required = required_value
        def _dependency_list(field: str) -> tuple[str, ...]:
            value = item.get(field, [])
            if not isinstance(value, list):
                raise RuntimeError(f"Overlay bundle item #{index} {field} must be a list of strings.")
            bad = [position for position, dependency in enumerate(value, 1) if not isinstance(dependency, str)]
            if bad:
                raise RuntimeError(f"Overlay bundle item #{index} {field} list must contain only strings.")
            return tuple(dependency.strip() for dependency in value if dependency.strip())

        after = _dependency_list("after")
        requires = _dependency_list("requires")
        entry_validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        validation_tasks_declared, declared_tasks = validation_tasks_declaration(item)
        if not validation_tasks_declared:
            validation_tasks_declared, declared_tasks = validation_tasks_declaration(entry_validation)
        validation_tasks = tuple(declared_tasks)
        expected_sha = str(item.get("sha256") or "").strip().lower()
        expected_size = item.get("size")
        if expected_sha and not _is_sha256(expected_sha):
            raise RuntimeError(f"Overlay bundle item #{index} sha256 is not a valid SHA-256.")
        if expected_size is not None and (not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0):
            raise RuntimeError(f"Overlay bundle item #{index} size must be a non-negative integer.")
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
        validation_tasks=validation_tasks,
        validation_tasks_declared=validation_tasks_declared,
        sha256=expected_sha,
        size=expected_size,
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
        validation_tasks=spec.validation_tasks,
        validation_tasks_declared=spec.validation_tasks_declared,
        sha256=spec.sha256,
        size=spec.size,
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
        if spec.size is not None and len(data) != spec.size:
            raise RuntimeError(f"Overlay bundle member size mismatch: {spec.path}")
        if spec.sha256 and hashlib.sha256(data).hexdigest().lower() != spec.sha256:
            raise RuntimeError(f"Overlay bundle member SHA-256 mismatch: {spec.path}")
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
                validation_tasks=spec.validation_tasks,
                validation_tasks_declared=spec.validation_tasks_declared,
                sha256=spec.sha256,
                size=spec.size,
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
    """Return unsafe duplicate writers, excluding integrity-linked transforms.

    A later inventory ``modify`` whose ``source_sha256`` equals the previous
    writer's output SHA is an intentional sequential transform, not a conflict.
    """
    writers: dict[str, list[str]] = {}
    previous_output: dict[str, str] = {}
    unsafe: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-conflicts-") as tmp:
        _manifest, entries = materialize_overlay_bundle_entries(bundle, tmp)
        for entry in entries:
            name = entry.label or Path(entry.bundle_path).name
            if entry.target:
                name = f"{name} [{entry.target}]"
            manifest = read_artifact_manifest(entry.path)
            inventory = artifact_inventory_entries(manifest)
            if inventory:
                for item in inventory:
                    path = str(item.get("path") or "").strip()
                    action = str(item.get("action") or "").strip().lower()
                    if not path:
                        continue
                    prior = previous_output.get(path, "")
                    source = str(item.get("source_sha256") or "").strip().lower()
                    if path in writers and not (action == "modify" and prior and source == prior):
                        unsafe.add(path)
                    writers.setdefault(path, []).append(name)
                    previous_output[path] = "" if action == "delete" else str(item.get("sha256") or "").strip().lower()
                continue
            try:
                members = list_archive_members(entry.path, include_data=False)
            except Exception:
                continue
            for member in members:
                if member.is_dir or is_skipped_artifact_payload_member(member.name):
                    continue
                if member.name in writers:
                    unsafe.add(member.name)
                writers.setdefault(member.name, []).append(name)
                previous_output[member.name] = ""
    return {path: writers[path] for path in sorted(unsafe)}


def _validate_bundle_inventory_chain(
    repo_root: Path,
    entries: list[MaterializedBundleOverlay],
    messages: list[str],
    *,
    ignore_checksum: bool,
) -> bool:
    """Validate nested inventory source identities in declared execution order."""
    ok = True
    state: dict[str, str | None] = {}
    for entry in entries:
        nested = read_artifact_manifest(entry.path)
        for item in artifact_inventory_entries(nested):
            path = str(item.get("path") or "").strip()
            action = str(item.get("action") or "").strip().lower()
            if not path:
                continue
            try:
                rel = _safe_rel(path)
            except RuntimeError as exc:
                messages.append(f"[{entry.id}] {exc}")
                ok = False
                continue
            if path not in state:
                target = repo_root / rel
                if target.is_file():
                    state[path] = sha256_file(target).lower()
                elif target.exists():
                    state[path] = "<non-file>"
                else:
                    state[path] = None
            current = state[path]
            source = str(item.get("source_sha256") or "").strip().lower()
            if action in {"modify", "delete"}:
                if current is None or current == "<non-file>":
                    messages.append(f"[{entry.id}] Inventory expects existing file for {action}: {path}")
                    ok = False
                elif not ignore_checksum and source and source != current:
                    messages.append(f"[{entry.id}] Source SHA-256 mismatch for {path} in bundle execution order.")
                    ok = False
            elif action == "add" and current is not None:
                conflict_policy = str((nested.get("apply") if isinstance(nested.get("apply"), dict) else {}).get("conflict_policy") or "fail").lower()
                if conflict_policy == "fail":
                    messages.append(f"[{entry.id}] Inventory add conflicts with existing path: {path}")
                    ok = False
            state[path] = None if action == "delete" else str(item.get("sha256") or "").strip().lower()
    return ok


def validate_overlay_bundle(
    repo_root: Path,
    bundle: str | Path,
    *,
    fail_on_conflict: bool = False,
    ignore_checksum: bool = False,
    check_source_state: bool = True,
    check_repository_identity: bool = True,
) -> ArtifactValidation:
    resolution = resolve_artifact_manifest(bundle)
    if resolution.present and not resolution.valid:
        return ArtifactValidation(False, [resolution.error], {})
    manifest = read_overlay_bundle_manifest(bundle)
    messages: list[str] = []
    ok = True
    if not manifest:
        return ArtifactValidation(False, ["Archive is not an overlay bundle: missing devtool-bundle.json"], {})
    kind = str(manifest.get("kind") or manifest.get("type") or "").strip().lower()
    if kind and kind not in BUNDLE_KINDS:
        ok = False
        messages.append(f"Bundle manifest kind/type is {kind!r}, expected 'devtool.overlay_bundle'.")
    schema_version = _schema_version(manifest, messages, bundle=True)
    if schema_version is None:
        ok = False
    if check_repository_identity and not _validate_repository_identity(repo_root, manifest, messages):
        ok = False
    if not _validate_validation_policy(manifest, messages, bundle=True):
        ok = False
    if not _validate_preflight_policy(manifest, messages):
        ok = False
    if not _validate_finalize_policy(manifest, messages):
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
    if "implicit" in manifest and not isinstance(manifest.get("implicit"), bool):
        ok = False
        messages.append("Bundle implicit must be a boolean.")
    if schema_version == 2 and manifest.get("implicit") is not True:
        for spec in specs:
            if not spec.sha256:
                ok = False
                messages.append(f"Bundle schema v2 overlay entry requires sha256: {spec.path}")
            if spec.size is None:
                ok = False
                messages.append(f"Bundle schema v2 overlay entry requires size: {spec.path}")
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
                check_repository_identity=check_repository_identity,
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
        if check_source_state and not _validate_bundle_inventory_chain(repo_root, entries, messages, ignore_checksum=ignore_checksum):
            ok = False
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
    requires_clean = apply.get("requires_clean_worktree", False)
    if not isinstance(requires_clean, bool):
        ok = False
        messages.append("apply.requires_clean_worktree must be a boolean.")
    elif requires_clean:
        clean_known, status = _git_text_status(repo_root, "status", "--porcelain")
        if not clean_known:
            ok = False
            messages.append("Bundle requires a clean Git worktree, but Git cleanliness could not be determined.")
        elif status:
            ok = False
            messages.append("Bundle requires a clean Git worktree.")
    commit_enabled, commit_message, commit_strategy = artifact_commit_policy(manifest)
    if commit_strategy not in {"single", "per-overlay"}:
        ok = False
        messages.append(f"Unsupported bundle commit strategy: {commit_strategy!r}.")
    if bool(apply.get("atomic", True)) and commit_strategy == "per-overlay" and commit_enabled:
        ok = False
        messages.append("Atomic bundles must use apply.commit.strategy='single'.")
    commit_block = apply.get("commit") if isinstance(apply.get("commit"), dict) else None
    if commit_block is not None and "enabled" in commit_block and not isinstance(commit_block.get("enabled"), bool):
        ok = False
        messages.append("apply.commit.enabled must be a boolean.")
    if commit_block is not None and commit_block.get("enabled") is True and not commit_message:
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
    preflight_script = artifact_preflight_script(manifest)
    if preflight_script:
        bundle_members_with_data = list_archive_members(bundle, include_data=True)
        if not _validate_embedded_script_member(
            bundle_members_with_data,
            preflight_script,
            messages,
            label="Preflight",
            ignore_checksum=ignore_checksum,
        ):
            ok = False
    finalize_script = artifact_finalize_script(manifest)
    if finalize_script:
        bundle_members_with_data = list_archive_members(bundle, include_data=True)
        if not _validate_embedded_script_member(
            bundle_members_with_data,
            finalize_script,
            messages,
            label="Finalize",
            ignore_checksum=ignore_checksum,
        ):
            ok = False
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
    def record_bundle_declared_write(write_path: str, kind: str) -> None:
        rel = _canonical_archive_path(write_path)
        for change in changes:
            if str(change.get("path") or "") == rel:
                if str(change.get("kind") or "") == "unchanged":
                    change["kind"] = kind
                return
        changes.append({"kind": kind, "path": rel, "overlay": "<bundle>"})

    for write_path in artifact_preflight_write_paths(manifest):
        record_bundle_declared_write(write_path, "preflight-write")
    for write_path in artifact_validation_write_paths(manifest):
        record_bundle_declared_write(write_path, "validation-write")
    for write_path in artifact_finalize_write_paths(manifest):
        record_bundle_declared_write(write_path, "finalize-write")
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
    validation_tasks: list[str] | tuple[str, ...] | str | None = None,
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
        nested_bytes = path.read_bytes()
        entries[arcname] = nested_bytes
        nested_manifest = read_artifact_manifest(path)
        item: dict[str, Any] = {
            "id": str(nested_manifest.get("artifact_id") or path.stem),
            "path": arcname,
            "label": str(nested_manifest.get("name") or path.stem),
            "required": True,
            "sha256": hashlib.sha256(nested_bytes).hexdigest(),
            "size": len(nested_bytes),
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
    validation_tasks_declared = validation_tasks is not None
    clean_validation_tasks = _normalize_validation_tasks(validation_tasks) if validation_tasks_declared else []
    if validation_script:
        if (
            not isinstance(validation_timeout_seconds, int)
            or isinstance(validation_timeout_seconds, bool)
            or validation_timeout_seconds <= 0
        ):
            raise RuntimeError("validation_timeout_seconds must be a positive integer.")

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
            # Bundle creation is atomic by default.  A validator failure must
            # therefore restore repository mutation state; pause/keep semantics
            # may retain evidence, never the partially applied bundle payload.
            "rollback_on_failure": True,
            "failure_action": "rollback",
        },
    }
    if validation_tasks_declared:
        bundle_manifest["validation"]["tasks"] = clean_validation_tasks
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
            "timeout_seconds": validation_timeout_seconds,
        }
    entries["devtool-bundle.json"] = (json.dumps(bundle_manifest, indent=2) + "\n").encode("utf-8")
    created = write_archive_entries(output, entries)
    validation = validate_overlay_bundle(
        Path.cwd(),
        created,
        ignore_checksum=False,
        check_source_state=False,
        check_repository_identity=False,
    )
    if not validation.ok:
        created.unlink(missing_ok=True)
        raise RuntimeError("Generated overlay bundle failed self-validation: " + "; ".join(validation.messages))
    return created

def read_artifact_manifest(artifact: str | Path) -> dict[str, Any]:
    resolution = resolve_artifact_manifest(artifact)
    if resolution.valid and resolution.present:
        data = dict(resolution.manifest)
        kind = _explicit_artifact_type(data)
        if kind in BUNDLE_KINDS or (not kind and (isinstance(data.get("apply_order"), list) or isinstance(data.get("overlays"), list))):
            data.setdefault("type", "overlay_bundle")
            data.setdefault("artifact_type", "overlay_bundle")
        return data
    if not resolution.present and resolution.valid:
        bundle_manifest = _implicit_overlay_bundle_manifest(artifact)
        if bundle_manifest:
            data = dict(bundle_manifest)
            data.setdefault("type", "overlay_bundle")
            data.setdefault("artifact_type", "overlay_bundle")
            return data
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
    known, text = _git_text_status(repo_root, *args)
    return text if known else ""


def _git_text_status(repo_root: Path, *args: str) -> tuple[bool, str]:
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
        return False, ""
    return (True, proc.stdout.strip()) if proc.returncode == 0 else (False, "")


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
    enforce_remote = repository.get("enforce_remote", False)
    if not isinstance(enforce_remote, bool):
        ok = False
        messages.append("repository.enforce_remote must be a boolean.")
        enforce_remote = False
    remote_known, current_remote = _git_text_status(repo_root, "remote", "get-url", "origin") if expected_remote else (True, "")
    if expected_remote and enforce_remote and not remote_known:
        ok = False
        messages.append("Repository remote identity could not be determined while enforce_remote=true.")
    elif expected_remote and current_remote and _normalized_remote(expected_remote) != _normalized_remote(current_remote):
        if enforce_remote:
            ok = False
            messages.append(f"Repository remote mismatch: expected {expected_remote!r}, found {current_remote!r}.")
        else:
            messages.append(f"Warning: repository remote differs from artifact metadata ({current_remote!r}).")

    expected_branch = str(repository.get("branch") or "").strip()
    enforce_branch = repository.get("enforce_branch", False)
    if not isinstance(enforce_branch, bool):
        ok = False
        messages.append("repository.enforce_branch must be a boolean.")
        enforce_branch = False
    branch_known, current_branch = _git_text_status(repo_root, "branch", "--show-current") if expected_branch else (True, "")
    if expected_branch and enforce_branch and (not branch_known or not current_branch):
        ok = False
        messages.append("Repository branch identity could not be determined while enforce_branch=true.")
    elif expected_branch and current_branch and expected_branch != current_branch:
        if enforce_branch:
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

    inventory_paths: set[str] = set()
    for entry in entries:
        value = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(value, str) and value.strip():
            try:
                inventory_paths.add(_canonical_archive_path(value))
            except RuntimeError:
                inventory_paths.add(value.strip())
    allowed_metadata_payloads = {
        path for path in inventory_paths if path and is_allowed_artifact_metadata_payload_member(path)
    }
    member_map = {
        member.name: member
        for member in members
        if not member.is_dir
        and not _is_skipped_artifact_payload_member(
            member.name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        )
    }
    declared_payload: set[str] = set()
    seen: set[str] = set()
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    conflict_policy = str(apply.get("conflict_policy") or "fail").strip().lower()

    for index, entry in enumerate(entries, 1):
        raw_path = entry.get("path")
        path = raw_path.strip() if isinstance(raw_path, str) else ""
        action = str(entry.get("action") or "").strip().lower()
        if not path:
            ok = False
            messages.append(f"Inventory entry #{index} is missing path.")
            continue
        try:
            rel = _safe_rel(path)
            path = rel.as_posix()
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
            continue
        if is_artifact_metadata_member(path) and not is_allowed_artifact_metadata_payload_member(path):
            ok = False
            messages.append(f"Inventory entry cannot target reserved artifact metadata: {path}")
            continue
        if is_ignored_artifact_payload_member(path):
            ok = False
            messages.append(f"Inventory entry cannot target ignored generated cache payload: {path}")
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
                exists = target.exists() or target.is_symlink()
                if not exists or (not target.is_file() and not target.is_symlink()):
                    ok = False
                    messages.append(f"Inventory expects existing file or symlink for {action}: {path}")
                elif not ignore_checksum and source_sha:
                    try:
                        actual_source = path_source_sha256(target).lower()
                    except RuntimeError as exc:
                        ok = False
                        messages.append(str(exc))
                    else:
                        if actual_source != source_sha:
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
    check_repository_identity: bool = True,
) -> ArtifactValidation:
    resolution = resolve_artifact_manifest(artifact)
    if not resolution.valid:
        return ArtifactValidation(False, [resolution.error or "Artifact manifest could not be resolved."], {})
    if artifact_type in {"overlay", "overlay_bundle"} and is_overlay_bundle(artifact):
        return validate_overlay_bundle(
            repo_root,
            artifact,
            ignore_checksum=ignore_checksum,
            check_source_state=check_source_state,
            check_repository_identity=check_repository_identity,
        )
    manifest = read_artifact_manifest(artifact)
    messages: list[str] = []
    ok = True

    try:
        members = list_archive_members(artifact, include_data=True)
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)

    if not members:
        return ArtifactValidation(False, ["Artifact archive is empty."], manifest)

    if manifest and _schema_version(manifest, messages) is None:
        ok = False
    if not _validate_inventory_shape(manifest, messages):
        ok = False
    inventory_entries = artifact_inventory_entries(manifest)
    if not validate_base_contract_shape(manifest, inventory_entries, messages):
        ok = False

    if check_repository_identity and not _validate_repository_identity(repo_root, manifest, messages):
        ok = False
    if not _validate_validation_policy(manifest, messages, bundle=False):
        ok = False
    if not _validate_preflight_policy(manifest, messages):
        ok = False
    if not _validate_finalize_policy(manifest, messages):
        ok = False

    try:
        delete_manifest_paths = _delete_manifest_entries_from_members(manifest, members)
    except Exception as exc:
        ok = False
        delete_manifest_paths = []
        messages.append(str(exc))

    payload_paths = {
        member.name
        for member in members
        if not member.is_dir
        and not _is_skipped_artifact_payload_member(
            member.name,
            allowed_metadata_payloads=artifact_inventory_metadata_payload_paths(manifest),
        )
    }
    payload_delete_conflicts = sorted(payload_paths.intersection(delete_manifest_paths))
    if payload_delete_conflicts:
        ok = False
        messages.append(
            "Artifact payload conflicts with .devtool-delete for path(s): "
            + ", ".join(payload_delete_conflicts[:10])
        )
    if check_source_state:
        for payload_path in sorted(payload_paths):
            target = repo_root / _safe_rel(payload_path)
            if target.is_dir() and not target.is_symlink():
                ok = False
                messages.append(f"Archive payload would replace existing directory: {payload_path}")

    expected_type = str(manifest.get("type") or manifest.get("artifact_type") or "").strip().lower()
    if expected_type and expected_type not in {artifact_type, "patch" if artifact_type == "overlay" else artifact_type}:
        ok = False
        messages.append(f"Manifest type is {expected_type!r}, but command expects {artifact_type!r}.")

    expected_solution = artifact_solution(manifest)
    if expected_solution:
        try:
            solution_rel = _safe_rel(expected_solution)
        except RuntimeError as exc:
            ok = False
            messages.append(str(exc))
            solution_rel = None
        solution_path = repo_root / solution_rel if solution_rel is not None else repo_root / "__invalid_solution__"
        names = {m.name for m in members}
        if solution_rel is not None and not solution_path.exists() and expected_solution not in names:
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
    commit_block = apply.get("commit") if isinstance(apply.get("commit"), dict) else None
    if commit_block is not None and "enabled" in commit_block and not isinstance(commit_block.get("enabled"), bool):
        ok = False
        messages.append("apply.commit.enabled must be a boolean.")
    if commit_block is not None and commit_block.get("enabled") is True and not commit_message:
        ok = False
        messages.append("Enabled artifact commits require a non-empty message.")
    requires_clean = apply.get("requires_clean_worktree", False)
    if not isinstance(requires_clean, bool):
        ok = False
        messages.append("apply.requires_clean_worktree must be a boolean.")
    elif requires_clean:
        clean_known, status = _git_text_status(repo_root, "status", "--porcelain")
        if not clean_known:
            ok = False
            messages.append("Artifact requires a clean Git worktree, but Git cleanliness could not be determined.")
        elif status:
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

    if check_source_state and str(manifest.get("base_contract_mode") or "").strip():
        try:
            base_result = validate_base_contract(repo_root, manifest, inventory_entries)
        except Exception as exc:
            base_result = None
            ok = False
            messages.append(f"Base contract verification failed: {type(exc).__name__}: {exc}")
        if base_result is not None:
            if not base_result.ok:
                ok = False
            messages.extend(base_result.messages)

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

    preflight_script = artifact_preflight_script(manifest)
    if preflight_script and not _validate_embedded_script_member(
        members,
        preflight_script,
        messages,
        label="Preflight",
        ignore_checksum=ignore_checksum,
    ):
        ok = False

    finalize_script = artifact_finalize_script(manifest)
    if finalize_script and not _validate_embedded_script_member(
        members,
        finalize_script,
        messages,
        label="Finalize",
        ignore_checksum=ignore_checksum,
    ):
        ok = False

    if ignore_checksum:
        messages.append("Warning: checksum verification skipped by --ignore-checksum.")
    if not messages:
        messages.append("Artifact validation passed.")
    return ArtifactValidation(ok, messages, manifest)



def _delete_manifest_entries_from_members(
    manifest: dict[str, Any],
    members: list[ArchiveMember],
) -> list[str]:
    entries: list[str] = []
    for item in artifact_inventory_entries(manifest):
        if str(item.get("action") or "").strip().lower() == "delete":
            path_value = item.get("path")
            if not isinstance(path_value, str):
                raise RuntimeError("Inventory delete entry path must be a string.")
            path = _canonical_archive_path(path_value)
            if path:
                entries.append(path)

    for member in members:
        if member.name != DELETE_MANIFEST_NAME or member.data is None or member.is_dir:
            continue
        text = member.data.decode("utf-8").strip()
        if not text:
            continue
        # Allow either a JSON string list or simple newline-delimited paths.
        if text.startswith("["):
            try:
                data = json.loads(text)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSON in .devtool-delete: {exc}") from exc
            if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
                raise RuntimeError(".devtool-delete JSON must be a list of strings.")
            entries.extend(_canonical_archive_path(item) for item in data if item.strip())
            continue
        if text.startswith("{"):
            raise RuntimeError(".devtool-delete JSON must be a list of strings.")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(_canonical_archive_path(line))
    return list(dict.fromkeys(entries))


def read_delete_manifest(artifact: str | Path) -> list[str]:
    """Read root-level overlay deletion entries from ``.devtool-delete``."""
    manifest = read_artifact_manifest(artifact)
    members = list_archive_members(artifact, include_data=True)
    return _delete_manifest_entries_from_members(manifest, members)

def _bytes_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preview_artifact(repo_root: Path, artifact: str | Path, *, snapshot: bool = False) -> dict[str, Any]:
    if not snapshot and is_overlay_bundle(artifact):
        return preview_overlay_bundle(repo_root, artifact)
    manifest = read_artifact_manifest(artifact) if not snapshot else {}
    allowed_metadata_payloads = artifact_inventory_metadata_payload_paths(manifest)
    members = list_archive_members(artifact, include_data=True)
    file_members = [m for m in members if not m.is_dir]
    incoming_files = {
        Path(m.name): m
        for m in file_members
        if not _is_skipped_artifact_payload_member(
            m.name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        )
    }
    incoming_dirs = {
        Path(m.name)
        for m in members
        if m.is_dir
        and not _is_skipped_artifact_payload_member(
            m.name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        )
    }
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

    for rel in sorted(incoming_dirs, key=str):
        target = repo_root / rel
        if target.exists() or target.is_symlink():
            continue
        changes.append({"kind": "add-dir", "path": str(rel)})

    for rel, member in sorted(incoming_files.items(), key=lambda item: str(item[0])):
        target = repo_root / rel
        if not target.exists() and not target.is_symlink():
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

    def record_declared_write(write_path: str, kind: str) -> None:
        rel = _canonical_archive_path(write_path)
        for change in changes:
            if str(change.get("path") or "") == rel:
                # A hook write needs rollback protection even when the payload
                # member is byte-for-byte unchanged.  Upgrade the preview kind so
                # transaction planning does not discard it as harmless.
                if str(change.get("kind") or "") == "unchanged":
                    change["kind"] = kind
                return
        changes.append({"kind": kind, "path": rel})

    for write_path in artifact_preflight_write_paths(manifest):
        record_declared_write(write_path, "preflight-write")

    for write_path in artifact_validation_write_paths(manifest):
        record_declared_write(write_path, "validation-write")

    for write_path in artifact_finalize_write_paths(manifest):
        record_declared_write(write_path, "finalize-write")

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
    manifest = read_artifact_manifest(artifact) if not snapshot else {}
    allowed_metadata_payloads = artifact_inventory_metadata_payload_paths(manifest)
    members = list_archive_members(artifact, include_data=True)
    file_members = [m for m in members if not m.is_dir]
    incoming_files = {
        Path(m.name): m
        for m in file_members
        if not _is_skipped_artifact_payload_member(
            m.name,
            allowed_metadata_payloads=allowed_metadata_payloads,
        )
    }
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
        "add-dir": "+",
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


def operation_history_entries_dir(repo_root: Path) -> Path:
    """Return authoritative runtime history outside the repository worktree."""
    identity = hashlib.sha256(str(repo_root.expanduser().resolve()).encode("utf-8")).hexdigest()[:24]
    return state_dir() / "artifact-history" / identity / "entries"


def _history_entry_files(repo_root: Path) -> list[Path]:
    directory = history_entries_dir(repo_root)
    return sorted(directory.glob("*.json")) if directory.exists() else []


def _operation_history_entry_files(repo_root: Path) -> list[Path]:
    directory = operation_history_entries_dir(repo_root)
    return sorted(directory.glob("*.json")) if directory.exists() else []


def load_history(repo_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Runtime operation records are authoritative and are written only after
    # commit/rollback outcome is known.  Repository-local v1/v2 history remains
    # readable below for backward compatibility.
    for entry_path in _operation_history_entry_files(repo_root):
        try:
            item = json.loads(entry_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        key = str(item.get("history_id") or item.get("operation_id") or item.get("sha256") or entry_path.name)
        if key in seen:
            continue
        seen.add(key)
        entries.append(item)

    # Repository-local append records from older/current opted-in repositories.
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
    _durable_atomic_write_text(path, json.dumps({"schema_version": 3, "artifacts": entries}, indent=2) + "\n")
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


def append_history(repo_root: Path, entry: dict[str, Any], *, repo_local: bool = True) -> Path:
    """Atomically upsert one idempotent operation history record.

    The XDG/state copy is authoritative and always written.  Repository-local
    history is optional compatibility metadata and may be omitted for automatic
    commit flows so history finalization cannot dirty or distort the commit.
    """
    runtime_directory = operation_history_entries_dir(repo_root)
    runtime_directory.mkdir(parents=True, exist_ok=True)
    lock_path = history_lock_file(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            history_id = str(entry.get("history_id") or uuid.uuid4().hex)
            entry = dict(entry)
            entry["history_id"] = history_id
            entry.setdefault("schema_version", 3)
            runtime_path = runtime_directory / f"{history_id}.json"
            _durable_atomic_write_text(runtime_path, json.dumps(entry, ensure_ascii=False, indent=2) + "\n")

            path = runtime_path
            if repo_local:
                directory = history_entries_dir(repo_root)
                directory.mkdir(parents=True, exist_ok=True)
                stamp = re.sub(r"[^0-9A-Za-z]+", "", str(entry.get("applied_at") or ""))[:20] or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                path = directory / f"{stamp}-{history_id}.json"
                _durable_atomic_write_text(path, json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
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
        "schema_version": 3,
        "applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact": str(path),
        "artifact_name": path.name,
        "type": artifact_type,
        "sha256": digest,
        "content_identity": {
            "sha256": digest,
            "size": path.stat().st_size if path.exists() and path.is_file() else None,
        },
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

def artifact_validation_refs(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize first-class artifact validation references.

    ``validation.refs`` is intentionally separate from ``validation.tests``:
    the latter preserves the artifact-v2 test protocol, while refs can name a
    first-class TestSpec or a configured workflow/invoke action.  Workflow refs
    are lowered through the canonical workflow scheduler/EXO path rather than
    spawning a second ad-hoc validation script.
    """
    raw = manifest.get("validation")
    if not isinstance(raw, dict):
        return []
    refs = raw.get("refs", [])
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ValueError("validation.refs must be a list.")
    out: list[dict[str, str]] = []
    for index, item in enumerate(refs, 1):
        if isinstance(item, str):
            ref = item.strip()
        elif isinstance(item, dict):
            if set(item) != {"ref"}:
                raise ValueError(f"validation.refs entry {index} may only contain ref.")
            ref = str(item.get("ref") or "").strip()
        else:
            raise ValueError(f"validation.refs entry {index} must be a string or ref object.")
        if not ref or ":" not in ref:
            raise ValueError(f"validation.refs entry {index} must use test:<id>, workflow:<action>, or invoke:<action>.")
        kind, name = ref.split(":", 1)
        kind = kind.strip().lower()
        name = name.strip()
        if kind not in {"test", "workflow", "invoke"} or not name:
            raise ValueError(f"validation.refs entry {index} must use test:<id>, workflow:<action>, or invoke:<action>.")
        canonical_kind = "workflow" if kind == "invoke" else kind
        out.append({"ref": f"{canonical_kind}:{name}", "kind": canonical_kind, "name": name})
    return out


def artifact_validation_tests(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize artifact-v2 validation.tests without conflating them with lifecycle scripts."""
    raw=manifest.get("validation")
    if not isinstance(raw,dict): return []
    tests=raw.get("tests",[])
    if tests is None: return []
    if not isinstance(tests,list): raise ValueError("validation.tests must be a list.")
    out=[]
    for i,item in enumerate(tests,1):
        if isinstance(item,str): item={"ref":item}
        if not isinstance(item,dict): raise ValueError(f"validation.tests entry {i} must be an object or test ref string.")
        ref=str(item.get("ref") or "").strip(); tid=str(item.get("id") or "").strip()
        if ref:
            if len(item)!=1: raise ValueError(f"validation.tests ref entry {i} may only contain ref.")
            out.append({"ref":ref}); continue
        runner=str(item.get("runner") or "").strip(); command=item.get("command",[]); task=str(item.get("task") or "").strip()
        if not tid or not runner: raise ValueError(f"validation.tests entry {i} requires id and runner, or ref.")
        if isinstance(command,str): import shlex; command=shlex.split(command)
        if task:
            if command: raise ValueError(f"validation.tests {tid!r} cannot declare both command and task.")
            if runner not in {"gradle","android"}: raise ValueError(f"validation.tests {tid!r} task requires gradle/android runner.")
            # Keep Gradle roots semantic.  artifact_tests lowers this through
            # the same GradleInvocationRequest/planner path as declared tests,
            # validation, workflow and CLI callers instead of re-baking
            # ``./gradlew`` into artifact-local validation.
            required=item.get("required", True)
            if not isinstance(required, bool): raise ValueError(f"validation.tests {tid!r} required must be a boolean.")
            out.append({"id":tid,"name":str(item.get("name") or tid),"runner":runner,"task":task,"cwd":str(item.get("cwd") or ""),"required":required})
            continue
        if not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command): raise ValueError(f"validation.tests {tid!r} requires a command or task.")
        required=item.get("required", True)
        if not isinstance(required, bool): raise ValueError(f"validation.tests {tid!r} required must be a boolean.")
        out.append({"id":tid,"name":str(item.get("name") or tid),"runner":runner,"command":list(command),"cwd":str(item.get("cwd") or ""),"required":required})
    return out
