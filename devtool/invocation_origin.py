from __future__ import annotations

"""Shared execution invocation-origin contract.

The CLI and repository-wrapper final seal both use this module so the metadata
that WRAP-04 persists into EXO is built and validated by one implementation.
"""

from typing import Any, Mapping
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


def safe_internal_invocation_token(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")
    if any(ch not in allowed for ch in text) or len(text) > 128:
        raise ValueError(f"invalid internal invocation {field}")
    return text


_MANAGED_RUN_HANDOFF_ENV = "DEVTOOL_MANAGED_RUN_HANDOFF"
_MANAGED_RUN_HANDOFF_MAX_AGE_SECONDS = 300.0
_FEDERATION_LINEAGE_HANDOFF_ENV = "DEVTOOL_FEDERATION_LINEAGE_HANDOFF"
_FEDERATION_LINEAGE_HANDOFF_MAX_AGE_SECONDS = 300.0


def _write_private_handoff(prefix: str, payload: Mapping[str, object]) -> str:
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(directory, 0o700)
    path = directory / "handoff.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        data = (json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return str(path)


def _consume_private_handoff(
    env_name: str,
    *,
    max_age_seconds: float,
    environment: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, object]:
    env = os.environ if environment is None else environment
    raw = str(env.pop(env_name, "") or "").strip()
    if not raw:
        return {}
    path = Path(raw)
    directory = path.parent
    try:
        pst = path.lstat()
        dst = directory.lstat()
        if stat.S_ISLNK(pst.st_mode) or not stat.S_ISREG(pst.st_mode):
            return {}
        if stat.S_ISLNK(dst.st_mode) or not stat.S_ISDIR(dst.st_mode):
            return {}
        uid = os.getuid() if hasattr(os, "getuid") else None
        if uid is not None and (pst.st_uid != uid or dst.st_uid != uid):
            return {}
        if pst.st_mode & 0o077 or dst.st_mode & 0o077:
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or int(document.get("schema_version") or 0) != 1:
            return {}
        if int(document.get("parent_pid") or -1) != os.getppid():
            return {}
        created_ns = int(document.get("created_ns") or 0)
        age = max(0.0, (time.time_ns() - created_ns) / 1_000_000_000.0)
        if not created_ns or age > max_age_seconds:
            return {}
        return document
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            directory.rmdir()
        except OSError:
            pass


def create_managed_run_handoff(run_id: object) -> str:
    """Create a one-shot capability for an immediate Devtool child process.

    Child-role environment flags are presentation/routing hints, not run-ID
    authority.  A managed parent that needs to preallocate the child's RUN
    identity must create this private, parent-bound handoff instead.
    """
    value = safe_internal_invocation_token(run_id, field="run_id")
    if not value:
        raise ValueError("managed run handoff requires a run id")
    payload = {
        "schema_version": 1,
        "run_id": value,
        "parent_pid": os.getpid(),
        "created_ns": time.time_ns(),
    }
    return _write_private_handoff("devtool-run-handoff-", payload)


def consume_managed_run_handoff(environment: dict[str, str] | os._Environ[str] | None = None) -> str:
    """Consume and verify an immediate-parent run-ID capability.

    Invalid, stale, inherited or repository-injected paths do not become
    authority; they are ignored and the caller allocates a fresh run ID.
    """
    document = _consume_private_handoff(
        _MANAGED_RUN_HANDOFF_ENV,
        max_age_seconds=_MANAGED_RUN_HANDOFF_MAX_AGE_SECONDS,
        environment=environment,
    )
    if not document:
        return ""
    return safe_internal_invocation_token(document.get("run_id"), field="run_id")


_FEDERATION_LINEAGE_CACHE: ContextVar[dict[str, object] | None] = ContextVar("devtool_federation_lineage", default=None)


def _sanitize_lineage_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _sanitize_lineage_value(v) for k, v in value.items() if str(k)}
    if isinstance(value, (list, tuple)):
        return [_sanitize_lineage_value(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    return str(value).strip()


def create_federation_lineage_handoff(lineage: Mapping[str, object]) -> str:
    """Create a private federation lineage capability for an immediate child.

    Public ``DEVTOOL_FEDERATION_*`` variables remain routing hints for older
    providers, but they are not authority for invocation metadata unless paired
    with this parent-bound handoff.
    """
    clean = {str(k): _sanitize_lineage_value(v) for k, v in dict(lineage).items() if str(k)}
    if not str(clean.get("parent_run_id") or "").strip():
        raise ValueError("federation lineage handoff requires parent_run_id")
    payload = {
        "schema_version": 1,
        "kind": "devtool.federation-lineage-handoff",
        "parent_pid": os.getpid(),
        "created_ns": time.time_ns(),
        "lineage": clean,
    }
    return _write_private_handoff("devtool-federation-lineage-", payload)


def consume_federation_lineage_handoff(environment: dict[str, str] | os._Environ[str] | None = None) -> dict[str, object]:
    cached = _FEDERATION_LINEAGE_CACHE.get()
    if cached is not None:
        return dict(cached)
    document = _consume_private_handoff(
        _FEDERATION_LINEAGE_HANDOFF_ENV,
        max_age_seconds=_FEDERATION_LINEAGE_HANDOFF_MAX_AGE_SECONDS,
        environment=environment,
    )
    raw = document.get("lineage") if isinstance(document, dict) else None
    if not isinstance(raw, Mapping):
        return {}
    lineage = {str(k): _sanitize_lineage_value(v) for k, v in raw.items() if str(k)}
    if not str(lineage.get("parent_run_id") or "").strip():
        return {}
    _FEDERATION_LINEAGE_CACHE.set(dict(lineage))
    return dict(lineage)


@dataclass(frozen=True, slots=True)
class TrustedInvocationContext:
    source: str = "cli"
    command: str = "devtool"
    wrapper_schema: str = ""


_TRUSTED_INVOCATION: ContextVar[TrustedInvocationContext | None] = ContextVar("devtool_trusted_invocation", default=None)


@contextmanager
def trusted_invocation_context(*, source: object, command: object, wrapper_schema: object = ""):
    """Set invocation provenance from a trusted in-process bridge only.

    Ordinary argv and repository/environment configuration never feed this
    context.  The wrapper execution bridge is the only current producer.
    """
    ctx = TrustedInvocationContext(
        source=safe_internal_invocation_token(source or "cli", field="source") or "cli",
        command=safe_internal_invocation_token(command or "devtool", field="command") or "devtool",
        wrapper_schema=safe_internal_invocation_token(wrapper_schema, field="wrapper_schema"),
    )
    token = _TRUSTED_INVOCATION.set(ctx)
    try:
        yield ctx
    finally:
        _TRUSTED_INVOCATION.reset(token)


def current_trusted_invocation() -> TrustedInvocationContext | None:
    return _TRUSTED_INVOCATION.get()

def build_invocation_metadata(
    *,
    source: object,
    command: object,
    wrapper_schema: object = "",
) -> dict[str, object]:
    normalized_source = safe_internal_invocation_token(source or "cli", field="source") or "cli"
    normalized_command = safe_internal_invocation_token(command or "devtool", field="command") or "devtool"
    metadata: dict[str, object] = {
        "invocation_source": normalized_source,
        "invocation_command": normalized_command,
    }
    schema = safe_internal_invocation_token(wrapper_schema, field="wrapper_schema")
    if schema:
        metadata["wrapper_schema"] = schema
    if normalized_source == "wrapper":
        metadata["wrapper_command"] = normalized_command
    return metadata


def invocation_source_for_args(args: Any) -> str:
    trusted = current_trusted_invocation()
    return trusted.source if trusted is not None else "cli"


def invocation_command_for_args(args: Any) -> str:
    trusted = current_trusted_invocation()
    if trusted is not None:
        return trusted.command
    return safe_internal_invocation_token(getattr(args, "command", "") or "devtool", field="command") or "devtool"


def invocation_wrapper_schema_for_args(args: Any) -> str:
    trusted = current_trusted_invocation()
    return trusted.wrapper_schema if trusted is not None else ""

def _ambient_federation_lineage_from_environment() -> dict[str, object]:
    parent_run_id = str(os.environ.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    if not parent_run_id:
        return {}
    values = {
        "kind": "federation",
        "relationship": str(os.environ.get("DEVTOOL_FEDERATION_RELATIONSHIP") or "child").strip() or "child",
        "parent_run_id": parent_run_id,
        "parent_node_id": str(os.environ.get("DEVTOOL_FEDERATION_PARENT_NODE_ID") or "").strip(),
        "federation_workflow": str(os.environ.get("DEVTOOL_FEDERATION_WORKFLOW") or "").strip(),
        "federation_group": str(os.environ.get("DEVTOOL_FEDERATION_GROUP") or "").strip(),
        "federation_step": str(os.environ.get("DEVTOOL_FEDERATION_STEP") or "").strip(),
        "repository": str(os.environ.get("DEVTOOL_FEDERATION_REPOSITORY") or "").strip(),
        "target": str(os.environ.get("DEVTOOL_FEDERATION_TARGET") or "").strip(),
        "child_workflow": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_WORKFLOW") or "").strip(),
        "federation_ref": str(os.environ.get("DEVTOOL_FEDERATION_REF") or "").strip(),
        "requested_ref": str(os.environ.get("DEVTOOL_FEDERATION_REQUESTED_REF") or "").strip(),
        "child_contract": str(os.environ.get("DEVTOOL_FEDERATION_CHILD_CONTRACT") or "").strip(),
        "contract_ref": str(os.environ.get("DEVTOOL_FEDERATION_CONTRACT_REF") or "").strip(),
        "source_repository": str(os.environ.get("DEVTOOL_FEDERATION_SOURCE_REPOSITORY") or "").strip(),
        "requested_repository": str(os.environ.get("DEVTOOL_FEDERATION_REQUESTED_REPOSITORY") or "").strip(),
        "effective_repository": str(os.environ.get("DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY") or "").strip(),
        "execution_environment": str(os.environ.get("DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT") or "").strip(),
        "child_run_id": str(os.environ.get("DEVTOOL_RUN_ID") or "").strip(),
        "frozen_source_lock_digest": str(os.environ.get("DEVTOOL_FEDERATION_SOURCE_LOCK_DIGEST") or "").strip(),
        "frozen_source_equivalence_digest": str(os.environ.get("DEVTOOL_FEDERATION_SOURCE_EQUIVALENCE_DIGEST") or "").strip(),
        "delegation_id": str(os.environ.get("DEVTOOL_DELEGATION_LINK_ID") or "").strip(),
    }
    git_lineage = {
        "schema_version": 1,
        "kind": "federation-git-lineage",
        "state": "planned",
        "parent_run_id": parent_run_id,
        "parent_node_id": values.get("parent_node_id", ""),
        "child_run_id": values.get("child_run_id", ""),
        "repository": values.get("repository", ""),
        "repository_id": str(os.environ.get("DEVTOOL_FEDERATION_GIT_REPOSITORY_ID") or "").strip(),
        "target": values.get("target", ""),
        "contract_ref": values.get("contract_ref", ""),
        "frozen_plan_identity": str(os.environ.get("DEVTOOL_FEDERATION_FROZEN_PLAN_IDENTITY") or values.get("frozen_source_lock_digest") or "").strip(),
        "frozen_plan_equivalence_identity": str(os.environ.get("DEVTOOL_FEDERATION_FROZEN_PLAN_EQUIVALENCE_IDENTITY") or values.get("frozen_source_equivalence_digest") or "").strip(),
        "frozen_repository_source_identity": str(os.environ.get("DEVTOOL_FEDERATION_FROZEN_REPOSITORY_SOURCE_IDENTITY") or "").strip(),
        "config_digest": str(os.environ.get("DEVTOOL_FEDERATION_GIT_CONFIG_DIGEST") or "").strip(),
        "head_tree": str(os.environ.get("DEVTOOL_FEDERATION_GIT_HEAD_TREE") or "").strip(),
        "remote_locator": str(os.environ.get("DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR") or "").strip(),
        "remote_locator_digest": str(os.environ.get("DEVTOOL_FEDERATION_GIT_REMOTE_LOCATOR_DIGEST") or "").strip(),
        "start_commit": str(os.environ.get("DEVTOOL_FEDERATION_GIT_START_COMMIT") or "").strip(),
        "start_unborn": str(os.environ.get("DEVTOOL_FEDERATION_GIT_START_UNBORN") or "0").strip() in {"1", "true", "yes"},
        "expected_result_ref": str(os.environ.get("DEVTOOL_FEDERATION_GIT_EXPECTED_RESULT_REF") or "").strip(),
        "commit_mode": str(os.environ.get("DEVTOOL_FEDERATION_GIT_COMMIT_MODE") or "none").strip() or "none",
    }
    if any(value not in (None, "", False) for key, value in git_lineage.items() if key not in {"schema_version", "kind", "state", "commit_mode"}):
        values["git_lineage"] = git_lineage
    return {key: value for key, value in values.items() if value not in (None, "")}


def federation_lineage_from_environment() -> dict[str, object]:
    lineage = consume_federation_lineage_handoff()
    if lineage:
        return lineage
    trusted = current_trusted_invocation()
    if trusted is not None and trusted.source == "federation":
        return _ambient_federation_lineage_from_environment()
    # Public DEVTOOL_FEDERATION_* values may be inherited from a parent shell,
    # repository job environment or hostile user input.  They are routing hints
    # for lower-level providers only; without a trusted in-process bridge or the
    # private parent-bound handoff above they are not invocation lineage.
    return {}


# Private historical spelling retained for internal/plugin compatibility.
_federation_lineage_from_environment = federation_lineage_from_environment


def invocation_metadata_for_args(args: Any) -> dict[str, object]:
    metadata = build_invocation_metadata(
        source=invocation_source_for_args(args),
        command=invocation_command_for_args(args),
        wrapper_schema=invocation_wrapper_schema_for_args(args),
    )
    lineage = federation_lineage_from_environment()
    if lineage:
        metadata["run_lineage"] = lineage
    return metadata
