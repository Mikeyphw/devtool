from __future__ import annotations

"""Verification and metadata extraction for Android/Gradle artifacts."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable
import zipfile

from .android_build import discover_artifacts, artifact_variant
from .diagnostics import relativize
from .models import Issue, ProjectConfig


@dataclass(slots=True)
class AndroidArtifactRecord:
    path: str
    type: str
    variant: str = ""
    module: str = ""
    size: int = 0
    sha256: str = ""
    valid: bool = True
    package_id: str = ""
    version_code: str = ""
    version_name: str = ""
    min_sdk: str = ""
    target_sdk: str = ""
    launchable_activity: str = ""
    signed: bool | None = None
    abis: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tool_from_sdk(name: str) -> str | None:
    direct = shutil.which(name)
    if direct:
        return direct
    sdk_text = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or ""
    if not sdk_text:
        return None
    build_tools = Path(sdk_text).expanduser() / "build-tools"
    if not build_tools.is_dir():
        return None
    versions = sorted((item for item in build_tools.iterdir() if item.is_dir()), reverse=True)
    for version in versions:
        candidate = version / name
        if candidate.is_file():
            return str(candidate)
    return None


def _run_tool(argv: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout + "\n" + result.stderr).strip()


def _check(record: AndroidArtifactRecord, name: str, status: str, detail: str = "") -> None:
    record.checks.append({"name": name, "status": status, "detail": detail})
    if status == "failed":
        record.valid = False


def _zip_check(record: AndroidArtifactRecord, path: Path, *, required: Iterable[str] = ()) -> list[str]:
    names: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt:
                _check(record, "zip_integrity", "failed", f"Corrupt member: {corrupt}")
            else:
                _check(record, "zip_integrity", "passed")
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        _check(record, "zip_integrity", "failed", str(exc))
        return []
    for member in required:
        found = any(name == member or name.endswith("/" + member) for name in names)
        _check(record, f"member:{member}", "passed" if found else "failed", "present" if found else "missing")
    return names


def _parse_badging(record: AndroidArtifactRecord, text: str) -> None:
    package = re.search(r"package:\s+name='([^']+)'(?:\s+versionCode='([^']*)')?(?:\s+versionName='([^']*)')?", text)
    if package:
        record.package_id = package.group(1) or ""
        record.version_code = package.group(2) or ""
        record.version_name = package.group(3) or ""
    minimum = re.search(r"sdkVersion:'([^']+)'", text)
    target = re.search(r"targetSdkVersion:'([^']+)'", text)
    launch = re.search(r"launchable-activity:\s+name='([^']+)'", text)
    native = re.search(r"native-code:\s+(.+)", text)
    record.min_sdk = minimum.group(1) if minimum else ""
    record.target_sdk = target.group(1) if target else ""
    record.launchable_activity = launch.group(1) if launch else ""
    if native:
        record.abis = re.findall(r"'([^']+)'", native.group(1))


def verify_apk(path: Path, record: AndroidArtifactRecord) -> None:
    names = _zip_check(record, path, required=("AndroidManifest.xml",))
    aapt2 = _tool_from_sdk("aapt2") or _tool_from_sdk("aapt")
    if aapt2:
        rc, output = _run_tool([aapt2, "dump", "badging", str(path)])
        if rc == 0:
            _check(record, "aapt2_badging", "passed")
            _parse_badging(record, output)
        else:
            _check(record, "aapt2_badging", "failed", output.splitlines()[-1] if output else f"exit {rc}")
    else:
        _check(record, "aapt2_badging", "skipped", "aapt2/aapt not found")

    apksigner = _tool_from_sdk("apksigner")
    if apksigner:
        rc, output = _run_tool([apksigner, "verify", "--verbose", str(path)])
        record.signed = rc == 0
        _check(record, "signature", "passed" if rc == 0 else "failed", output.splitlines()[-1] if output else "")
    else:
        record.signed = None
        # JAR signatures are not authoritative for APK Signature Scheme v2+, so
        # report only the observable hint and keep the check non-fatal.
        jar_signed = any(name.upper().startswith("META-INF/") and name.upper().endswith((".RSA", ".DSA", ".EC")) for name in names)
        _check(record, "signature", "skipped", "apksigner not found" + ("; JAR signature entries present" if jar_signed else ""))

    if "release" in path.name.lower() and "unsigned" in path.name.lower():
        _check(record, "release_signing", "failed", "Release artifact name indicates an unsigned APK")


def verify_aab(path: Path, record: AndroidArtifactRecord) -> None:
    _zip_check(record, path, required=("BundleConfig.pb", "base/manifest/AndroidManifest.xml"))


def verify_aar(path: Path, record: AndroidArtifactRecord) -> None:
    _zip_check(record, path, required=("AndroidManifest.xml", "classes.jar"))


def verify_jar(path: Path, record: AndroidArtifactRecord) -> None:
    _zip_check(record, path)


def verify_android_artifact(path: Path, *, repo_root: Path, project_root: Path) -> tuple[AndroidArtifactRecord, list[Issue]]:
    path = path.resolve()
    suffix = path.suffix.lower().lstrip(".") or "file"
    record = AndroidArtifactRecord(
        path=relativize(repo_root, str(path)),
        type=suffix,
        variant=artifact_variant(path),
        size=path.stat().st_size if path.exists() else 0,
        sha256=_sha256(path) if path.is_file() else "",
    )
    try:
        relative = path.relative_to(project_root)
        parts = relative.parts
        record.module = ":".join(parts[:parts.index("build")]) if "build" in parts else ""
    except (ValueError, OSError):
        record.module = ""

    if not path.is_file():
        _check(record, "exists", "failed", "Artifact does not exist")
    elif record.size <= 0:
        _check(record, "non_empty", "failed", "Artifact is empty")
    else:
        _check(record, "exists", "passed")
        _check(record, "non_empty", "passed")
        if suffix == "apk":
            verify_apk(path, record)
        elif suffix == "aab":
            verify_aab(path, record)
        elif suffix == "aar":
            verify_aar(path, record)
        elif suffix in {"jar", "war", "zip"}:
            verify_jar(path, record)
        else:
            _check(record, "format", "skipped", f"No verifier for .{suffix}")

    issues: list[Issue] = []
    for check in record.checks:
        if check["status"] != "failed":
            continue
        issues.append(Issue(
            "error",
            "artifact-verify",
            record.path,
            str(check["name"]).upper().replace(":", "_"),
            str(check.get("detail") or "Artifact verification failed"),
        ))
    return record, issues


def _configured_output_dir(config: ProjectConfig) -> Path | None:
    options = config.runner_options.get("artifacts", {})
    options = dict(options) if isinstance(options, dict) else {}
    value = str(options.get("output_directory") or options.get("output_dir") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve()


def collect_and_verify_android_artifacts(
    config: ProjectConfig,
    *,
    project_root: Path,
    started_at: float | None,
) -> tuple[dict[str, Any], list[Issue]]:
    since = float(started_at or 0.0)
    discovered = discover_artifacts(project_root, since)
    output_dir = _configured_output_dir(config)
    if output_dir and output_dir.is_dir():
        for pattern in ("*.apk", "*.aab", "*.aar", "*.jar", "*.war", "*.zip"):
            for path in output_dir.glob(pattern):
                try:
                    if path.stat().st_mtime + 2.0 >= since:
                        discovered.append(path.resolve())
                except OSError:
                    continue
    unique = sorted({path.resolve() for path in discovered if path.is_file()})
    records: list[AndroidArtifactRecord] = []
    issues: list[Issue] = []
    for path in unique:
        record, record_issues = verify_android_artifact(
            path,
            repo_root=config.repo_root,
            project_root=project_root,
        )
        records.append(record)
        issues.extend(record_issues)

    required = bool((config.runner_options.get("artifacts") or {}).get("required", config.kind == "android")) if isinstance(config.runner_options.get("artifacts", {}), dict) else config.kind == "android"
    if required and not records:
        issues.append(Issue(
            "error",
            "artifact-verify",
            relativize(config.repo_root, str(project_root)),
            "ARTIFACT_MISSING",
            "Build completed without producing a new standard Android/Gradle artifact.",
        ))
    metadata = {
        "count": len(records),
        "valid": sum(1 for item in records if item.valid),
        "invalid": sum(1 for item in records if not item.valid),
        "required": required,
        "items": [item.as_dict() for item in records],
    }
    return metadata, issues


def write_artifact_report(path: Path, metadata: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
