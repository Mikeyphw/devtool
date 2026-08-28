#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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
                names = set(archive.namelist())
            _verification_check(checks, "archive_integrity", "passed")
            required_members: tuple[str, ...] = ()
            if kind == "apk":
                required_members = ("AndroidManifest.xml",)
            elif kind == "aab":
                required_members = ("BundleConfig.pb", "base/manifest/AndroidManifest.xml")
            elif kind == "aar":
                required_members = ("AndroidManifest.xml", "classes.jar")
            elif "root-bridge" in lower:
                required_members = ("module.prop", "customize.sh", "bin/devtool-root-bridge")
            for member in required_members:
                present = member in names or any(name.endswith("/" + member) for name in names)
                _verification_check(checks, f"member:{member}", "passed" if present else "failed", "present" if present else "missing")
        elif lower.endswith((".tar.gz", ".tgz", ".crate")):
            with tarfile.open(path, "r:*") as archive:
                for _ in archive:
                    pass
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
) -> list[dict[str, object]]:
    excluded = set(exclude or set()) | {"release-artifacts.json", "SHA256SUMS", "SHA256SUMS.all"}
    records: list[dict[str, object]] = []
    for path in sorted(out_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name in excluded or path.name.startswith("."):
            continue
        lower = path.name.lower()
        if not any(lower.endswith(suffix) for suffix in RELEASE_ARTIFACT_SUFFIXES):
            continue
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
            out_dir, exclude={archive.name}, require_apk_signature=args.require_apk_signature
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
