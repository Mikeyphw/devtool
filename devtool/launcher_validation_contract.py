"""Bounded, data-only validation contract shared by the native launcher and scaffold.

This module never executes project tests, hooks, git, or network operations.
All executable validation is delegated to Devtool's existing artifact engine.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
HOOK_BASE = ".devtool-artifact/"
MAX_HOOK_BYTES = 128 * 1024
MAX_TOTAL_HOOK_BYTES = 512 * 1024
_ALLOWED_TOP = frozenset({"schema_version", "changes", "delivery_validation_mode", "delivery_static_justification",
                          "validation", "preflight", "finalize"})
_HOOK_FIELDS = frozenset({"path", "sha256", "runner", "timeout_seconds", "protocol", "python"})


class ValidationContractError(ValueError):
    """A required, immutable artifact contract cannot be verified."""


def _object(obj: Any, name: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValidationContractError(f"{name} must be a JSON object")
    return dict(obj)


def _hook_paths(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    paths: dict[str, dict[str, Any]] = {}
    changes = plan.get("changes")
    changed = {c.get("path") for c in changes if isinstance(c, dict)} if isinstance(changes, list) else set()
    for section in ("validation", "preflight", "finalize"):
        config = plan.get(section)
        if config is None:
            continue
        config = _object(config, section)
        spec = config.get("script")
        if spec is None:
            continue
        spec = _object(spec, section + ".script")
        if not set(spec).issubset(_HOOK_FIELDS):
            raise ValidationContractError(f"{section} hook contains unsupported keys")
        path = spec.get("path")
        if not isinstance(path, str) or len(path) > 160 or not path.startswith(HOOK_BASE):
            raise ValidationContractError(f"{section} hook path must be below {HOOK_BASE}")
        pure = PurePosixPath(path)
        if (pure.as_posix() != path or any(t in ("", ".", "..") for t in path.split("/"))
                or pure.suffix not in (".py", ".sh") or path in changed):
            raise ValidationContractError(f"{section} hook has unsafe or inventory-overlapping path")
        digest = spec.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValidationContractError(f"{section} hook SHA-256 must be exact lowercase hex")
        runner = spec.get("runner")
        if runner not in ("python3", "sh", "bash"):
            raise ValidationContractError(f"{section} hook runner unsupported")
        timeout = spec.get("timeout_seconds", 300)
        if type(timeout) is not int or not 1 <= timeout <= 1800:
            raise ValidationContractError(f"{section} hook timeout must be 1..1800 seconds")
        if spec.get("protocol", "legacy-env") not in ("json-v1", "legacy-env"):
            raise ValidationContractError(f"{section} hook protocol unsupported")
        prior = paths.get(path)
        if prior is not None and prior != spec:
            raise ValidationContractError("Duplicate hook path with inconsistent identity")
        paths[path] = spec
    return paths


def inspect_delivery_plan(plan: Mapping[str, Any], payload_root: Path | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate extension, return canonical plan fields and checksum-verified hooks.

    Legacy S7 plans containing only schema_version and changes are accepted
    unchanged. No implicit test execution is inferred for those plans.
    """
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not isinstance(plan.get("changes"), list):
        raise ValidationContractError("Scaffold plan requires schema_version=1 and changes")
    if not set(plan).issubset(_ALLOWED_TOP):
        raise ValidationContractError("Expected only schema_version=1, changes and documented validation extensions; Unknown scaffold plan fields")
    extension = {k: plan[k] for k in ("delivery_validation_mode", "delivery_static_justification",
                                        "validation", "preflight", "finalize") if k in plan}
    mode = extension.get("delivery_validation_mode", "static")
    if mode not in ("full", "static"):
        raise ValidationContractError("Unsupported validation mode; deferred delivery requires a separate native session")
    legacy = not extension
    if mode == "static" and not legacy:
        reason = extension.get("delivery_static_justification")
        if (not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 320
                or any(ord(c) < 32 for c in reason)):
            raise ValidationContractError("Static-only delivery requires an explicit bounded justification")
    elif "delivery_static_justification" in extension:
        raise ValidationContractError("Static justification is invalid in full validation mode")
    if "validation" in extension:
        policy = _object(extension["validation"], "validation")
        if policy.get("mode") == "disabled":
            raise ValidationContractError("Explicit validation cannot be disabled")
        if "required" in policy and policy["required"] is not True:
            raise ValidationContractError("Explicit validation.required cannot be false")
        if mode == "full" and policy.get("required") is not True:
            raise ValidationContractError("Full validation must declare required=true")
        for k in ("restore", "build", "test", "rollback_on_failure"):
            if k in policy and type(policy[k]) is not bool:
                raise ValidationContractError(f"validation.{k} must be boolean")
        if "tasks" in policy and (not isinstance(policy["tasks"], list)
                                  or not policy["tasks"]
                                  or not all(isinstance(x, str) and x.strip() for x in policy["tasks"])):
            raise ValidationContractError("validation.tasks must be a nonempty string list")
        if "tests" in policy:
            if not isinstance(policy["tests"], list) or not policy["tests"]:
                raise ValidationContractError("validation.tests must be a nonempty list")
            required_test = False
            ids: set[str] = set()
            for index, item in enumerate(policy["tests"], 1):
                if isinstance(item, str):
                    if not re.fullmatch(r"(?:test|workflow|invoke):[a-zA-Z0-9_.-]{1,128}", item):
                        raise ValidationContractError("Invalid native validation.tests reference")
                    required_test = True
                    continue
                if not isinstance(item, dict):
                    raise ValidationContractError("validation.tests entries must be native declarations or references")
                if "ref" in item:
                    if set(item) != {"ref"} or not isinstance(item["ref"], str) or not re.fullmatch(
                            r"(?:test|workflow|invoke):[a-zA-Z0-9_.-]{1,128}", item["ref"]):
                        raise ValidationContractError("Invalid native validation.tests reference object")
                    required_test = True
                    continue
                tid, runner = item.get("id"), item.get("runner")
                if not isinstance(tid, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", tid) or tid in ids:
                    raise ValidationContractError("Invalid or duplicate native validation.tests ID")
                ids.add(tid)
                if not isinstance(runner, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", runner):
                    raise ValidationContractError("Invalid native validation.tests runner")
                required = item.get("required", True)
                if type(required) is not bool:
                    raise ValidationContractError("validation.tests required must be a boolean")
                cmd, task = item.get("command"), item.get("task")
                if (bool(cmd) == bool(task) or
                    (cmd is not None and (not isinstance(cmd, (str, list)) or
                        (isinstance(cmd, list) and (not cmd or not all(isinstance(t, str) and t.strip() for t in cmd))))) or
                    (task is not None and (not isinstance(task, str) or not task.strip()))):
                    raise ValidationContractError("Native validation.tests must select exactly one executable command or task")
                required_test = required_test or required
            if not required_test and not policy.get("script") and not policy.get("tasks") and not any(
                    policy.get(k) is True for k in ("restore", "build", "test")):
                raise ValidationContractError("Required validation cannot rely only on optional tests")
        # Native validation.tasks is an *exclusive* runner plan: Devtool does
        # not run its normal artifact validator under that branch. Reject a
        # contract which promises both and would otherwise give false evidence.
        if policy.get("tasks") and (policy.get("tests") or policy.get("script") or any(
                policy.get(k) is True for k in ("restore", "build", "test"))):
            raise ValidationContractError("Exclusive tasks cannot promise skipped tests, lifecycle phases, or validator")
        has_exec = (bool(policy.get("script")) or bool(policy.get("tests"))
                    or bool(policy.get("tasks"))
                    or any(policy.get(k) is True for k in ("restore", "build", "test")))
        if not has_exec:
            raise ValidationContractError("Declared validation has zero executable checks")
        if mode == "static":
            if policy.get("tests") or policy.get("tasks") or any(
                    policy.get(k) is True for k in ("restore", "build", "test")):
                raise ValidationContractError("Static validation cannot silently skip declared tests/tasks/restore/build/test")
            if not policy.get("script"):
                raise ValidationContractError("Static validation requires an explicit checksummed validator")
            if policy.get("required") is not True:
                raise ValidationContractError("Static validator must be required")
            if any(policy.get(k) is not False for k in ("restore", "build", "test")):
                raise ValidationContractError("Static validator must explicitly disable all skipped lifecycle phases")
    elif mode == "full":
        raise ValidationContractError("Full validation requires explicit validation policy")
    if mode == "static" and not legacy and "validation" not in extension:
        raise ValidationContractError("Static-only delivery requires an embedded executable validator")
    hooks = _hook_paths(plan)
    files: dict[str, bytes] = {}
    if payload_root is not None:
        total = 0
        root = Path(payload_root)
        if root.is_symlink() or not root.is_dir():
            raise ValidationContractError("Hook payload root is not a regular directory")
        for path, spec in sorted(hooks.items()):
            candidate = root
            for part in PurePosixPath(path).parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    raise ValidationContractError("Hook symlink ancestry refused")
            if not candidate.is_file() or not stat.S_ISREG(candidate.stat().st_mode):
                raise ValidationContractError("Hook source must be a regular file")
            size = candidate.stat().st_size
            if not 0 < size <= MAX_HOOK_BYTES:
                raise ValidationContractError("Hook script must be bounded and nonempty")
            data = candidate.read_bytes()
            total += len(data)
            if total > MAX_TOTAL_HOOK_BYTES or hashlib.sha256(data).hexdigest() != spec["sha256"]:
                raise ValidationContractError("Hook script digest/size refused")
            files[path] = data
    return extension, files


def metadata_descriptors(hooks: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for path, data in sorted(hooks.items())]


def verify_metadata_manifest(plan: Mapping[str, Any], metadata: Any) -> None:
    """Reject omitted or undeclared hook members before any native transaction."""
    inspect_delivery_plan(plan)
    expected = {path: s["sha256"] for path, s in _hook_paths(plan).items()}
    if not isinstance(metadata, list) or len(metadata) != len(expected):
        raise ValidationContractError("Immutable hook metadata inventory differs from plan")
    seen: set[str] = set()
    for entry in metadata:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise ValidationContractError("Malformed hook metadata descriptor")
        p, sha, size = entry["path"], entry["sha256"], entry["size"]
        if p in seen or p not in expected or sha != expected[p] or type(size) is not int or not 0 < size <= MAX_HOOK_BYTES:
            raise ValidationContractError("Hook metadata cannot be reconciled with checksummed script")
        seen.add(p)


def validation_execution_mode(plan: Mapping[str, Any]) -> str:
    inspect_delivery_plan(plan)
    return str(plan.get("delivery_validation_mode", "static"))
