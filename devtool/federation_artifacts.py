from __future__ import annotations

"""FRX-Y08 immutable federation artifact producer -> consumer edges.

This module owns only federation artifact-edge semantics. Artifact production
remains owned by the child artifact registry/EXO, and remote physical transfer
remains owned by SSH-02. Consumers receive registry identity + digest and a
verified copy inside their own .devtool runtime area; producer filesystem paths
never become part of the public edge contract.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from .models import FederatedArtifactInput, FederatedWorkflowStep


class FederationArtifactError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_component(value: str, fallback: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    text = text.strip(".-")
    return text[:96] or fallback


def _registry_records(child: Any) -> list[dict[str, Any]]:
    ownership = getattr(child, "operational_ownership", {}) or {}
    refs = ownership.get("references", []) if isinstance(ownership, Mapping) else []
    records: list[dict[str, Any]] = []
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, Mapping) or str(ref.get("kind") or "") != "artifact-registry":
            continue
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), Mapping) else {}
        for row in metadata.get("records", []) if isinstance(metadata.get("records"), list) else []:
            if isinstance(row, Mapping):
                records.append(dict(row))
    return records


def _producer_root(child: Any) -> Path:
    ownership = getattr(child, "operational_ownership", {}) or {}
    root = str(ownership.get("root") or "") if isinstance(ownership, Mapping) else ""
    if not root:
        identity = getattr(child, "repository_identity", {}) or {}
        root = str(identity.get("root") or "") if isinstance(identity, Mapping) else ""
    if not root:
        raise FederationArtifactError(f"Producer step {getattr(child, 'step_id', '')!r} has no repository root evidence")
    return Path(root).expanduser().resolve(strict=False)


def _match_record(child: Any, spec: FederatedArtifactInput) -> dict[str, Any]:
    records = _registry_records(child)
    matches = [row for row in records if spec.selector in {str(row.get("id") or ""), str(row.get("name") or "")}]
    if not matches:
        raise FederationArtifactError(
            f"Artifact input {spec.name!r} could not resolve producer {spec.producer_step!r} "
            f"registry id/name {spec.selector!r}"
        )
    # Operational snapshots preserve newest-first registry order.
    return matches[0]


def _verify_record(child: Any, spec: FederatedArtifactInput, record: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    if str(getattr(child, "status", "") or "") != "succeeded" or int(getattr(child, "exit_code", 1) or 0) != 0:
        raise FederationArtifactError(f"Artifact producer step {spec.producer_step!r} did not succeed")
    registry_id = str(record.get("id") or "").strip()
    digest = str(record.get("sha256") or "").strip().lower()
    artifact_type = str(record.get("type") or "").strip()
    schema = str(record.get("schema") or "").strip()
    if not registry_id:
        raise FederationArtifactError(f"Artifact input {spec.name!r} has no immutable registry id")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise FederationArtifactError(f"Artifact input {spec.name!r} has no valid SHA-256 digest")
    if not artifact_type:
        raise FederationArtifactError(f"Artifact input {spec.name!r} has no artifact type")
    if not bool(record.get("valid", True)):
        raise FederationArtifactError(f"Artifact input {spec.name!r} registry record is marked invalid")
    if spec.artifact_type and spec.artifact_type != artifact_type:
        raise FederationArtifactError(
            f"Artifact input {spec.name!r} type mismatch: expected {spec.artifact_type!r}, got {artifact_type!r}"
        )
    if spec.schema and spec.schema != schema:
        raise FederationArtifactError(
            f"Artifact input {spec.name!r} schema mismatch: expected {spec.schema!r}, got {schema or '<missing>'!r}"
        )

    root = _producer_root(child)
    raw_path = str(record.get("path") or "").strip()
    if not raw_path:
        raise FederationArtifactError(f"Artifact input {spec.name!r} registry record has no availability path")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FederationArtifactError(f"Artifact input {spec.name!r} registry path escapes producer repository") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise FederationArtifactError(f"Artifact input {spec.name!r} is unavailable from the producer registry")
    actual = _sha256(candidate)
    if actual != digest:
        raise FederationArtifactError(
            f"Artifact input {spec.name!r} digest changed: expected {digest}, got {actual}"
        )

    remote = dict(record.get("remote_transfer") or {}) if isinstance(record.get("remote_transfer"), Mapping) else {}
    if remote:
        required = ("produced", "staged", "transferred", "verified", "restored_locally")
        missing = [name for name in required if not bool(remote.get(name))]
        if missing:
            raise FederationArtifactError(
                f"Artifact input {spec.name!r} remote return is not consumable; missing SSH-02 state: {', '.join(missing)}"
            )
    return candidate, {
        "registry_id": registry_id,
        "name": str(record.get("name") or candidate.name),
        "sha256": digest,
        "type": artifact_type,
        "schema": schema,
        "size": int(record.get("size") or candidate.stat().st_size),
        "remote_transfer": remote,
    }


def artifact_edges_identity(edges: Mapping[str, Any] | None) -> str:
    payload: list[dict[str, Any]] = []
    for name, row in sorted((edges or {}).items()):
        if not isinstance(row, Mapping):
            continue
        payload.append({
            "name": str(name),
            "producer_step": str(row.get("producer_step") or ""),
            "producer_run_id": str(row.get("producer_run_id") or ""),
            "registry_id": str(row.get("registry_id") or ""),
            "sha256": str(row.get("sha256") or ""),
            "type": str(row.get("type") or ""),
            "schema": str(row.get("schema") or ""),
        })
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_artifact_inputs(
    step: FederatedWorkflowStep,
    outcomes: Mapping[str, Any],
    *,
    consumer_root: str | Path,
    parent_run_id: str,
) -> dict[str, Any]:
    if not step.artifact_inputs:
        return {}
    root = Path(consumer_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise FederationArtifactError(f"Consumer repository root is unavailable: {root}")
    base = root / ".devtool" / "federation-inputs" / _safe_component(parent_run_id, "run") / _safe_component(step.step_id, "step")
    edges: dict[str, Any] = {}
    for spec in step.artifact_inputs:
        producer = outcomes.get(spec.producer_step)
        if producer is None:
            raise FederationArtifactError(
                f"Artifact input {spec.name!r} producer step {spec.producer_step!r} has no completed child result"
            )
        source, identity = _verify_record(producer, spec, _match_record(producer, spec))
        filename = _safe_component(str(identity.get("name") or source.name), "artifact")
        rel = Path(".devtool") / "federation-inputs" / _safe_component(parent_run_id, "run") / _safe_component(step.step_id, "step") / _safe_component(spec.name, "input") / filename
        destination = (root / rel).resolve(strict=False)
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise FederationArtifactError(f"Artifact input {spec.name!r} materialization path escaped consumer repository") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not destination.is_file() or destination.is_symlink():
                raise FederationArtifactError(
                    f"Artifact input {spec.name!r} materialization target is not a regular owned file: {destination}"
                )
            current_digest = _sha256(destination)
            if current_digest != identity["sha256"]:
                raise FederationArtifactError(
                    f"Artifact input {spec.name!r} materialization target drifted: "
                    f"expected {identity['sha256']}, got {current_digest}. "
                    "Refusing direct destructive replacement outside LIFE/provider reconciliation."
                )
        if not destination.exists():
            tmp = destination.with_name(destination.name + ".tmp")
            shutil.copy2(source, tmp)
            if _sha256(tmp) != identity["sha256"]:
                tmp.unlink(missing_ok=True)
                raise FederationArtifactError(f"Artifact input {spec.name!r} changed while materializing")
            os.replace(tmp, destination)
        edge = {
            "input": spec.name,
            "ref": spec.ref,
            "producer_step": spec.producer_step,
            "producer_repository": str(getattr(producer, "repository", "") or ""),
            "producer_run_id": str(getattr(producer, "run_id", "") or ""),
            "registry_id": identity["registry_id"],
            "sha256": identity["sha256"],
            "type": identity["type"],
            "schema": identity["schema"],
            "size": identity["size"],
            "materialized_path": rel.as_posix(),
            "remote_transfer": identity["remote_transfer"],
        }
        edge["edge_digest"] = artifact_edges_identity({spec.name: edge})
        edges[spec.name] = edge

    base.mkdir(parents=True, exist_ok=True)
    manifest_rel = Path(".devtool") / "federation-inputs" / _safe_component(parent_run_id, "run") / _safe_component(step.step_id, "step") / "manifest.json"
    manifest = root / manifest_rel
    payload = {
        "schema_version": 1,
        "kind": "devtool.federation.artifact-inputs",
        "consumer_step": step.step_id,
        "artifact_edges_identity": artifact_edges_identity(edges),
        "inputs": edges,
    }
    tmp_manifest = manifest.with_suffix(".json.tmp")
    tmp_manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, manifest)
    edges["__manifest__"] = manifest_rel.as_posix()
    return edges


def artifact_input_environment(edges: Mapping[str, Any]) -> dict[str, str]:
    if not edges:
        return {}
    manifest = str(edges.get("__manifest__") or "")
    public = {name: row for name, row in edges.items() if name != "__manifest__" and isinstance(row, Mapping)}
    payload = {
        name: {
            "producer_step": str(row.get("producer_step") or ""),
            "producer_run_id": str(row.get("producer_run_id") or ""),
            "registry_id": str(row.get("registry_id") or ""),
            "sha256": str(row.get("sha256") or ""),
            "type": str(row.get("type") or ""),
            "schema": str(row.get("schema") or ""),
            "path": str(row.get("materialized_path") or ""),
        }
        for name, row in sorted(public.items())
    }
    return {
        "DEVTOOL_FEDERATION_ARTIFACT_INPUTS": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        "DEVTOOL_FEDERATION_ARTIFACT_INPUT_MANIFEST": manifest,
        "DEVTOOL_FEDERATION_ARTIFACT_INPUTS_DIGEST": artifact_edges_identity(public),
    }


def checkpoint_artifact_reasons(
    inputs: Mapping[str, Any] | None,
    prior_rows: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for name, edge in sorted((inputs or {}).items()):
        if name == "__manifest__" or not isinstance(edge, Mapping):
            continue
        producer_step = str(edge.get("producer_step") or "")
        expected_id = str(edge.get("registry_id") or "")
        expected_sha = str(edge.get("sha256") or "").lower()
        producer = prior_rows.get(producer_step, {})
        ownership = producer.get("operational_ownership") if isinstance(producer, Mapping) else {}
        refs = ownership.get("references", []) if isinstance(ownership, Mapping) else []
        match: dict[str, Any] | None = None
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, Mapping) or str(ref.get("kind") or "") != "artifact-registry":
                continue
            metadata = ref.get("metadata") if isinstance(ref.get("metadata"), Mapping) else {}
            for row in metadata.get("records", []) if isinstance(metadata.get("records"), list) else []:
                if isinstance(row, Mapping) and str(row.get("id") or "") == expected_id:
                    match = dict(row)
                    break
            if match is not None:
                break
        if match is None:
            reasons.append(f"consumed artifact unavailable: {name}")
            continue
        current_sha = str(match.get("sha256") or "").lower()
        if current_sha != expected_sha:
            reasons.append(f"consumed artifact registry digest changed: {name}")
            continue
        root = str(ownership.get("root") or "") if isinstance(ownership, Mapping) else ""
        raw_path = str(match.get("path") or "")
        if not root or not raw_path:
            reasons.append(f"consumed artifact unavailable: {name}")
            continue
        base = Path(root).expanduser().resolve(strict=False)
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = base / path
        path = path.resolve(strict=False)
        try:
            path.relative_to(base)
        except ValueError:
            reasons.append(f"consumed artifact availability escaped producer: {name}")
            continue
        if not path.is_file() or path.is_symlink():
            reasons.append(f"consumed artifact unavailable: {name}")
            continue
        try:
            actual = _sha256(path)
        except OSError:
            reasons.append(f"consumed artifact unavailable: {name}")
            continue
        if actual != expected_sha:
            reasons.append(f"consumed artifact digest changed: {name}")
            continue
        remote = match.get("remote_transfer") if isinstance(match.get("remote_transfer"), Mapping) else {}
        if remote:
            if not all(bool(remote.get(key)) for key in ("produced", "staged", "transferred", "verified", "restored_locally")):
                reasons.append(f"consumed remote artifact transfer incomplete: {name}")
    return tuple(dict.fromkeys(reasons))


__all__ = [
    "FederationArtifactError",
    "artifact_edges_identity",
    "artifact_input_environment",
    "checkpoint_artifact_reasons",
    "resolve_artifact_inputs",
]
