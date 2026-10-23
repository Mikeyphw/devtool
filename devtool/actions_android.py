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
_WORKFLOW_NAME = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.ya?ml|\.github/workflows/[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.ya?ml)\Z")


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


def _gradle_modules(root: Path, *, discovery_mode: str = 'static', repository: Path | None = None) -> dict[str, dict[str, Any]]:
    if discovery_mode == 'static':
        from .actions_android_model import discover_modules
        return discover_modules(root)
    if discovery_mode == 'gradle':
        from .actions_android_runtime import discover_runtime
        return discover_runtime(root, repository=repository or root)
    raise GitHubActionsError('Android discovery mode must be static or explicitly reviewed gradle')


def android_discover(root: Path, project_dir: str = "", *, discovery_mode: str = "static") -> dict[str, Any]:
    project, project_relative = _project_root(root, project_dir)
    return {"phase": "ACTIONS-05", "repo_root": str(root.resolve()),
            "project_dir":project_relative, "modules": list(_gradle_modules(project, discovery_mode=discovery_mode, repository=root).values()),
            "discovery_mode":discovery_mode,"note": "Gradle runtime executes explicitly reviewed repository code, never a sandbox" if discovery_mode=="gradle" else "Static Gradle inspection only; use explicit runtime discovery for evaluated convention plugins"}


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


def _reviewed_variant_override(root: Path, module: str, variant: str) -> dict[str, str] | None:
    """Project-owned canonical Android variant profile for static discovery gaps.

    Profile selection is *exact* and never infers an unreviewed Gradle task.
    The canonical config document and android/variants.py resolver own policy.
    """
    from .config import load_repository_document
    from .models import ProjectConfig
    from .android.variants import resolve_android_variant
    path = root / '.devtool.toml'
    if not path.is_file() or path.is_symlink():
        return None
    try:
        _git(root, 'ls-files', '--error-unmatch', '.devtool.toml')
    except GitHubActionsError:
        raise GitHubActionsError('Reviewed Android variant profile must be a tracked repository document') from None
    try:
        doc = load_repository_document(path, required=True)
    except (ValueError, OSError, SystemExit) as exc:
        raise GitHubActionsError('Cannot load reviewed Android variant profile') from exc
    android = doc.get('android', {})
    if not isinstance(android, dict):
        raise GitHubActionsError('Android variant profiles must be a TOML table')
    # The global Android table is the canonical profile namespace. Target-local
    # variant policy is merged only when the module is the selected target.
    targets = doc.get('targets', {})
    target = targets.get(module, {}) if isinstance(targets, dict) else {}
    if isinstance(target, dict):
        entry = target.get('variants', {}).get(variant) if isinstance(target.get('variants', {}),dict) else None
        if isinstance(entry, dict) and isinstance(entry.get('android'), dict):
            android = {**android, **entry['android']}
            android['variants'] = {**(android.get('variants', {}) if isinstance(android.get('variants'),dict) else {}), variant:entry['android']}
    profiles = android.get('variants', android.get('variant_profiles', {}))
    if not isinstance(profiles, dict) or variant not in profiles or not isinstance(profiles[variant], dict):
        return None
    selected = profiles[variant]
    if not {'build_type', 'flavor', 'module'} <= selected.keys():
        raise GitHubActionsError('Explicit ACTIONS-05 variant override requires build_type, flavor and module')
    profile = resolve_android_variant(ProjectConfig(repo_root=root,kind='android',variant=variant,
                              runner_options={'android':android}), variant)
    if (profile.module != module or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,40}',profile.build_type) or
        not re.fullmatch(r'[a-z][A-Za-z0-9]{0,40}',profile.flavor) or
        variant != profile.flavor + profile.build_type.title()):
        raise GitHubActionsError('Reviewed variant override mismatches module, flavor or build type')
    return {'build_type':profile.build_type, 'flavor':profile.flavor}


def android_plan(client: GitHubActionsClient, slug: str, root: Path, *, module: str,
                 variant: str, fmt: str, ref: str, workflow: str = _WORKFLOW,
                 execute: bool = False, expected_package: str = "", project_dir: str = "",
                 signing_mode: str = "local", discovery_mode: str = "static") -> dict[str, Any]:
    slug = _slug(slug)
    if not _MODULE.fullmatch(module) or not re.fullmatch(r"[a-z][A-Za-z0-9]{0,60}", variant) or fmt not in ("apk", "aab"):
        raise GitHubActionsError("Android module, variant, or output format is invalid")
    if expected_package and not _PACKAGE.fullmatch(expected_package):
        raise GitHubActionsError("Expected Android application ID is invalid")
    if signing_mode not in ('local','github'):
        raise GitHubActionsError('Android signing mode must be local or github')
    project, project_relative = _project_root(root, project_dir)
    modules = _gradle_modules(project, discovery_mode=discovery_mode, repository=root)
    if module not in modules:
        raise GitHubActionsError("Requested Android application module is not statically discoverable")
    declared = modules[module].get('variants', {'debug': {}, 'release': {}})
    spec = declared.get(variant)
    source = 'static-gradle'
    if spec is None:
        spec = _reviewed_variant_override(root, module, variant)
        if spec is None:
            raise GitHubActionsError('Unknown or unreviewed Android variant; register its model/profile')
        source = 'reviewed-profile'
    if workflow == _WORKFLOW and (variant not in ('debug', 'release') or
                                   (spec['build_type'] != 'debug' and signing_mode == 'local')):
        workflow = 'devtool-android-variants.yml'
    if (spec['build_type'] != 'debug' and signing_mode == 'local' and
        workflow != 'devtool-android-variants.yml'):
        raise GitHubActionsError('Local signing requires unsigned-capable reviewed Android workflow')
    gradle_bootstrap = _gradle_bootstrap(project)
    if not ref:
        raise GitHubActionsError("Android CI requires an explicit --ref branch or tag")
    local_sha = _git(root, "rev-parse", "HEAD").lower()
    if not _SHA40.fullmatch(local_sha):
        raise GitHubActionsError("Local Android source commit is invalid")
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise GitHubActionsError("Android CI requires a clean local Git checkout; commit and push changes first")
    inputs = {"module":module, "variant":variant, "format":fmt, "project_dir":project_relative}
    if workflow == 'devtool-android-variants.yml':
        inputs['signing_mode'] = signing_mode
        if spec['build_type'] not in ('debug','release'):
            inputs['build_type'] = spec['build_type']
    remote_plan = dispatch_workflow(client, slug, workflow, ref, inputs, execute=False)
    if remote_plan.get("pre_dispatch_sha", "").lower() != local_sha:
        raise GitHubActionsError("Local HEAD differs from GitHub selected ref; push the commit or select the correct ref")
    package = expected_package or modules[module]["application_id"]
    result = {"phase":"ACTIONS-05", "repository":slug, "module":module,
              "variant":variant, "build_type":spec["build_type"], "flavor":spec["flavor"],
              "variant_source":source if discovery_mode=="static" else "reviewed-gradle-execution", "discovery_mode":discovery_mode, "signing_mode":signing_mode,
              "signing_state":("unsigned-candidate" if spec["build_type"]!="debug" and signing_mode=="local" else "hosted-signing-requested" if spec["build_type"]!="debug" else "debug"), "format":fmt,"project_dir":project_relative,"gradle_bootstrap":gradle_bootstrap,"expected_package":package,
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
    # The Android Build Tools apksigner 0.9 in Termux emits, for example:
    # "V3.0 Signer: certificate SHA-256 digest: ...".  Newer tools use
    # "Signer #1 certificate SHA-256 digest: ...".  Capture only explicit
    # certificate SHA-256 fields and never guess from an unrelated digest.
    apksigner_field = re.compile(
        r"^(?:(?:Signer\s*#?\s*(?P<ordinal>\d+)\s+)|"
        r"(?:V[1-4](?:\.\d+)?\s+Signer(?:\s*#?\s*(?P<scheme_ordinal>\d+))?\s*:\s*))?"
        r"certificate\s+SHA[- ]?256(?:\s+(?:digest|fingerprint))?\s*:\s*"
        r"(?P<value>.*?)\s*$", re.I)
    signer_heading = re.compile(
        r"^(?:Signer\s*#?\s*(\d+)\s+certificate\b|"
        r"V[1-4](?:\.\d+)?\s+Signer(?:\s*#?\s*(\d+))?\s*:\s*certificate\b)", re.I)
    keytool_field = re.compile(r"^(?:SHA[- ]?256)\s*:\s*(.*?)\s*$", re.I)
    if source == "apksigner":
        for raw in text.splitlines():
            heading = signer_heading.match(raw.strip())
            if heading and (heading.group(1) or heading.group(2)) not in (None, "1"):
                raise GitHubActionsError("Signing certificate fingerprint could not be verified (multiple-signers)")
        # Some versions emit an independent signer-count in verbose mode.
        if any(int(number) != 1 for number in re.findall(
                r"(?im)^\s*Number of signers\s*:\s*(\d+)\s*$", text)):
            raise GitHubActionsError("Signing certificate fingerprint could not be verified (multiple-signers)")
    digests: set[str] = set()
    matched_field = False
    for raw in text.splitlines():
        m = (apksigner_field if source == "apksigner" else keytool_field).fullmatch(raw.strip())
        if m is None:
            continue
        matched_field = True
        value = m.group("value") if source == "apksigner" else m.group(1)
        if source == "apksigner" and any(
                group not in (None, "1") for group in
                (m.group("ordinal"), m.group("scheme_ordinal"))):
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


def _apk_badging_identity(dump: str, *, role: str) -> tuple[str, str, str]:
    """One unambiguous aapt package declaration; no invented test APK version."""
    declarations = [line.strip() for line in dump.splitlines()
                    if re.match(r"^\s*package\s*:", line)]
    if len(declarations) != 1:
        raise GitHubActionsError("APK package metadata missing or ambiguous")
    line = declarations[0]
    attrs = re.findall(r"\b(name|versionCode|versionName)='([^']*)'", line)
    values = {}
    for key, value in attrs:
        if key in values:
            raise GitHubActionsError("APK package metadata has duplicate attributes")
        values[key] = value
    if (set(values) != {"name", "versionCode", "versionName"} or
            not _PACKAGE.fullmatch(values["name"])):
        raise GitHubActionsError("APK package metadata could not be verified")
    version = values["versionCode"]
    if role == "application":
        if not re.fullmatch(r"[0-9]+", version) or int(version) < 1:
            raise GitHubActionsError("Application APK requires positive numeric versionCode")
    elif version and (not re.fullmatch(r"[0-9]+", version) or int(version) < 1):
        raise GitHubActionsError("Instrumentation APK has invalid nonempty versionCode")
    return values["name"], version, values["versionName"]


def _instrumentation_xml(dump: str, *, test_package: str, target_package: str) -> str:
    """Inspect compiled manifest structure, not the aapt badging display."""
    lines = dump.splitlines()
    elements = [(i, len(line) - len(line.lstrip()),
                 re.match(r"^\s*E:\s*([A-Za-z_][\w:.-]*)\b", line))
                for i, line in enumerate(lines)]
    elements = [(i, depth, m.group(1)) for i, depth, m in elements if m]
    roots = [(i, d) for i, d, name in elements if name == 'manifest']
    insts = [(i, d) for i, d, name in elements if name == 'instrumentation']
    if (len(roots) != 1 or len(insts) != 1 or
            insts[0][0] <= roots[0][0] or insts[0][1] <= roots[0][1]):
        raise GitHubActionsError("Compiled manifest instrumentation declaration missing or ambiguous")
    def attrs_at(start: int, depth: int):
        stop = next((i for i, d, _ in elements if i > start and d <= depth), len(lines))
        values: dict[str, str] = {}
        for line in lines[start + 1:stop]:
            m = re.match(r'^\s*A:\s*([A-Za-z_][\w:]*)\s*(?:\([^)]*\))?\s*=\s*"([^"]*)"', line)
            if m:
                key, value = m.groups()
                if key in values:
                    raise GitHubActionsError("Compiled manifest has duplicate package or instrumentation attribute")
                values[key] = value
        return values
    manifest = attrs_at(*roots[0])
    instrumentation = attrs_at(*insts[0])
    # XML dumps of android manifest report package as unqualified package.
    if manifest.get('package') != test_package:
        raise GitHubActionsError("Compiled manifest package differs from signed test APK package")
    if instrumentation.get('android:targetPackage') != target_package:
        raise GitHubActionsError("Compiled manifest instrumentation targetPackage differs from expected application")
    runner = instrumentation.get('android:name', '')
    if not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+", runner):
        raise GitHubActionsError("Compiled manifest instrumentation runner is missing or invalid")
    return runner


def _inspect_apk(package_file: Path, *, apk_role: str = "application",
                 expected_target_package: str = "") -> dict[str, Any]:
    if apk_role not in ('application', 'instrumentation'):
        raise GitHubActionsError("Unknown APK role")
    if apk_role == 'instrumentation' and not _PACKAGE.fullmatch(expected_target_package):
        raise GitHubActionsError("Instrumentation inspection requires expected target application package")
    signer = _run([_tool("apksigner"), "verify", "--print-certs", str(package_file)], purpose="APK signature verification")
    try:
        aapt = _tool("aapt")
        aapt2 = False
    except GitHubActionsError:
        aapt = _tool("aapt2")
        aapt2 = True
    dump = _run([aapt, "dump", "badging", str(package_file)], purpose="APK manifest inspection")
    pkg, code, name = _apk_badging_identity(dump, role=apk_role)
    result = {"package":pkg,"version_code":code,"version_name":name,
              "signer_sha256":_fingerprint(signer),
              "debug_signing":bool(re.search(r"\bCN\s*=\s*Android Debug\b", signer, re.I)),
              "signature_verified":True,"manifest_verified":True,"apk_role":apk_role}
    if apk_role == 'instrumentation':
        if pkg == expected_target_package:
            raise GitHubActionsError("Instrumentation and application package identities must differ")
        command = ([aapt, 'dump', 'xmltree', '--file', 'AndroidManifest.xml', str(package_file)]
                   if aapt2 else [aapt, 'dump', 'xmltree', str(package_file), 'AndroidManifest.xml'])
        xml = _run(command, purpose='APK compiled instrumentation manifest inspection')
        result['instrumentation_runner'] = _instrumentation_xml(
            xml, test_package=pkg, target_package=expected_target_package)
        result['instrumentation_target_package'] = expected_target_package
    return result


def _verify_aab_jar_signature(package_file: Path) -> None:
    """Verify signed JAR bytes without confusing local PKI trust with integrity.

    `jarsigner -verify -strict` returns 4 for a correctly signed JAR using an
    untrusted/self-signed LIFE certificate.  Plain `-verify` checks cryptographic
    signatures but may *also* return 0 for unsigned JARs and JARs with unsigned
    added entries.  Require an explicit verified verdict, reject unsigned
    members, and require a matching JAR signature pair.  `local_verify` then
    independently pins the signer's certificate via keytool and LIFE profile.
    """
    try:
        with zipfile.ZipFile(package_file) as archive:
            names = {item.filename.upper() for item in archive.infolist()
                     if not item.is_dir()}
        signatures = {n[9:-3] for n in names
                      if n.startswith('META-INF/') and n.count('/') == 1 and n.endswith('.SF')}
        blocks = {n[9:-4] for n in names
                  if n.startswith('META-INF/') and n.count('/') == 1 and n.endswith(('.RSA', '.DSA'))}
        blocks |= {n[9:-3] for n in names
                   if n.startswith('META-INF/') and n.count('/') == 1 and n.endswith('.EC')}
    except (zipfile.BadZipFile, OSError, ValueError):
        raise GitHubActionsError('AAB JAR signature archive is invalid') from None
    if len(signatures) != 1 or signatures != blocks or not next(iter(signatures)):
        raise GitHubActionsError('AAB JAR signature entries are missing or ambiguous')

    output = _run([_tool('jarsigner'), '-verify', '-verbose', str(package_file)],
                  purpose='AAB JAR signature verification')
    verdicts = [line.strip().lower() for line in output.splitlines()
                if line.strip().lower() in ('jar verified.', 'jar is unsigned.',
                                            'jar verified, with signer errors.')]
    # The ordinary (non-strict) verification result is cryptographic: chains
    # may be self-signed but damaged signatures still return a nonzero code.
    # A zero return alone is never enough, as unsigned archives also return 0.
    if (verdicts != ['jar verified.'] or
            'unsigned entries' in output.lower() or
            re.search(r'(?m)^\s*\?\s+\d+\s+', output)):
        raise GitHubActionsError('AAB JAR signature is unsigned, partial or ambiguous')


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
    _verify_aab_jar_signature(package_file)
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
                        expected_cert: str = "", member: str = "", apk_role: str = "application",
                        expected_target_package: str = "", expected_source_sha: str = "",
                        expected_run_attempt: int = 0) -> dict[str, Any]:
    if fmt not in ("apk", "aab") or not re.fullmatch(r"[a-z][A-Za-z0-9]{0,60}",variant):
        raise GitHubActionsError("Invalid Android output format or variant")
    if apk_role not in ('application', 'instrumentation'):
        raise GitHubActionsError("Unknown APK role")
    if apk_role == 'instrumentation':
        if fmt != 'apk' or variant != 'debug':
            raise GitHubActionsError("Instrumentation APK inspection is restricted to debug APKs")
        if (not expected_package or not _PACKAGE.fullmatch(expected_package) or
                not _PACKAGE.fullmatch(expected_target_package)):
            raise GitHubActionsError("Instrumentation inspection requires test package and target package pins")
    elif expected_target_package:
        raise GitHubActionsError("Application/AAB inspection cannot accept instrumentation target")
    if expected_cert and not _CERT.fullmatch(expected_cert):
        raise GitHubActionsError("Signing certificate must be a 64-character SHA-256 hex digest")
    is_release = variant.lower().endswith("release")
    if is_release and not expected_cert:
        raise GitHubActionsError("Release output requires --expected-cert-sha256 (stable signing identity)")
    if is_release and not expected_package:
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
        if variant not in ('debug', 'release'):
            if not expected_source_sha or not _SHA40.fullmatch(expected_source_sha) or expected_run_attempt < 1:
                raise GitHubActionsError('Custom Android variant requires exact source and run-attempt binding')
            try:
                manifest_info = z.getinfo('android-variant-manifest.json')
                if manifest_info.file_size > 8192 or manifest_info.file_size <= 0:
                    raise ValueError()
                identity = json.loads(z.read(manifest_info).decode('utf-8'))
            except (KeyError, ValueError, UnicodeError):
                raise GitHubActionsError('Custom variant requires signed-run-bound artifact identity manifest') from None
            required = {'schema_version':1, 'variant':variant, 'format':fmt,
                        'apk_file':info.filename, 'head_sha':expected_source_sha.lower(),
                        'run_attempt':expected_run_attempt}
            if not isinstance(identity, dict) or any(type(identity.get(k)) is not type(v) or identity[k] != v for k,v in required.items()):
                raise GitHubActionsError('Custom Android variant artifact identity mismatch')
            if not re.fullmatch(r'[0-9a-f]{64}', str(identity.get('sha256',''))):
                raise GitHubActionsError('Missing custom Android variant member hash')
            if hashlib.sha256(z.read(info)).hexdigest() != identity['sha256']:
                raise GitHubActionsError('Custom Android variant artifact bytes differ from identity manifest')
            if not variant.lower().endswith('release'):
                declared_build_type=identity.get('build_type')
                if not isinstance(declared_build_type,str) or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,40}',declared_build_type) or not (variant==declared_build_type or variant.endswith(declared_build_type.title())):
                    raise GitHubActionsError('Custom Android variant requires an exact reviewed build-type manifest')
                if declared_build_type != 'debug' and not expected_cert:
                    raise GitHubActionsError('Non-debug Android output requires stable expected signing certificate')
                if declared_build_type != 'debug' and not expected_package:
                    raise GitHubActionsError('Non-debug Android output requires expected application identity')
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
            details = (_inspect_apk(package_file, apk_role=apk_role,
                                    expected_target_package=expected_target_package)
                       if apk_role == 'instrumentation' else
                       _inspect_apk(package_file)) if fmt == "apk" else _inspect_aab(package_file)
            if expected_package and details["package"] != expected_package:
                raise GitHubActionsError("Android package ID differs from the expected application ID")
            if expected_cert and details["signer_sha256"] != expected_cert.lower():
                raise GitHubActionsError("Android signing certificate does not match expected stable identity")
            return {"member":info.filename,"sha256":digest.hexdigest(),"size":size,
                    "format":fmt,"variant":variant,**details,
                    "certificate_expected":bool(expected_cert),"qualified_release":False,
                    "signing_state":"signed-verified" if details.get("signature_verified") else "unverified"}


def android_collect(client: GitHubActionsClient, slug: str, root: Path, *, run_id: int,
                    artifact_id: int, fmt: str, variant: str, expected_package: str = "",
                    expected_cert: str = "", member: str = "", download: bool = False, materialize: bool = True,
                    workflow: str = _WORKFLOW, expected_source_sha: str = "",
                    expected_run_attempt: int = 0, apk_role: str = "application",
                    expected_target_package: str = "") -> dict[str, Any]:
    slug = _slug(slug)
    if not isinstance(workflow, str) or not _WORKFLOW_NAME.fullmatch(workflow):
        raise GitHubActionsError("Android workflow must be one exact safe YAML filename/path")
    if expected_source_sha and not _SHA40.fullmatch(expected_source_sha):
        raise GitHubActionsError("Expected Android source SHA must be exactly 40 hexadecimal characters")
    if type(expected_run_attempt) is not int or expected_run_attempt < 0:
        raise GitHubActionsError("Expected Android run attempt must be a nonnegative integer")
    if workflow != _WORKFLOW and (not expected_source_sha or expected_run_attempt < 1):
        raise GitHubActionsError("Custom Android workflow requires --expected-source-sha and --expected-run-attempt")
    remote = _normalize_run(client.get_run(slug, run_id), slug, run_id)
    if expected_source_sha and str(remote.get('head_sha') or '').lower() != expected_source_sha.lower():
        raise GitHubActionsError("Remote Android run source SHA differs from explicitly pinned source")
    if expected_run_attempt and remote.get('run_attempt') != expected_run_attempt:
        raise GitHubActionsError("Remote Android run attempt differs from explicitly pinned attempt")
    if not remote["completed"] or remote["conclusion"] != "success":
        raise GitHubActionsError("Android collection requires a completed successful GitHub run")
    if remote.get("event") != "workflow_dispatch":
        raise GitHubActionsError("Remote Android build must originate from workflow_dispatch")
    selected_workflow = client.workflow(slug, workflow)
    if remote.get("workflow_id") != selected_workflow["id"]:
        raise GitHubActionsError("Remote run is not the selected Devtool Android workflow")
    if download:
        downloaded = download_artifact(client, slug, run_id, artifact_id, root)
        verified = verify_download(client, slug, run_id, artifact_id, root)
    else:
        downloaded = None
        verified = verify_download(client, slug, run_id, artifact_id, root)
    details = inspect_android_zip(Path(verified["file"]), fmt=fmt, variant=variant,
                                  expected_package=expected_package,expected_cert=expected_cert,
                                  member=member, apk_role=apk_role,
                                  expected_target_package=expected_target_package,
                                  expected_source_sha=str(remote['head_sha']),
                                  expected_run_attempt=remote['run_attempt'])
    # Only materialize Android outputs after ZIP, remote-run and signing checks succeed.
    if not materialize:
        return {"phase":"ACTIONS-05", "repository":slug, "run_id":run_id,
                "artifact_id":artifact_id,"remote_head_sha":remote["head_sha"],
                "remote_attempt":remote["run_attempt"],"remote_workflow_id":remote["workflow_id"],
                "workflow":workflow,"android":details,"archive_verified":True,"qualified_release":False}
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
                "remote_artifact_id":artifact_id,"workflow":workflow,"apk_role":apk_role,
                "instrumentation_target_package":expected_target_package if apk_role == 'instrumentation' else "",
                "package":details["package"],
                "format":fmt,"variant":variant,"signer_sha256":details["signer_sha256"],
                "package_sha256":details["sha256"],"signature_verified":True,
                "qualified_release":False, "release_gate":"not-implemented"}
    records = register_paths(root, [destination], metadata=metadata)
    if not records:
        raise GitHubActionsError("Android package materialized but artifact registry did not accept it")
    payload = {"phase":"ACTIONS-05","repository":slug,"run_id":run_id,
               "artifact_id":artifact_id,"remote_head_sha":remote["head_sha"],
               "remote_attempt":remote["run_attempt"],"remote_workflow_id":remote["workflow_id"],
               "workflow":workflow,"archive_verified":True,"android":details, "package_file":str(destination), "registry_id":records[0]["id"],
               "qualified_release":False,"release_gate":"not-implemented"}
    if downloaded:
        payload["download"] = downloaded
    return payload

# ACTIONS-10: retained V3 integration included in repair commit.
