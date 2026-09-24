from __future__ import annotations

"""Shared execution invocation-origin contract.

The CLI and repository-wrapper final seal both use this module so the metadata
that WRAP-04 persists into EXO is built and validated by one implementation.
"""

from typing import Any
import os


def safe_internal_invocation_token(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")
    if any(ch not in allowed for ch in text) or len(text) > 128:
        raise ValueError(f"invalid internal invocation {field}")
    return text


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
    return safe_internal_invocation_token(
        getattr(args, "_devtool_invocation_source", "") or "cli",
        field="source",
    ) or "cli"


def invocation_command_for_args(args: Any) -> str:
    return safe_internal_invocation_token(
        getattr(args, "_devtool_invocation_command", "") or getattr(args, "command", "") or "devtool",
        field="command",
    ) or "devtool"


def federation_lineage_from_environment() -> dict[str, object]:
    parent_run_id = str(os.environ.get("DEVTOOL_FEDERATION_PARENT_RUN_ID") or "").strip()
    if not parent_run_id:
        return {}
    values = {
        "kind": "federation",
        "relationship": str(os.environ.get("DEVTOOL_FEDERATION_RELATIONSHIP") or "child").strip() or "child",
        "parent_run_id": parent_run_id,
        "parent_node_id": str(os.environ.get("DEVTOOL_FEDERATION_PARENT_NODE_ID") or "").strip(),
        "federation_workflow": str(os.environ.get("DEVTOOL_FEDERATION_WORKFLOW") or "").strip(),
        "federation_step": str(os.environ.get("DEVTOOL_FEDERATION_STEP") or "").strip(),
        "repository": str(os.environ.get("DEVTOOL_FEDERATION_REPOSITORY") or "").strip(),
        "source_repository": str(os.environ.get("DEVTOOL_FEDERATION_SOURCE_REPOSITORY") or "").strip(),
        "requested_repository": str(os.environ.get("DEVTOOL_FEDERATION_REQUESTED_REPOSITORY") or "").strip(),
        "effective_repository": str(os.environ.get("DEVTOOL_FEDERATION_EFFECTIVE_REPOSITORY") or "").strip(),
        "execution_environment": str(os.environ.get("DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT") or "").strip(),
        "child_run_id": str(os.environ.get("DEVTOOL_RUN_ID") or "").strip(),
    }
    return {key: value for key, value in values.items() if value not in (None, "")}


# Private historical spelling retained for internal/plugin compatibility.
_federation_lineage_from_environment = federation_lineage_from_environment


def invocation_metadata_for_args(args: Any) -> dict[str, object]:
    metadata = build_invocation_metadata(
        source=invocation_source_for_args(args),
        command=invocation_command_for_args(args),
        wrapper_schema=getattr(args, "_devtool_wrapper_schema", ""),
    )
    lineage = federation_lineage_from_environment()
    if lineage:
        metadata["run_lineage"] = lineage
    return metadata
