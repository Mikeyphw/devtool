"""ACTIONS-05: reproducible Android CI dispatch and inspected APK/AAB acquisition.

Never infer that a GitHub artifact digest proves an Android package or a release.
Builds run only through a checked-in workflow_dispatch workflow on GitHub.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any

from .actions_artifacts import download_artifact, verify_download
from .actions_dispatch import dispatch_workflow
from .actions_runs import _normalize_run
from .artifact_registry import register_paths
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug

_MODULE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*){0,7}\Z")
_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_SHA40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_CERT = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAX_PACKAGE = 256 * 1024 * 1024
_WORKFLOW = "devtool-android-build.yml"


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError("Cannot inspect local Git state for Android build") from None
    if proc.returncode:
        raise GitHubActionsError("Cannot inspect local Git state for Android build")
    return proc.stdout.strip()


def _project_root(root: Path, project_dir: str = "") -> tuple[Path, str]:
    base = root.resolve()
    if project_dir:
        if project_dir == ".":
            return base, "."
        if (project_dir.startswith("/") or "\\" in project_dir or
            any(part in ("", ".", "..") for part in project_dir.split("/")) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", project_dir)):
            raise GitHubActionsError("Android project directory must be a safe repository-relative path")
        candidate = root / project_dir
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(base):
            raise GitHubActionsError("Android project directory escapes repository")
        return candidate, project_dir
    found = []
    # Small bounded discovery; never walk dependency/build directories or symlink dirs.
    skipped = {".git", ".gradle", "build", "node_modules", ".devtool", "vendor", "dist"}
    for dirpath, dirs, files in os.walk(base, followlinks=False):
        relative = Path(dirpath).relative_to(base)
        if len(relative.parts) > 5:
            dirs.clear()
            continue
        dirs[:] = sorted(d for d in dirs if d not in skipped and not (Path(dirpath)/d).is_symlink())
        if "settings.gradle" in files or "settings.gradle.kts" in files:
            found.append(Path(dirpath))
            dirs.clear()
        if len(found) > 1:
            break
    if len(found) != 1:
        raise GitHubActionsError("Cannot uniquely identify Android Gradle project; use --project-dir")
    project = found[0]
    return project, project.relative_to(base).as_posix() if project != base else "."


def _gradle_modules(root: Path) -> dict[str, dict[str, Any]]:
    settings = next((root / f for f in ("settings.gradle.kts", "settings.gradle") if (root / f).is_file()), None)
    if settings is None:
        raise GitHubActionsError("No settings.gradle(.kts) in selected Android repository")
    text = settings.read_text(encoding="utf-8")
    # Accept the literal include forms supported by Gradle Kotlin/Groovy DSL.
    # Dynamic includes, projectDir relocation and conditional includes require explicit intervention.
    names = set(re.findall(r"[\"'](:[A-Za-z0-9_:-]+)[\"']", "\n".join(
        line for line in text.splitlines() if re.search(r"\binclude\s*\(?", line))))
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        module = name.lstrip(":")
        if not _MODULE.fullmatch(module):
            continue
        directory = root.joinpath(*module.split(":"))
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            continue
        scripts = [directory / f for f in ("build.gradle.kts", "build.gradle") if (directory / f).is_file()]
        if not scripts:
            continue
        gradle = scripts[0].read_text(encoding="utf-8")
        manifest = directory / "src/main/AndroidManifest.xml"
        is_app = bool(re.search(r"com\.android\.application", gradle))
        if not is_app and "alias(libs.plugins." in gradle:
            # Resolve catalog alias to its declared id rather than guessing what
            # a similarly named alias points to.
            catalog = root / "gradle/libs.versions.toml"
            if catalog.is_file():
                try:
                    import tomllib
                    plugins = tomllib.loads(catalog.read_text(encoding="utf-8")).get("plugins", {})
                    for m in re.finditer(r"alias\(libs\.plugins\.([A-Za-z0-9_.]+)\)", gradle):
                        definition = plugins.get(m.group(1).replace(".", "-"))
                        plugin_id = definition if isinstance(definition, str) else (definition or {}).get("id")
                        if plugin_id == "com.android.application":
                            is_app = True
                except (OSError, ValueError, TypeError):
                    pass
        # Version catalogs may use an alias; require an app manifest, and avoid claiming
        # certainty about the plugin when the source does not name it literally.
        if not is_app:
            continue
        p = re.search(r"\bapplicationId\s*(?:=|\s)\s*[\"']([A-Za-z_][\w.]*)[\"']", gradle)
        pkg = p.group(1) if p and _PACKAGE.fullmatch(p.group(1)) else None
        result[module] = {"module": module, "gradle_file": str(scripts[0].relative_to(root)),
                          "manifest_present": manifest.is_file(), "application_id": pkg,
                          "application_id_static": pkg is not None}
    return result


def android_discover(root: Path, project_dir: str = "") -> dict[str, Any]:
    project, project_relative = _project_root(root, project_dir)
    return {"phase": "ACTIONS-05", "repo_root": str(root.resolve()),
            "project_dir":project_relative, "modules": list(_gradle_modules(project).values()),
            "note": "Static Gradle inspection only; dynamic Gradle variants/IDs need runtime verification"}


def _gradle_bootstrap(project: Path) -> dict[str, str]:
    """Inspect a checked-in Gradle launcher; do not execute it during planning.

    Accept an ordinary Gradle wrapper or a tightly bounded, checksum-pinned
    Devtool-style Gradle bootstrap. This is source inspection, not a sandbox:
    GitHub Actions will execute the selected commit's own launcher.
    """
    launcher = project / "gradlew"
    if not launcher.is_file() or launcher.is_symlink():
        raise GitHubActionsError("Android project has no regular checked-in gradlew launcher")
    properties = project / "gradle/wrapper/gradle-wrapper.properties"
    if properties.is_file() and not properties.is_symlink():
        return {"mode": "gradle-wrapper", "version": "", "checksum": ""}
    if launcher.stat().st_size > 64 * 1024:
        raise GitHubActionsError("Nonstandard Gradle launcher exceeds inspection limit")
    try:
        source = launcher.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise GitHubActionsError("Cannot inspect Gradle bootstrap launcher") from None
    # Deliberately require a pinned distribution checksum and a verification
    # call on the download path. A generic curl | sh launcher is not accepted.
    version = re.search(r"(?m)^VERSION=([0-9]+(?:\.[0-9]+){1,2})\s*$", source)
    digest = re.search(r"(?m)^SHA256=([0-9a-fA-F]{64})\s*$", source)
    markers = (
        'https://services.gradle.org/distributions/gradle-$VERSION-bin.zip',
        'verify_zip', 'sha256sum', 'bin/gradle', 'exec '
    )
    if (not version or not digest or
        not all(x in source for x in markers) or
        not re.search(r"(?m)^\s*verify_zip\s*$", source) or
        not re.search(r'(?m)^\s*if \[ \"\$actual\" != \"\$SHA256\" \]; then', source)):
        raise GitHubActionsError(
            "Android Gradle launcher has no standard wrapper or recognized checksum-pinned bootstrap"
        )
    return {"mode": "checksum-bootstrap", "version": version.group(1),
            "checksum": digest.group(1).lower()}


def android_plan(client: GitHubActionsClient, slug: str, root: Path, *, module: str,
                 variant: str, fmt: str, ref: str, workflow: str = _WORKFLOW,
                 execute: bool = False, expected_package: str = "", project_dir: str = "") -> dict[str, Any]:
    slug = _slug(slug)
    if not _MODULE.fullmatch(module) or variant not in ("debug", "release") or fmt not in ("apk", "aab"):
        raise GitHubActionsError("Android module, variant, or output format is invalid")
    if expected_package and not _PACKAGE.fullmatch(expected_package):
        raise GitHubActionsError("Expected Android application ID is invalid")
    project, project_relative = _project_root(root, project_dir)
    modules = _gradle_modules(project)
    if module not in modules:
        raise GitHubActionsError("Requested Android application module is not statically discoverable")
    gradle_bootstrap = _gradle_bootstrap(project)
    if not ref:
        raise GitHubActionsError("Android CI requires an explicit --ref branch or tag")
    local_sha = _git(root, "rev-parse", "HEAD").lower()
    if not _SHA40.fullmatch(local_sha):
        raise GitHubActionsError("Local Android source commit is invalid")
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise GitHubActionsError("Android CI requires a clean local Git checkout; commit and push changes first")
    inputs = {"module":module, "variant":variant, "format":fmt, "project_dir":project_relative}
    remote_plan = dispatch_workflow(client, slug, workflow, ref, inputs, execute=False)
    if remote_plan.get("pre_dispatch_sha", "").lower() != local_sha:
        raise GitHubActionsError("Local HEAD differs from GitHub selected ref; push the commit or select the correct ref")
    package = expected_package or modules[module]["application_id"]
    result = {"phase":"ACTIONS-05", "repository":slug, "module":module,
              "variant":variant, "format":fmt,"project_dir":project_relative,"gradle_bootstrap":gradle_bootstrap,"expected_package":package,
              "expected_package_source": "explicit" if expected_package else ("static-gradle" if package else "unresolved"),
              "local_head_sha": local_sha, "workflow":workflow,"ref":ref,
              "workflow_plan":remote_plan, "dispatched":False, "qualified_release":False}
    if not execute:
        return result
    # The ACTIONS-02 dispatch journal is created before the mutating POST.
    dispatched = dispatch_workflow(client, slug, workflow, ref, inputs, execute=True)
    result["dispatch"] = dispatched
    result["dispatched"] = True
    if dispatched["pre_dispatch_sha"].lower() != local_sha:
        result["warning"] = "Remote Git ref moved between validation and dispatch; build source is not confirmed"
    return result


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk and name in ("apksigner", "aapt", "aapt2", "zipalign"):
        build_tools = Path(sdk) / "build-tools"
        if build_tools.is_dir():
            candidates = sorted(build_tools.glob(f"*/{name}"), reverse=True)
            if candidates:
                return str(candidates[0])
    raise GitHubActionsError(f"Android verification requires {name}; no unchecked fallback is allowed")


def _run(args: list[str], *, purpose: str, timeout: int = 45) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError(f"{purpose} tool unavailable or timed out") from None
    if proc.returncode:
        raise GitHubActionsError(f"{purpose} failed; cannot trust Android package")
    return proc.stdout + "\n" + proc.stderr


def _fingerprint(text: str, *, source: str = "apksigner") -> str:
    """Parse a verified tool's public certificate SHA-256, never a guessed hash.

    Android tools have used ``SHA-256``/``SHA256`` and sometimes emit colon-
    delimited bytes.  Only certificate fields are authoritative.  Multiple
    signers/digests and malformed fields remain fail-closed.  Never include the
    untrusted tool output in errors (it could contain private diagnostics).
    """
    if source not in ("apksigner", "keytool"):
        raise GitHubActionsError("Unrecognized certificate inspection source")
    apksigner_field = re.compile(
        r"^(?:Signer\s*#?\s*(\d+)\s+)?certificate\s+SHA[- ]?256"
        r"(?:\s+(?:digest|fingerprint))?\s*:\s*(.*?)\s*$", re.I)
    keytool_field = re.compile(r"^(?:SHA[- ]?256)\s*:\s*(.*?)\s*$", re.I)
    seen_signers: set[str] = set()
    if source == "apksigner":
        for number in re.findall(r"(?im)^\s*Signer\s*#?\s*(\d+)\s+certificate\b", text):
            seen_signers.add(number)
        if any(number != "1" for number in seen_signers):
            raise GitHubActionsError("Signing certificate fingerprint could not be verified (multiple-signers)")
    digests: set[str] = set()
    matched_field = False
    for raw in text.splitlines():
        m = (apksigner_field if source == "apksigner" else keytool_field).fullmatch(raw.strip())
        if m is None:
            continue
        matched_field = True
        value = m.group(2) if source == "apksigner" else m.group(1)
        if source == "apksigner" and m.group(1) not in (None, "1"):
            raise GitHubActionsError("Signing certificate fingerprint could not be verified (multiple-signers)")
        # Accept precisely 32 hex byte pairs, either contiguous, colon- or
        # space-separated.  Never accept a 64-character prefix of a bad value.
        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            digest = value.lower()
        elif re.fullmatch(r"[0-9a-fA-F]{2}(?:(?::|\s+)[0-9a-fA-F]{2}){31}", value):
            digest = re.sub(r"[:\s]", "", value).lower()
        else:
            raise GitHubActionsError("Signing certificate fingerprint could not be verified (malformed-sha256)")
        digests.add(digest)
    if len(digests) > 1:
        raise GitHubActionsError("Signing certificate fingerprint could not be verified (conflicting-digests)")
    if len(digests) == 1:
        return next(iter(digests))
    reason = "unrecognized-sha256-field" if matched_field else "missing-sha256-field"
    raise GitHubActionsError(f"Signing certificate fingerprint could not be verified ({reason})")


def _inspect_apk(package_file: Path) -> dict[str, Any]:
    signer = _run([_tool("apksigner"), "verify", "--print-certs", str(package_file)], purpose="APK signature verification")
    try:
        aapt = _tool("aapt")
    except GitHubActionsError:
        aapt = _tool("aapt2")
    dump = _run([aapt, "dump", "badging", str(package_file)], purpose="APK manifest inspection")
    match = re.search(r"^package:\s+name='([^']+)'\s+versionCode='([^']+)'\s+versionName='([^']*)'", dump, re.M)
    if not match or not _PACKAGE.fullmatch(match.group(1)):
        raise GitHubActionsError("APK package metadata could not be verified")
    return {"package":match.group(1),"version_code":match.group(2),
            "version_name":match.group(3), "signer_sha256":_fingerprint(signer),
            "debug_signing": bool(re.search(r"\bCN\s*=\s*Android Debug\b", signer, re.I)),
            "signature_verified":True, "manifest_verified":True}


def _inspect_aab(package_file: Path) -> dict[str, Any]:
    jar = os.environ.get("DEVTOOL_BUNDLETOOL_JAR", "")
    if not jar or not Path(jar).is_file():
        raise GitHubActionsError("AAB verification requires DEVTOOL_BUNDLETOOL_JAR pointing to bundletool.jar")
    java = _tool("java")
    _run([java, "-jar", jar, "validate", "--bundle", str(package_file)], purpose="AAB bundletool validation")
    dump = _run([java, "-jar", jar, "dump", "manifest", "--bundle", str(package_file),
                 "--xpath", "/manifest/@package"], purpose="AAB manifest inspection")
    pkg = dump.strip().strip('"').strip("'")
    if not _PACKAGE.fullmatch(pkg):
        raise GitHubActionsError("AAB application ID is not verified")
    _run([_tool("jarsigner"), "-verify", "-strict", str(package_file)], purpose="AAB JAR signature verification")
    cert = _run([_tool("keytool"), "-printcert", "-jarfile", str(package_file)], purpose="AAB signing identity inspection")
    code_text = _run([java, "-jar", jar, "dump", "manifest", "--bundle", str(package_file),
                      "--xpath", "/manifest/@android:versionCode"], purpose="AAB versionCode inspection").strip()
    if not code_text.isdecimal() or int(code_text) < 1:
        raise GitHubActionsError("AAB versionCode is not verified")
    return {"package":pkg,"version_code":code_text,"version_name":None,
            "signer_sha256":_fingerprint(cert, source="keytool"),
            "debug_signing":bool(re.search(r"\bCN\s*=\s*Android Debug\b", cert, re.I)),
            "signature_verified":True,"manifest_verified":True}


def inspect_android_zip(archive: Path, *, fmt: str, variant: str, expected_package: str = "",
                        expected_cert: str = "", member: str = "") -> dict[str, Any]:
    if fmt not in ("apk", "aab") or variant not in ("debug", "release"):
        raise GitHubActionsError("Invalid Android output format or variant")
    if expected_cert and not _CERT.fullmatch(expected_cert):
        raise GitHubActionsError("Signing certificate must be a 64-character SHA-256 hex digest")
    if variant == "release" and not expected_cert:
        raise GitHubActionsError("Release output requires --expected-cert-sha256 (stable signing identity)")
    if variant == "release" and not expected_package:
        raise GitHubActionsError("Release output requires --expected-package (stable application identity)")
    if expected_package and not _PACKAGE.fullmatch(expected_package):
        raise GitHubActionsError("Expected Android application ID is invalid")
    with zipfile.ZipFile(archive) as z:
        items = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith("." + fmt)]
        if member:
            items = [i for i in items if i.filename == member]
        if len(items) != 1:
            raise GitHubActionsError("Expected exactly one Android output; use --member to select an exact archive path")
        info = items[0]
        if info.file_size <= 0 or info.file_size > _MAX_PACKAGE:
            raise GitHubActionsError("Android package size is invalid or exceeds 256 MiB")
        with tempfile.TemporaryDirectory(prefix="devtool-android-inspect-") as folder:
            package_file = Path(folder) / ("package." + fmt)
            digest = hashlib.sha256()
            size = 0
            with z.open(info) as src, package_file.open("xb") as dst:
                os.chmod(package_file, 0o600)
                while data := src.read(1024 * 1024):
                    size += len(data)
                    if size > _MAX_PACKAGE:
                        raise GitHubActionsError("Android package exceeds extraction limit")
                    dst.write(data); digest.update(data)
                dst.flush(); os.fsync(dst.fileno())
            details = _inspect_apk(package_file) if fmt == "apk" else _inspect_aab(package_file)
            if expected_package and details["package"] != expected_package:
                raise GitHubActionsError("Android package ID differs from the expected application ID")
            if expected_cert and details["signer_sha256"] != expected_cert.lower():
                raise GitHubActionsError("Android signing certificate does not match expected stable identity")
            return {"member":info.filename,"sha256":digest.hexdigest(),"size":size,
                    "format":fmt,"variant":variant,**details,
                    "certificate_expected":bool(expected_cert),"qualified_release":False}


def android_collect(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
                    artifact_id: int, fmt: str, variant: str, expected_package: str = "",
                    expected_cert: str = "", member: str = "", download: bool = False, materialize: bool = True) -> dict[str, Any]:
    slug = _slug(slug)
    remote = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    if not remote["completed"] or remote["conclusion"] != "success":
        raise GitHubActionsError("Android collection requires a completed successful GitHub run")
    if remote.get("event") != "workflow_dispatch":
        raise GitHubActionsError("Remote Android build must originate from workflow_dispatch")
    selected_workflow = client.workflow(slug, _WORKFLOW)
    if remote.get("workflow_id") != selected_workflow["id"]:
        raise GitHubActionsError("Remote run is not the selected Devtool Android workflow")
    if download:
        downloaded = download_artifact(client, slug, run_id, artifact_id, root)
        verified = verify_download(client, slug, run_id, artifact_id, root)
    else:
        downloaded = None
        verified = verify_download(client, slug, run_id, artifact_id, root)
    details = inspect_android_zip(Path(verified["file"]), fmt=fmt, variant=variant,
                                  expected_package=expected_package,expected_cert=expected_cert,member=member)
    # Only materialize Android outputs after ZIP, remote-run and signing checks succeed.
    if not materialize:
        return {"phase":"ACTIONS-05", "repository":slug, "run_id":run_id,
                "artifact_id":artifact_id,"remote_head_sha":remote["head_sha"],
                "android":details,"archive_verified":True,"qualified_release":False}
    storage = root.resolve() / ".devtool" / "artifacts" / "android-verified"
    for parent in (root.resolve() / ".devtool", root.resolve() / ".devtool" / "artifacts", storage):
        if parent.is_symlink():
            raise GitHubActionsError("Android destination contains a symlink")
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = storage / (f"github-run{run_id}-artifact{artifact_id}-{details['sha256'][:16]}.{fmt}")
    if destination.is_symlink() or destination.exists():
        raise GitHubActionsError("Android output already materialized; refuse overwrite")
    fd, tmp_name = tempfile.mkstemp(prefix=".android-", suffix="." + fmt, dir=storage)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, zipfile.ZipFile(verified["file"]) as z, z.open(details["member"]) as src:
            os.fchmod(out.fileno(), 0o600)
            h = hashlib.sha256()
            size = 0
            while data := src.read(1024 * 1024):
                size += len(data)
                if size > _MAX_PACKAGE:
                    raise GitHubActionsError("Android output exceeds extraction limit")
                out.write(data); h.update(data)
            out.flush(); os.fsync(out.fileno())
        if h.hexdigest() != details["sha256"] or size != details["size"]:
            raise GitHubActionsError("Android output changed since signing inspection")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    metadata = {"producer":"github-actions", "state":"android-inspected-unqualified",
                "remote_repository":slug,"remote_run_id":run_id,"remote_run_attempt":remote["run_attempt"],
                "remote_workflow_id":remote["workflow_id"],"remote_head_sha":remote["head_sha"],
                "remote_artifact_id":artifact_id,"package":details["package"],
                "format":fmt,"variant":variant,"signer_sha256":details["signer_sha256"],
                "package_sha256":details["sha256"],"signature_verified":True,
                "qualified_release":False, "release_gate":"not-implemented"}
    records = register_paths(root, [destination], metadata=metadata)
    if not records:
        raise GitHubActionsError("Android package materialized but artifact registry did not accept it")
    payload = {"phase":"ACTIONS-05","repository":slug,"run_id":run_id,
               "artifact_id":artifact_id,"remote_head_sha":remote["head_sha"],
               "remote_attempt":remote["run_attempt"],"archive_verified":True,
               "android":details, "package_file":str(destination), "registry_id":records[0]["id"],
               "qualified_release":False,"release_gate":"not-implemented"}
    if downloaded:
        payload["download"] = downloaded
    return payload

# ACTIONS-10: retained V3 integration included in repair commit.
