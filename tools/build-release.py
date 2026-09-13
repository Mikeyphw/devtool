#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shutil
import stat
import tarfile
import zipfile

from devtool.release_qualification import load_versions, repository_root, sha256_file, validate_version_consistency


EXCLUDED_PARTS = {
    ".git", ".gradle", ".idea", ".pytest_cache", "__pycache__", "build", "dist",
    ".devtool", ".venv", "venv", "node_modules",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".class", ".apk", ".aab", ".deb"}


def source_date_epoch(root: Path) -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as error:
            raise SystemExit("SOURCE_DATE_EPOCH must be an integer") from error
        return max(value, 315532800)
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip().isdigit():
        return max(int(result.stdout.strip()), 315532800)
    # Repositories shipped without .git still need deterministic output.
    return 315532800


def zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(epoch, timezone.utc)
    year = min(max(value.year, 1980), 2107)
    return year, value.month, value.day, value.hour, value.minute, value.second - value.second % 2


def _safe_relative_path(relative: Path) -> str:
    text = relative.as_posix()
    if not text or text.startswith("/") or "\\" in text:
        raise RuntimeError(f"Unsafe release path: {text!r}")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError(f"Unsafe release path: {text!r}")
    return text


def iter_source_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        _safe_relative_path(relative)
        yield path, relative


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path, relative in iter_source_files(root):
        digest.update(_safe_relative_path(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def git_head(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temporary.unlink(missing_ok=True)


def build_source_archive(root: Path, output: Path, *, epoch: int) -> dict[str, object]:
    versions = load_versions(root)
    prefix = f"devtool-{versions['devtool']}/"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, relative in iter_source_files(root):
            info = zipfile.ZipInfo(prefix + _safe_relative_path(relative), date_time=zip_datetime(epoch))
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = (permissions & 0xFFFF) << 16
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt member in generated archive: {bad}")
    return {
        "path": output.name,
        "sha256": sha256_file(output),
        "bytes": output.stat().st_size,
        "entries": sum(1 for _ in iter_source_files(root)),
    }




RELEASE_ARTIFACT_SUFFIXES = (
    ".whl", ".tar.gz", ".tgz", ".deb", ".rpm", ".apk", ".aab", ".aar",
    ".nupkg", ".snupkg", ".crate", ".jar", ".war", ".zip",
)


def artifact_kind(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith(".tar.gz"):
        return "sdist"
    if lower.endswith(".whl"):
        return "wheel"
    if lower.endswith(".apk"):
        return "apk"
    if lower.endswith(".aab"):
        return "aab"
    if lower.endswith(".aar"):
        return "aar"
    if lower.endswith(".deb"):
        return "deb"
    if lower.endswith(".rpm"):
        return "rpm"
    if lower.endswith(".crate"):
        return "crate"
    if lower.endswith(".nupkg"):
        return "nupkg"
    if lower.endswith(".snupkg"):
        return "snupkg"
    if lower.endswith(".jar"):
        return "jar"
    if lower.endswith(".war"):
        return "war"
    if lower.endswith(".zip"):
        return "zip"
    return path.suffix.lower().lstrip(".") or "file"


CURRENT_RELEASE_ARTIFACTS = ".devtool-current-release-artifacts.json"
GENERATED_RELEASE_FILES = {"release-artifacts.json", "SHA256SUMS", "SHA256SUMS.all", CURRENT_RELEASE_ARTIFACTS}


def _safe_archive_member_name(name: str, *, archive: str) -> str:
    normalized = name.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "//" in normalized
        or "\\" in name
        or "\x00" in normalized
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise RuntimeError(f"unsafe {archive} member path: {name!r}")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"unsafe {archive} member path: {name!r}")
    return normalized


def _safe_tar_link_target(target: str, *, member: str) -> None:
    if not target:
        raise RuntimeError(f"unsafe empty tar link target for {member!r}")
    _safe_archive_member_name(target, archive="tar link")


def _zip_member_names(archive: zipfile.ZipFile) -> tuple[set[str], dict[str, zipfile.ZipInfo]]:
    names: set[str] = set()
    infos: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        normalized = _safe_archive_member_name(info.filename, archive="zip")
        names.add(normalized)
        infos[normalized] = info
    return names, infos


def _read_zip_text(archive: zipfile.ZipFile, name: str, *, limit: int = 1024 * 1024) -> str:
    with archive.open(name, "r") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError(f"ZIP member is too large to verify safely: {name}")
    return payload.decode("utf-8", errors="replace")


def _require_wheel_metadata(archive: zipfile.ZipFile, names: set[str], checks: list[dict[str, object]]) -> None:
    dist_info_prefixes = sorted({name.split(".dist-info/", 1)[0] + ".dist-info/" for name in names if ".dist-info/" in name})
    if len(dist_info_prefixes) != 1:
        _verification_check(checks, "wheel.dist-info", "failed", "wheel must contain exactly one .dist-info directory")
        return
    prefix = dist_info_prefixes[0]
    required = ("WHEEL", "METADATA", "RECORD")
    missing = [prefix + member for member in required if prefix + member not in names]
    if missing:
        _verification_check(checks, "wheel.metadata", "failed", "missing " + ", ".join(missing))
        return
    wheel_text = _read_zip_text(archive, prefix + "WHEEL")
    metadata_text = _read_zip_text(archive, prefix + "METADATA")
    record_text = _read_zip_text(archive, prefix + "RECORD")
    wheel_ok = "Wheel-Version:" in wheel_text and "Generator:" in wheel_text
    metadata_ok = "Name:" in metadata_text and "Version:" in metadata_text
    record_paths = {line.split(",", 1)[0] for line in record_text.splitlines() if line.strip()}
    record_ok = all(prefix + member in record_paths for member in required)
    _verification_check(checks, "wheel.metadata", "passed" if wheel_ok and metadata_ok and record_ok else "failed", "validated" if wheel_ok and metadata_ok and record_ok else "invalid WHEEL/METADATA/RECORD content")


def _root_module_required_by_name(lower_name: str) -> bool:
    return any(token in lower_name for token in ("root-bridge", "root_module", "root-module", "magisk-module", "module.zip"))


def _require_root_module_metadata(path: Path, archive: zipfile.ZipFile, names: set[str], infos: dict[str, zipfile.ZipInfo], checks: list[dict[str, object]]) -> None:
    required_members = ("module.prop", "customize.sh", "bin/devtool-root-bridge")
    for member in required_members:
        present = member in names
        _verification_check(checks, f"member:{member}", "passed" if present else "failed", "present" if present else "missing")
    if not all(member in names for member in required_members):
        return
    module_prop = _read_zip_text(archive, "module.prop", limit=64 * 1024)
    customize = _read_zip_text(archive, "customize.sh", limit=64 * 1024)
    bridge = _read_zip_text(archive, "bin/devtool-root-bridge", limit=512 * 1024)
    prop_ok = all(re.search(rf"^{key}=\S+", module_prop, re.MULTILINE) for key in ("id", "name", "version", "versionCode"))
    script_ok = customize.startswith("#!/") and "devtool" in bridge.lower()
    _verification_check(
        checks,
        "root_module.content",
        "passed" if prop_ok and script_ok else "failed",
        "validated" if prop_ok and script_ok else "invalid module.prop/customize/root bridge content",
    )


def _tar_archive_integrity(path: Path) -> None:
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            _safe_archive_member_name(member.name, archive="tar")
            if member.issym() or member.islnk():
                _safe_tar_link_target(member.linkname, member=member.name)


def _current_artifact_names(out_dir: Path, manifest_path: Path | None) -> set[str]:
    path = manifest_path or (out_dir / CURRENT_RELEASE_ARTIFACTS)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Current release artifact manifest is invalid: {path}: {exc}") from exc
    entries = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("Current release artifact manifest must contain an artifacts list")
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Current release artifact manifest entries must be objects")
        name = str(entry.get("path") or "")
        if not name or "/" in name or "\\" in name or name.startswith(".") or name in {".", ".."}:
            raise RuntimeError(f"Unsafe current release artifact name: {name!r}")
        file_path = out_dir / name
        if not file_path.is_file():
            raise RuntimeError(f"Current release artifact is missing: {name}")
        expected = str(entry.get("sha256") or "")
        if expected and sha256_file(file_path) != expected:
            raise RuntimeError(f"Current release artifact checksum mismatch: {name}")
        names.add(name)
    return names


def _apksigner() -> str:
    direct = shutil.which("apksigner")
    if direct:
        return direct
    for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        build_tools = Path(raw).expanduser() / "build-tools"
        if not build_tools.is_dir():
            continue
        for directory in sorted(build_tools.iterdir(), key=lambda item: item.name, reverse=True):
            candidate = directory / ("apksigner.bat" if os.name == "nt" else "apksigner")
            if candidate.is_file():
                return str(candidate)
    return ""


def _verification_check(checks: list[dict[str, object]], name: str, status: str, detail: str = "") -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def verify_release_artifact(path: Path, *, require_apk_signature: bool = False) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    if not path.is_file():
        _verification_check(checks, "exists", "failed", "artifact is missing")
        return {"status": "failed", "checks": checks}
    if path.stat().st_size <= 0:
        _verification_check(checks, "non_empty", "failed", "artifact is empty")
        return {"status": "failed", "checks": checks}
    _verification_check(checks, "exists", "passed")
    _verification_check(checks, "non_empty", "passed")
    lower = path.name.lower()
    kind = artifact_kind(path)
    try:
        if lower.endswith((".zip", ".whl", ".jar", ".war", ".aar", ".apk", ".aab", ".nupkg", ".snupkg")):
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"corrupt ZIP member: {bad}")
                names, infos = _zip_member_names(archive)
                _verification_check(checks, "archive_members_safe", "passed")
                _verification_check(checks, "archive_integrity", "passed")
                if kind == "wheel":
                    _require_wheel_metadata(archive, names, checks)
                required_members: tuple[str, ...] = ()
                if kind == "apk":
                    required_members = ("AndroidManifest.xml",)
                elif kind == "aab":
                    required_members = ("BundleConfig.pb", "base/manifest/AndroidManifest.xml")
                elif kind == "aar":
                    required_members = ("AndroidManifest.xml", "classes.jar")
                for member in required_members:
                    present = member in names or any(name.endswith("/" + member) for name in names)
                    _verification_check(checks, f"member:{member}", "passed" if present else "failed", "present" if present else "missing")
                root_module = _root_module_required_by_name(lower) or "module.prop" in names
                if root_module:
                    _require_root_module_metadata(path, archive, names, infos, checks)
        elif lower.endswith((".tar.gz", ".tgz", ".crate")):
            _tar_archive_integrity(path)
            _verification_check(checks, "archive_members_safe", "passed")
            _verification_check(checks, "archive_integrity", "passed")
        elif lower.endswith(".deb"):
            if path.read_bytes()[:8] != b"!<arch>\n":
                raise RuntimeError("invalid Debian/ar archive magic")
            _verification_check(checks, "archive_integrity", "passed", "Debian ar container")
            dpkg_deb = shutil.which("dpkg-deb")
            if dpkg_deb:
                probe = subprocess.run([dpkg_deb, "--info", str(path)], capture_output=True, text=True, check=False, timeout=15)
                _verification_check(checks, "dpkg_metadata", "passed" if probe.returncode == 0 else "failed", (probe.stderr or probe.stdout).strip()[-500:])
        elif lower.endswith(".rpm"):
            if path.read_bytes()[:4] != bytes.fromhex("edabeedb"):
                raise RuntimeError("invalid RPM package magic")
            _verification_check(checks, "archive_integrity", "passed", "RPM lead magic")
    except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        _verification_check(checks, "archive_integrity", "failed", str(exc))

    if kind == "apk":
        signer = _apksigner()
        if signer:
            probe = subprocess.run([signer, "verify", "--verbose", str(path)], capture_output=True, text=True, check=False, timeout=30)
            detail = (probe.stderr or probe.stdout).strip()[-1000:]
            _verification_check(checks, "apk_signature", "passed" if probe.returncode == 0 else "failed", detail)
        elif require_apk_signature:
            _verification_check(checks, "apk_signature", "failed", "apksigner is required but unavailable")
        else:
            _verification_check(checks, "apk_signature", "skipped", "apksigner unavailable")

    status = "failed" if any(item["status"] == "failed" for item in checks) else "passed"
    return {"status": status, "checks": checks}


def artifact_producer(path: Path, *, kind: str) -> str:
    lower = path.name.lower()
    if kind == "source":
        return "tools/build-release.py"
    if kind in {"wheel", "sdist"}:
        return "python-build"
    if "root-bridge" in lower:
        return "build-root-bridge-module.sh"
    if kind == "deb":
        return "desktop-package"
    if kind in {"apk", "aab", "aar"}:
        return "android-package"
    return "release-artifact-discovery"


def artifact_record(path: Path, *, kind: str | None = None, require_apk_signature: bool = False) -> dict[str, object]:
    resolved_kind = kind or artifact_kind(path)
    return {
        "path": path.name,
        "kind": resolved_kind,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "verification": verify_release_artifact(path, require_apk_signature=require_apk_signature),
        "provenance": {"producer": artifact_producer(path, kind=resolved_kind)},
    }


def discover_release_artifacts(
    out_dir: Path,
    *,
    exclude: set[str] | None = None,
    require_apk_signature: bool = False,
    current_artifacts_manifest: Path | None = None,
) -> list[dict[str, object]]:
    excluded = set(exclude or set()) | GENERATED_RELEASE_FILES
    current_names = _current_artifact_names(out_dir, current_artifacts_manifest)
    records: list[dict[str, object]] = []
    for name in sorted(current_names):
        if name in excluded or name.startswith("."):
            continue
        path = out_dir / name
        if not path.is_file():
            raise RuntimeError(f"Current release artifact disappeared during discovery: {name}")
        lower = path.name.lower()
        if not any(lower.endswith(suffix) for suffix in RELEASE_ARTIFACT_SUFFIXES):
            raise RuntimeError(f"Current release artifact has unsupported suffix: {name}")
        records.append(artifact_record(path, require_apk_signature=require_apk_signature))
    return records


def verify_release_manifest(out_dir: Path, manifest: dict[str, object]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("Release manifest must list at least one artifact")
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError("Release manifest artifact entry must be an object")
        name = str(item.get("path") or "")
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise RuntimeError(f"Unsafe artifact name in release manifest: {name!r}")
        path = out_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"Release artifact is missing or empty: {path}")
        actual = sha256_file(path)
        if actual != item.get("sha256"):
            raise RuntimeError(f"Release artifact checksum mismatch for {name}: {actual} != {item.get('sha256')}")
        verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
        if verification.get("status") == "failed":
            raise RuntimeError(f"Release artifact verification failed for {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Devtool release artifacts.")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--verify-reproducible", action="store_true")
    parser.add_argument("--require-apk-signature", action="store_true", help="Fail if an APK cannot be verified with apksigner")
    parser.add_argument("--current-artifacts-manifest", type=Path, help="JSON manifest naming release artifacts produced by the current candidate build")
    args = parser.parse_args()

    root = (args.root or repository_root()).resolve()
    errors = validate_version_consistency(root)
    if errors:
        raise SystemExit("Version consistency failed:\n- " + "\n- ".join(errors))
    versions = load_versions(root)
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    epoch = source_date_epoch(root)
    archive = out_dir / f"devtool-{versions['devtool']}-source.zip"
    artifact = build_source_archive(root, archive, epoch=epoch)
    artifact["kind"] = "source"
    artifact["verification"] = verify_release_artifact(archive, require_apk_signature=args.require_apk_signature)
    artifact["provenance"] = {"producer": "tools/build-release.py"}

    if args.verify_reproducible:
        comparison = out_dir / f".{archive.name}.reproducibility-check"
        try:
            build_source_archive(root, comparison, epoch=epoch)
            if sha256_file(comparison) != artifact["sha256"]:
                raise SystemExit("Deterministic source archive check failed")
        finally:
            comparison.unlink(missing_ok=True)

    head = git_head(root)
    artifacts = [
        artifact,
        *discover_release_artifacts(
            out_dir, exclude={archive.name}, require_apk_signature=args.require_apk_signature,
            current_artifacts_manifest=(args.current_artifacts_manifest.resolve() if args.current_artifacts_manifest else None),
        ),
    ]
    for item in artifacts:
        provenance = item.setdefault("provenance", {})
        if isinstance(provenance, dict):
            provenance.update({"git_head": head, "source_date_epoch": epoch})

    manifest = {
        "schema_version": 2,
        "release": versions["release"],
        "versions": versions,
        "hash_algorithm": "sha256",
        "source_date_epoch": epoch,
        "generated_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "git_head": head,
        "source_tree_sha256": tree_hash(root),
        "artifacts": artifacts,
    }
    verify_release_manifest(out_dir, manifest)
    manifest_path = out_dir / "release-artifacts.json"
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    checksum_text = "".join(
        f"{item['sha256']}  {item['path']}\n" for item in manifest["artifacts"]
    )
    checksums = out_dir / "SHA256SUMS"
    checksums_all = out_dir / "SHA256SUMS.all"
    atomic_write_text(checksums, checksum_text)
    atomic_write_text(checksums_all, checksum_text)
    print(archive)
    print(manifest_path)
    print(checksums)
    print(checksums_all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
