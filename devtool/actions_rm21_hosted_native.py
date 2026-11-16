"""RM21: hosted, source-pinned native Node/JVM consumer acceptance.

Creates NON-RELEASE evidence only. No tests are inferred from exit status alone;
Node build outputs and JVM class archives are byte-verified after real execution.
Node/JVM source admission is delegated to the native RM11/RM12 providers.
This tool does not claim a separately authenticated hosted attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile
from typing import Any

SHA = re.compile(r"[0-9a-f]{40}\Z")
PROJECT = re.compile(r"(?:[A-Za-z0-9_.-]+/){0,7}[A-Za-z0-9_.-]+\Z")
MODULE = re.compile(r":[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*)*\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,100}\Z")


class HostedAcceptanceError(ValueError):
    """A source, toolchain, execution, or immutable-byte gate failed."""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostedAcceptanceError("RM21 Git source identity unavailable") from exc
    if result.returncode:
        raise HostedAcceptanceError("RM21 Git source verification failed: " + result.stderr[-180:])
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _checkout(root: Path, expected: str) -> dict[str, str]:
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise HostedAcceptanceError("RM21 expected consumer SHA must be a full hex commit")
    if not root.is_dir() or root.is_symlink():
        raise HostedAcceptanceError("RM21 consumer root is not a regular directory")
    root = root.resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise HostedAcceptanceError("RM21 root must be the consumer checkout root")
    head = _git(root, "rev-parse", "HEAD")
    if head != expected:
        raise HostedAcceptanceError("RM21 consumer HEAD differs from expected commit")
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise HostedAcceptanceError("RM21 source checkout is dirty before execution")
    return {"consumer_commit": head, "consumer_tree": _git(root, "rev-parse", "HEAD^{tree}")}


def _ensure_source_unchanged(root: Path, expected: str, tree: str) -> None:
    if _git(root, "rev-parse", "HEAD") != expected or _git(root, "rev-parse", "HEAD^{tree}") != tree:
        raise HostedAcceptanceError("RM21 consumer commit/tree changed during native execution")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise HostedAcceptanceError("RM21 tracked consumer bytes changed during execution")


def _child_env() -> dict[str, str]:
    # GitHub or user tokens, package registry credentials, credentials in arbitrary
    # environment names, and inherited PYTHONPATH are not forwarded to consumers.
    safe = ("PATH", "HOME", "TMPDIR", "RUNNER_TEMP", "JAVA_HOME", "GRADLE_USER_HOME",
            "LANG", "LC_ALL", "SYSTEMROOT", "USERPROFILE")
    env = {k: os.environ[k] for k in safe if k in os.environ}
    # RM22: runner platform is nonsecret, but arbitrary inherited values are not.
    # This is an environment compatibility hint, NOT hosted authentication.
    if os.environ.get("RUNNER_OS") in ("Linux", "Windows", "macOS"):
        env["RUNNER_OS"] = os.environ["RUNNER_OS"]
    env.update({"CI": "true", "GITHUB_ACTIONS": "true",
                "npm_config_ignore_scripts": "true", "npm_config_audit": "false",
                "COREPACK_ENABLE_AUTO_PIN": "0", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
                "GRADLE_OPTS": "-Dorg.gradle.daemon=false"})
    return env


def _run(argv: list[str], cwd: Path, *, seconds: int = 600) -> None:
    # No shell or shell-style arguments. The called source has already passed
    # native provider review; only literal, bounded commands are constructed.
    try:
        p = subprocess.run(argv, cwd=cwd, env=_child_env(), timeout=seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostedAcceptanceError("RM21 native command unavailable or exceeded time budget") from exc
    if p.returncode:
        raise HostedAcceptanceError("RM21 native command failed: " + " ".join(argv[:3]))


def _tool_version(tool: str, required: str, *, major: bool = False) -> None:
    try:
        p = subprocess.run([tool, "--version"] if tool != "javac" else ["javac", "-version"],
                           capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostedAcceptanceError("RM21 required hosted tool is unavailable: " + tool) from exc
    result = (p.stdout + " " + p.stderr).strip()
    if p.returncode:
        raise HostedAcceptanceError("RM21 hosted tool probe failed: " + tool)
    if tool == "node":
        ok = re.search(r"\bv" + re.escape(required) + r"\.", result) is not None
    elif tool == "javac":
        ok = re.search(r"\bjavac " + re.escape(required) + r"(?:\.|\s|$)", result) is not None
    elif tool == "gradle":
        ok = re.search(r"\bGradle " + re.escape(required) + r"(?:\s|$)", result) is not None
    elif major:
        ok = result == required
    else:
        ok = result == required
    if not ok:
        raise HostedAcceptanceError("RM21 hosted " + tool + " version differs from reviewed pin: " + result[:100])


def _project(root: Path, relative: str) -> Path:
    if relative in ("", "."):
        return root
    if not PROJECT.fullmatch(relative) or any(x in (".", "..") for x in relative.split("/")):
        raise HostedAcceptanceError("RM21 unsafe nested Node project")
    project = root
    for part in relative.split("/"):
        project = project / part
        if project.is_symlink():
            raise HostedAcceptanceError("RM21 linked project directory refused")
    if not project.is_dir():
        raise HostedAcceptanceError("RM21 nested Node project missing")
    return project


def _outputs(folder: Path) -> list[str]:
    if folder.is_symlink() or not folder.is_dir():
        raise HostedAcceptanceError("RM21 missing or symlinked dist root")
    paths = []
    for p in folder.rglob("*"):
        if p.is_symlink() or not (p.is_dir() or p.is_file()):
            raise HostedAcceptanceError("RM21 symlink or nonregular generated output")
        if p.is_file():
            if p.stat().st_size > 16 * 1024 * 1024:
                raise HostedAcceptanceError("RM21 generated output exceeds limit")
            paths.append("dist/" + p.relative_to(folder).as_posix())
    if not 2 <= len(paths) <= 200 or "dist/manifest.json" not in paths:
        raise HostedAcceptanceError("RM21 output inventory missing manifest/owned bytes")
    return sorted(paths)


def _jar(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise HostedAcceptanceError("RM21 JVM JAR missing, linked or oversized")
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if not 2 <= len(entries) <= 4096 or sum(x.file_size for x in entries) > 128 * 1024 * 1024:
                raise HostedAcceptanceError("RM21 JVM JAR entries exceed bounds")
            names = set()
            for item in entries:
                name = item.filename
                if (not name or name.startswith("/") or "\\" in name or
                    ".." in name.split("/") or name.casefold() in names or
                    item.flag_bits & 1 or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                    raise HostedAcceptanceError("RM21 unsafe/duplicate JVM JAR entry")
                names.add(name.casefold())
            if "meta-inf/manifest.mf" not in names or not any(n.endswith(".class") for n in names):
                raise HostedAcceptanceError("RM21 JAR lacks actual compiled JVM classes")
            if z.testzip() is not None:
                raise HostedAcceptanceError("RM21 JVM JAR has corrupt members")
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise HostedAcceptanceError("RM21 invalid JVM JAR archive") from exc
    return {"sha256": _sha256(path), "bytes": path.stat().st_size, "file": path.name}


def _external_source(root: Path, repository: str) -> dict[str, Any]:
    if not repository:
        return {"external_consumer_verified": False, "external_repository": None}
    if not REPOSITORY.fullmatch(repository) or repository.lower() == "mikeyphw/devtool":
        raise HostedAcceptanceError("RM21 external repo must be a distinct owner/name")
    url = _git(root, "remote", "get-url", "origin")
    valid = {"https://github.com/" + repository + ".git",
             "https://github.com/" + repository,
             "git@github.com:" + repository + ".git"}
    if url not in valid:
        raise HostedAcceptanceError("RM21 external consumer remote identity differs")
    return {"external_consumer_verified": True, "external_repository": repository}


def accept_node(root: Path, *, expected_sha: str, project_dir: str = "web",
                node_version: str = "22", python_version: str = "3.13",
                external_repository: str = "") -> dict[str, Any]:
    identity = _checkout(root, expected_sha)
    ext = _external_source(root, external_repository)
    project = _project(root, project_dir)
    from .actions_rm11_node import inspect_node_job, verify_js_artifacts, NodeError
    meta = {"ci_node_project_dir": project_dir if project_dir != "." else "",
            "ci_node_script_policy": "reviewed-composite", "ci_node_version": node_version,
            "ci_python_version": python_version}
    try:
        reviewed = inspect_node_job(root, ["npm", "run", "build"], meta)
    except NodeError as exc:
        raise HostedAcceptanceError("RM21 RM11 source review refused: " + str(exc)) from None
    _tool_version("node", node_version)
    _tool_version("npm", reviewed["manager_version"])
    _run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], project)
    _run(["npm", "run", "build"], project)
    _ensure_source_unchanged(root, identity["consumer_commit"], identity["consumer_tree"])
    try:
        again = inspect_node_job(root, ["npm", "run", "build"], meta)
        if again["workspace"]["source_digest"] != reviewed["workspace"]["source_digest"]:
            raise HostedAcceptanceError("RM21 Node source digest drift after execution")
        output_names = _outputs(project / "dist")
        outputs = verify_js_artifacts(project, output_names, "build", reviewed["workspace"], reviewed["package_path"])
    except NodeError as exc:
        raise HostedAcceptanceError("RM21 RM11 native output verification refused: " + str(exc)) from None
    return {"lane": "node-build", **identity, **ext,
            "provider": "rm11-node", "project_dir": project_dir,
            "source_digest": reviewed["workspace"]["source_digest"],
            "node_version": node_version, "npm_version": reviewed["manager_version"],
            "outputs": outputs}


def accept_jvm(root: Path, *, expected_sha: str, module: str = ":desktop",
               jdk: str = "21", gradle: str = "9.1.0",
               external_repository: str = "") -> dict[str, Any]:
    identity = _checkout(root, expected_sha)
    ext = _external_source(root, external_repository)
    if not MODULE.fullmatch(module):
        raise HostedAcceptanceError("RM21 unsafe JVM module selection")
    from .actions_rm12_jvm import inspect_jvm_job, JvmError
    args = ["gradle", "--offline", "--no-daemon", "--console=plain", module + ":jar"]
    meta = {"ci_jvm_mixed_module": module, "ci_jdk_version": jdk,
            "ci_jvm_tool_version": gradle}
    try:
        reviewed = inspect_jvm_job(root, args, meta)
    except JvmError as exc:
        raise HostedAcceptanceError("RM21 RM12 source review refused: " + str(exc)) from None
    _tool_version("javac", jdk)
    _tool_version("gradle", gradle)
    _run(args, root, seconds=900)
    _ensure_source_unchanged(root, identity["consumer_commit"], identity["consumer_tree"])
    try:
        again = inspect_jvm_job(root, args, meta)
    except JvmError as exc:
        raise HostedAcceptanceError("RM21 RM12 post-execution source review refused: " + str(exc)) from None
    if again["project"]["source_digest"] != reviewed["project"]["source_digest"]:
        raise HostedAcceptanceError("RM21 JVM reviewed source changed during native execution")
    path = root
    for component in (*module[1:].split(":"), "build", "libs"):
        path = path / component
        if path.is_symlink():
            raise HostedAcceptanceError("RM21 linked JVM artifact ancestor refused")
    if not path.is_dir():
        raise HostedAcceptanceError("RM21 JVM artifact output directory missing")
    jars = list(path.glob("*.jar"))
    if len(jars) != 1:
        raise HostedAcceptanceError("RM21 JVM requires exactly one owned compiled JAR")
    output = _jar(jars[0])
    # The first-class RM12 verifier also walks real class-file constant pools,
    # nested archive structure, manifest semantics, and any local signature.
    # It is stronger than checking for a .class filename and an intact ZIP.
    from .actions_rm12_jvm import verify_jar
    try:
        rm12 = verify_jar(jars[0])
    except JvmError as exc:
        raise HostedAcceptanceError("RM21 native RM12 JAR verifier refused: " + str(exc)) from None
    if rm12['sha256'] != output['sha256']:
        raise HostedAcceptanceError("RM21 RM12 verifier/source output digest mismatch")
    output['rm12_verification'] = rm12
    output["path"] = jars[0].relative_to(root).as_posix()
    return {"lane": "jvm-jar", **identity, **ext, "provider": "rm12-jvm",
            "selected_module": module, "source_digest": reviewed["project"]["source_digest"],
            "jdk_version": jdk, "gradle_version": gradle, "outputs": [output]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("lane", choices=("node-build", "jvm-jar"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--consumer-sha", required=True)
    p.add_argument("--devtool-sha", required=True)
    p.add_argument("--external-repository", default="")
    p.add_argument("--node-project", default="web")
    p.add_argument("--node-version", default="22")
    p.add_argument("--python-version", default="3.13")
    p.add_argument("--jvm-module", default=":desktop")
    p.add_argument("--jdk-version", default="21")
    p.add_argument("--gradle-version", default="9.1.0")
    p.add_argument("--receipt", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if not SHA.fullmatch(args.devtool_sha):
            raise HostedAcceptanceError("RM21 Devtool source SHA must be 40 lowercase hex")
        tool_root = Path(__file__).resolve().parents[1]
        if _git(tool_root, "rev-parse", "HEAD") != args.devtool_sha:
            raise HostedAcceptanceError("RM21 running Devtool SHA differs from reviewed code checkout")
        if args.root.is_symlink():
            raise HostedAcceptanceError("RM21 linked checkout root refused")
        root = args.root.resolve(strict=True)
        if args.lane == "node-build":
            payload = accept_node(root, expected_sha=args.consumer_sha, project_dir=args.node_project,
                                  node_version=args.node_version, python_version=args.python_version,
                                  external_repository=args.external_repository)
        else:
            payload = accept_jvm(root, expected_sha=args.consumer_sha, module=args.jvm_module,
                                 jdk=args.jdk_version, gradle=args.gradle_version,
                                 external_repository=args.external_repository)
        receipt = {"schema_version": 1, "kind": "devtool.rm21.hosted-native",
                   "result": "PASS", "devtool_sha": args.devtool_sha,
                   "native_execution_completed": True,
                   "source_pinned": True, "release_qualified": False,
                   "signing_qualified": False, "device_qualified": False,
                   "independent_consumer_qualified": payload["external_consumer_verified"],
                   **payload}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as out:
            json.dump(receipt, out, indent=2, sort_keys=True)
            out.write("\n")
        print("RM21_" + args.lane.upper().replace("-", "_") + "_PASS " + str(args.receipt))
        return 0
    except (HostedAcceptanceError, OSError, RuntimeError) as exc:
        print("RM21_ACCEPTANCE_REFUSED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
