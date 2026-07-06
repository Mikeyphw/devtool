from __future__ import annotations

import contextlib
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .console_utils import resolve_cli_path

MANIFEST_NAMES = {".devtool-artifact.json", "artifact.devtool.json", "devtool-artifact.json"}
DELETE_MANIFEST_NAME = ".devtool-delete"
ARTIFACT_METADATA_NAMES = MANIFEST_NAMES | {DELETE_MANIFEST_NAME}
HISTORY_PATH = Path(".devtool") / "artifacts.json"

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
    return Path(name).name in ARTIFACT_METADATA_NAMES


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Unsafe archive path: {name}")
    return rel


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


def read_overlay_bundle_manifest(artifact: str | Path) -> dict[str, Any]:
    """Read an overlay bundle manifest without treating it as a normal overlay manifest."""
    try:
        for member in list_archive_members(artifact, include_data=True):
            if Path(member.name).name not in BUNDLE_MANIFEST_NAMES or member.data is None:
                continue
            data = json.loads(member.data.decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def is_overlay_bundle(artifact: str | Path) -> bool:
    manifest = read_overlay_bundle_manifest(artifact)
    if not manifest:
        return False
    kind = str(manifest.get("kind") or manifest.get("type") or "").strip().lower()
    return kind in BUNDLE_KINDS or isinstance(manifest.get("apply_order"), list)


def _bundle_apply_order(bundle: str | Path, manifest: dict[str, Any] | None = None) -> list[str]:
    manifest = manifest or read_overlay_bundle_manifest(bundle)
    raw_order = manifest.get("apply_order") or manifest.get("overlays")
    if not isinstance(raw_order, list):
        raise RuntimeError("Overlay bundle manifest must contain an apply_order list.")
    order: list[str] = []
    for item in raw_order:
        name = str(item).strip()
        if not name:
            continue
        _safe_rel(name)
        if not archive_suffix(name):
            raise RuntimeError(f"Bundle member is not a supported archive: {name}")
        order.append(name)
    if not order:
        raise RuntimeError("Overlay bundle apply_order is empty.")
    return order


def materialize_overlay_bundle(bundle: str | Path, target_dir: str | Path) -> tuple[dict[str, Any], list[Path]]:
    """Extract nested overlay archives listed by a bundle manifest into target_dir."""
    manifest = read_overlay_bundle_manifest(bundle)
    if not manifest:
        raise RuntimeError("Archive is not an overlay bundle: missing devtool-bundle.json")
    order = _bundle_apply_order(bundle, manifest)
    files = _archive_file_data(bundle)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    materialized: list[Path] = []
    for name in order:
        data = files.get(name)
        if data is None:
            raise RuntimeError(f"Overlay bundle lists missing member: {name}")
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        materialized.append(out)
    return manifest, materialized


def overlay_bundle_conflicts(bundle: str | Path) -> dict[str, list[str]]:
    """Return payload files written by more than one nested overlay in apply order."""
    writers: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-conflicts-") as tmp:
        _manifest, overlays = materialize_overlay_bundle(bundle, tmp)
        for overlay in overlays:
            try:
                members = list_archive_members(overlay, include_data=False)
            except Exception:
                continue
            for member in members:
                if member.is_dir or is_artifact_metadata_member(member.name):
                    continue
                writers.setdefault(member.name, []).append(overlay.name)
    return {path: names for path, names in writers.items() if len(names) > 1}


def validate_overlay_bundle(repo_root: Path, bundle: str | Path, *, fail_on_conflict: bool = False) -> ArtifactValidation:
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
        order = _bundle_apply_order(bundle, manifest)
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)
    try:
        members = {m.name for m in list_archive_members(bundle, include_data=False) if not m.is_dir}
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)
    missing = [name for name in order if name not in members]
    if missing:
        ok = False
        messages.append("Bundle apply_order lists missing overlays: " + ", ".join(missing[:10]))
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-validate-") as tmp:
        try:
            _manifest, overlays = materialize_overlay_bundle(bundle, tmp)
        except Exception as exc:
            return ArtifactValidation(False, [str(exc)], manifest)
        for index, overlay in enumerate(overlays, 1):
            validation = validate_artifact(repo_root, overlay, artifact_type="overlay")
            prefix = f"[{index}/{len(overlays)}] {overlay.name}"
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
        if fail_on_conflict:
            ok = False
    if ok:
        messages.insert(0, f"Overlay bundle validation passed ({len(order)} overlay(s)).")
    return ArtifactValidation(ok, messages, manifest)


def preview_overlay_bundle(repo_root: Path, bundle: str | Path) -> dict[str, Any]:
    changes: list[dict[str, str]] = []
    total_files = 0
    manifest: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="devtool-bundle-preview-") as tmp:
        manifest, overlays = materialize_overlay_bundle(bundle, tmp)
        for index, overlay in enumerate(overlays, 1):
            preview = preview_artifact(repo_root, overlay, snapshot=False)
            total_files += int(preview.get("files", 0))
            for change in preview.get("changes", []):
                if not isinstance(change, dict):
                    continue
                updated = {str(k): str(v) for k, v in change.items()}
                updated["overlay"] = overlay.name
                updated["overlay_index"] = str(index)
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
        _manifest, overlays = materialize_overlay_bundle(bundle, tmp)
        for index, overlay in enumerate(overlays, 1):
            if remaining <= 0:
                sections.append("... bundle diff truncated")
                break
            text = diff_artifact(repo_root, overlay, snapshot=False, context=context, max_lines=remaining)
            lines = text.splitlines()
            remaining -= len(lines)
            sections.append(f"# [{index}/{len(overlays)}] {overlay.name}\n" + text.rstrip())
    return "\n\n".join(section for section in sections if section).rstrip() + "\n"


def create_overlay_bundle(output: str | Path, overlays: list[str | Path], *, name: str = "", description: str = "") -> Path:
    if not overlays:
        raise RuntimeError("No overlays were provided for the bundle.")
    entries: dict[str, bytes] = {}
    apply_order: list[str] = []
    used_names: set[str] = set()
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
        apply_order.append(arcname)
    bundle_manifest = {
        "kind": "devtool.overlay_bundle",
        "version": 1,
        "name": name or Path(output).name,
        "description": description,
        "apply_order": apply_order,
        "stop_on_failure": True,
        "validate_each": True,
        "validate_final": True,
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


def validate_artifact(repo_root: Path, artifact: str | Path, *, artifact_type: str = "overlay") -> ArtifactValidation:
    if artifact_type in {"overlay", "overlay_bundle"} and is_overlay_bundle(artifact):
        return validate_overlay_bundle(repo_root, artifact)
    manifest = read_artifact_manifest(artifact)
    messages: list[str] = []
    ok = True

    try:
        members = list_archive_members(artifact, include_data=False)
    except Exception as exc:
        return ArtifactValidation(False, [str(exc)], manifest)

    if not members:
        return ArtifactValidation(False, ["Artifact archive is empty."], manifest)

    expected_repo = str(manifest.get("repo") or manifest.get("repo_name") or manifest.get("expected_repo") or "").strip()
    if expected_repo and expected_repo not in {repo_root.name, str(repo_root)}:
        ok = False
        messages.append(f"Manifest expects repo {expected_repo!r}, current repo is {repo_root.name!r}.")

    expected_type = str(manifest.get("type") or manifest.get("artifact_type") or "").strip().lower()
    if expected_type and expected_type not in {artifact_type, "patch" if artifact_type == "overlay" else artifact_type}:
        ok = False
        messages.append(f"Manifest type is {expected_type!r}, but command expects {artifact_type!r}.")

    expected_solution = str(manifest.get("expected_solution") or manifest.get("solution") or "").strip()
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

    if not messages:
        messages.append("Artifact validation passed.")
    return ArtifactValidation(ok, messages, manifest)



def read_delete_manifest(artifact: str | Path) -> list[str]:
    """Read overlay deletion entries from .devtool-delete without treating it as payload."""
    entries: list[str] = []
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
    return entries

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


def load_history(repo_root: Path) -> list[dict[str, Any]]:
    path = history_file(repo_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            items = data.get("artifacts", [])
        else:
            items = data
        return items if isinstance(items, list) else []
    except Exception:
        return []


def save_history(repo_root: Path, entries: list[dict[str, Any]]) -> Path:
    path = history_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "artifacts": entries}, indent=2) + "\n", encoding="utf-8")
    return path


def append_history(repo_root: Path, entry: dict[str, Any]) -> Path:
    entries = load_history(repo_root)
    entries.append(entry)
    return save_history(repo_root, entries)


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
