from __future__ import annotations

"""GIT-FRX-18 federated provenance manifest and final campaign seal."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .git_audit import verify_zero_gap_audit_freshness
from .git_campaign import GitCampaignError, campaign_root, load_campaign
from .git_execution_notes import GitExecutionNoteError, read_execution_note
from .git_signing import GitSigningError, final_tag_argv, resolve_signing_decision, signing_mode_from_config, verify_commit_signature, verify_tag_signature
from .xdg import state_dir

SEAL_SCHEMA_VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class GitFinalSealError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe(value: str, fallback: str = "campaign") -> str:
    clean = _SAFE.sub("-", str(value or "").strip()).strip(".-")
    return clean or fallback


def _git(root: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], text=True, encoding="utf-8", errors="surrogateescape",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFinalSealError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(root: Path, *args: str) -> str:
    proc = _git(root, *args)
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitFinalSealError(detail)


def _lineage_records() -> list[dict[str, Any]]:
    root = state_dir() / "federation-git-lineage"
    result: list[dict[str, Any]] = []
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["_path"] = str(path)
            result.append(payload)
    return result


def _lineage_for(repository: str, result_commit: str) -> dict[str, Any]:
    rows = [
        row for row in _lineage_records()
        if str(row.get("repository") or "") == repository
        and str(row.get("result_commit") or "") == result_commit
        and str(row.get("state") or "") == "finalized"
    ]
    if not rows:
        return {}
    rows.sort(key=lambda row: (str(row.get("parent_run_id") or ""), str(row.get("child_run_id") or ""), str(row.get("_path") or "")))
    return rows[-1]


def _note_evidence(root: Path, commit: str) -> dict[str, Any]:
    try:
        note = read_execution_note(root, commit)
    except GitExecutionNoteError as exc:
        raise GitFinalSealError(f"Unable to read Devtool execution note for {commit}: {exc}") from exc
    rows = note.get("records") if isinstance(note.get("records"), list) else []
    ids: list[str] = []
    adoption: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for key in ("run_id", "validation_run_id", "parent_run_id", "child_run_id"):
            value = str(row.get(key) or "").strip()
            if value and value not in ids:
                ids.append(value)
        # GIT-FRX-11 notes are the existing cross-cutting execution evidence
        # authority.  FRX-Y12 projects the latest repository-local platform
        # contract from those notes into the final manifest instead of
        # inventing a second adoption store.
        adoption = {
            "frozen_lock_identity": str(row.get("frozen_lock_identity") or ""),
            "frozen_lock_equivalence_identity": str(row.get("frozen_lock_equivalence_identity") or ""),
            "frozen_repository_source_identity": str(row.get("frozen_repository_source_identity") or ""),
            "canonical_contract_identity": str(row.get("contract_ref") or ""),
            "capabilities": dict(row.get("capabilities") or {}) if isinstance(row.get("capabilities"), Mapping) else {},
            "interface_conformance": dict(row.get("interface") or {}) if isinstance(row.get("interface"), Mapping) else {},
            "scheduler_provider_placement": dict(row.get("scheduler") or {}) if isinstance(row.get("scheduler"), Mapping) else {},
            "resume_reuse_state": dict(row.get("resume_reuse") or {}) if isinstance(row.get("resume_reuse"), Mapping) else {},
            "artifact_inputs": list(row.get("artifact_inputs") or []) if isinstance(row.get("artifact_inputs"), list) else [],
            "artifact_outputs": list(row.get("artifact_outputs") or []) if isinstance(row.get("artifact_outputs"), list) else [],
        }
    return {"digest": _digest(note), "records": len(rows), "run_ids": ids, "adoption": adoption}


def _verify_hashed_record(payload: Mapping[str, Any], digest_key: str) -> bool:
    expected = str(payload.get(digest_key) or "")
    material = {key: value for key, value in payload.items() if key not in {digest_key, "path"}}
    return bool(expected) and expected == _digest(material)


def _verify_audit_evidence(audit_pass: Mapping[str, Any]) -> None:
    if not _verify_hashed_record(audit_pass, "audit_digest"):
        raise GitFinalSealError("Final zero-gap audit record digest is invalid")
    for row in audit_pass.get("evidence", []) if isinstance(audit_pass.get("evidence"), list) else []:
        if not isinstance(row, Mapping):
            continue
        path = Path(str(row.get("path") or "")).expanduser().resolve(strict=False)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise GitFinalSealError(f"Final zero-gap audit evidence is unavailable: {path}: {exc}") from exc
        if hashlib.sha256(data).hexdigest() != str(row.get("sha256") or "") or len(data) != int(row.get("size", -1)):
            raise GitFinalSealError(f"Final zero-gap audit evidence changed after audit: {path}")


def _repo_manifest_row(config: Any, *, campaign: Mapping[str, Any], start: Mapping[str, Any], result: Mapping[str, Any], signing_mode: str) -> dict[str, Any]:
    repository = str(result.get("repository") or start.get("repository") or "")
    root = Path(str(result.get("root") or start.get("root") or "")).resolve()
    start_commit = str(start.get("commit") or "")
    result_commit = str(result.get("commit") or "")
    if not repository or not result_commit:
        raise GitFinalSealError("Campaign generation is missing repository/result commit identity")
    current = _require(root, "rev-parse", "--verify", "HEAD")
    if current != result_commit:
        raise GitFinalSealError(f"Campaign repository {repository!r} drifted after its final generation: {current} != {result_commit}")
    tree = _require(root, "rev-parse", f"{result_commit}^{{tree}}")
    lineage = _lineage_for(repository, result_commit)
    if result_commit != start_commit and not lineage:
        raise GitFinalSealError(f"Campaign repository {repository!r} changed without finalized Devtool Git lineage evidence")
    if lineage:
        result_ref = str(lineage.get("result_ref") or "")
        rollback_ref = str(lineage.get("rollback_ref") or "")
        if result_ref and _require(root, "rev-parse", "--verify", result_ref) != result_commit:
            raise GitFinalSealError(f"Campaign repository {repository!r} result ref does not match final commit")
        if rollback_ref and start_commit and _require(root, "rev-parse", "--verify", rollback_ref) != start_commit:
            raise GitFinalSealError(f"Campaign repository {repository!r} rollback ref does not match start commit")
        provider = str(lineage.get("execution_provider") or "")
        attestation = lineage.get("remote_attestation") if isinstance(lineage.get("remote_attestation"), Mapping) else {}
        transport_digest = str(lineage.get("source_transport_evidence_digest") or "")
        if provider.startswith("ssh") and not attestation and not transport_digest:
            raise GitFinalSealError(f"Remote campaign repository {repository!r} lacks remote attestation/source transport evidence")
    note = _note_evidence(root, result_commit)
    try:
        signing = resolve_signing_decision(root, signing_mode)
    except GitSigningError as exc:
        raise GitFinalSealError(f"Signing policy failed for repository {repository!r}: {exc}") from exc
    verification = verify_commit_signature(root, result_commit)
    if signing.enabled and not bool(verification.get("verified")):
        raise GitFinalSealError(f"Required/configured SSH signature did not verify for {repository!r} commit {result_commit}")
    contract = campaign.get("contract_identity") if isinstance(campaign.get("contract_identity"), Mapping) else {}
    validation_ids = list(note.get("run_ids") or [])
    child_run = str(lineage.get("child_run_id") or "")
    if child_run and child_run not in validation_ids:
        validation_ids.append(child_run)
    return {
        "repo_id": str(result.get("repository_id") or start.get("repository_id") or ""),
        "repository": repository,
        "start_commit": start_commit,
        "result_commit": result_commit,
        "branch": str(result.get("branch") or result.get("head_ref") or ""),
        "integration_target": str(contract.get("integration_policy") or "per-repo"),
        "overlay_digests": list(lineage.get("overlay_digests") or []),
        "commit_policy": str(lineage.get("commit_mode") or "none"),
        "amended_superseded_commits": list(lineage.get("supersession_pairs") or []),
        "validation_run_ids": validation_ids,
        "remote_provider": str(lineage.get("execution_provider") or "local"),
        "remote_attestation": dict(lineage.get("remote_attestation") or {}) if isinstance(lineage.get("remote_attestation"), Mapping) else {},
        "source_transport_evidence_digest": str(lineage.get("source_transport_evidence_digest") or ""),
        "frozen_lock_identity": str((note.get("adoption") or {}).get("frozen_lock_identity") or lineage.get("frozen_plan_identity") or ""),
        "frozen_lock_equivalence_identity": str((note.get("adoption") or {}).get("frozen_lock_equivalence_identity") or lineage.get("frozen_plan_equivalence_identity") or ""),
        "frozen_repository_source_identity": str((note.get("adoption") or {}).get("frozen_repository_source_identity") or lineage.get("frozen_repository_source_identity") or ""),
        "canonical_contract_identity": str((note.get("adoption") or {}).get("canonical_contract_identity") or lineage.get("contract_ref") or ""),
        "capabilities": dict((note.get("adoption") or {}).get("capabilities") or {}),
        "interface_conformance": dict((note.get("adoption") or {}).get("interface_conformance") or {}),
        "scheduler_provider_placement": dict((note.get("adoption") or {}).get("scheduler_provider_placement") or {}),
        "resume_reuse_state": dict((note.get("adoption") or {}).get("resume_reuse_state") or {}),
        "artifact_producer_consumer_digests": {
            "inputs": list((note.get("adoption") or {}).get("artifact_inputs") or []),
            "outputs": list((note.get("adoption") or {}).get("artifact_outputs") or []),
        },
        "notes_evidence_digest": str(note.get("digest") or ""),
        "notes_records": int(note.get("records") or 0),
        "signing_policy": signing.as_dict(),
        "signing_verification": verification,
        "rollback_ref": str(lineage.get("rollback_ref") or ""),
        "final_tree": tree,
    }


def seal_path(campaign_id: str, generation: int) -> Path:
    return campaign_root(campaign_id) / "seals" / f"generation-{int(generation)}.json"


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def build_campaign_seal(config: Any, campaign_id: str, *, signing_mode: str = "", create_tag: bool = False) -> dict[str, Any]:
    try:
        campaign = load_campaign(campaign_id)
    except GitCampaignError as exc:
        raise GitFinalSealError(str(exc)) from exc
    if not _verify_hashed_record(campaign, "campaign_digest"):
        raise GitFinalSealError("Git campaign state digest is invalid")
    generations = campaign.get("generations") if isinstance(campaign.get("generations"), list) else []
    if not generations:
        raise GitFinalSealError("Campaign has no generation state")
    first = generations[0] if isinstance(generations[0], Mapping) else {}
    latest = generations[-1] if isinstance(generations[-1], Mapping) else {}
    generation = int(latest.get("generation", len(generations) - 1))
    audit = verify_zero_gap_audit_freshness(config, campaign_id)
    if not bool(audit.get("zero_gap_fresh")):
        raise GitFinalSealError("Final seal requires a fresh zero-gap audit across every participating repository")
    zero = audit.get("latest_zero_gap_pass") if isinstance(audit.get("latest_zero_gap_pass"), Mapping) else {}
    _verify_audit_evidence(zero)
    roadmap = str(zero.get("inspected_roadmap_range") or "")
    if "GIT-FRX-18" not in roadmap and not roadmap.endswith("..18"):
        raise GitFinalSealError("Final zero-gap audit must cover GIT-FRX-01..18")
    selected_signing = signing_mode_from_config(config, signing_mode)
    start_by = {str(row.get("repository")): row for row in first.get("repositories", []) if isinstance(row, Mapping)}
    result_by = {str(row.get("repository")): row for row in latest.get("repositories", []) if isinstance(row, Mapping)}
    if set(start_by) != set(result_by):
        raise GitFinalSealError("Campaign repository membership changed between initial and final generations")
    repositories = [
        _repo_manifest_row(config, campaign=campaign, start=start_by[name], result=result_by[name], signing_mode=selected_signing)
        for name in sorted(result_by)
    ]
    manifest = {
        "schema_version": SEAL_SCHEMA_VERSION,
        "kind": "devtool.git-federation-provenance-manifest",
        "campaign_id": str(campaign.get("campaign_id") or campaign_id),
        "generation": generation,
        "subject_kind": str(campaign.get("subject_kind") or ""),
        "workflow": str(campaign.get("workflow") or ""),
        "group": str(campaign.get("group") or ""),
        "contract_digest": str((campaign.get("contract_identity") or {}).get("digest") or "") if isinstance(campaign.get("contract_identity"), Mapping) else "",
        "campaign_digest": str(campaign.get("campaign_digest") or ""),
        "zero_gap_audit_pass_id": str(zero.get("audit_pass_id") or ""),
        "zero_gap_audit_digest": str(zero.get("audit_digest") or ""),
        "zero_gap_evidence_digest": str(zero.get("evidence_set_digest") or ""),
        "inspected_roadmap_range": roadmap,
        "signing_policy": selected_signing,
        "repositories": repositories,
        "automatic_push": False,
    }
    manifest_digest = _digest(manifest)
    payload: dict[str, Any] = {
        "schema_version": SEAL_SCHEMA_VERSION,
        "kind": "devtool.git-federation-final-seal",
        "campaign_id": manifest["campaign_id"],
        "generation": generation,
        "manifest": manifest,
        "manifest_sha256": manifest_digest,
        "control_repository": str(Path(config.repo_root).resolve()),
        "tag": {},
        "status": "ok",
    }
    # Re-read freshness immediately before persistence: no participating source
    # may change between the zero-gap audit check and packaging the seal.
    final_freshness = verify_zero_gap_audit_freshness(config, campaign_id)
    if not bool(final_freshness.get("zero_gap_fresh")) or str(final_freshness.get("status_digest") or "") != str(audit.get("status_digest") or ""):
        raise GitFinalSealError("Participating repository changed while final seal was being packaged")
    if create_tag:
        root = Path(config.repo_root).resolve()
        try:
            tag_signing = resolve_signing_decision(root, selected_signing)
        except GitSigningError as exc:
            raise GitFinalSealError(str(exc)) from exc
        tag_name = f"devtool/{_safe(str(manifest['campaign_id']))}/{generation}"
        target = _require(root, "rev-parse", "--verify", "HEAD")
        existing = _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag_name}")
        message = f"Devtool campaign seal {manifest['campaign_id']} generation {generation}\nmanifest-sha256: {manifest_digest}"
        if existing.returncode == 0:
            tagged = _require(root, "rev-list", "-n", "1", tag_name)
            tag_object = _require(root, "cat-file", "-p", tag_name)
            if tagged != target or manifest_digest not in tag_object:
                raise GitFinalSealError(f"Final seal tag collision: {tag_name} does not reference the current target/manifest")
        else:
            argv = final_tag_argv(tag_signing, tag_name, target, message)
            proc = _git(root, *argv)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "git tag failed").strip()
                raise GitFinalSealError(f"Unable to create final campaign tag: {detail}")
        tag_verification = verify_tag_signature(root, tag_name)
        if tag_signing.enabled and not bool(tag_verification.get("verified")):
            raise GitFinalSealError(f"Final campaign tag signature did not verify: {tag_name}")
        payload["tag"] = {"name": tag_name, "target": target, "signing_policy": tag_signing.as_dict(), "verification": tag_verification}
    path = seal_path(campaign_id, generation)
    _atomic_write(path, payload)
    payload["path"] = str(path)
    return payload


def verify_campaign_seal(config: Any, campaign_id: str, *, generation: int | None = None) -> dict[str, Any]:
    campaign = load_campaign(campaign_id)
    campaign_digest_ok = _verify_hashed_record(campaign, "campaign_digest")
    generations = campaign.get("generations") if isinstance(campaign.get("generations"), list) else []
    if not generations:
        raise GitFinalSealError("Campaign has no generations")
    latest = generations[-1] if isinstance(generations[-1], Mapping) else {}
    wanted = int(latest.get("generation", len(generations) - 1) if generation is None else generation)
    path = seal_path(campaign_id, wanted)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitFinalSealError(f"Unable to read final campaign seal: {exc}") from exc
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), Mapping) else {}
    digest_ok = str(payload.get("manifest_sha256") or "") == _digest(manifest)
    campaign_binding_ok = str(manifest.get("campaign_digest") or "") == str(campaign.get("campaign_digest") or "")
    freshness = verify_zero_gap_audit_freshness(config, campaign_id)
    rows_ok = True
    drift: list[dict[str, str]] = []
    for row in manifest.get("repositories", []) if isinstance(manifest.get("repositories"), list) else []:
        if not isinstance(row, Mapping):
            continue
        root = None
        for gen_row in latest.get("repositories", []) if isinstance(latest.get("repositories"), list) else []:
            if isinstance(gen_row, Mapping) and str(gen_row.get("repository")) == str(row.get("repository")):
                root = Path(str(gen_row.get("root") or "")).resolve(); break
        if root is None:
            rows_ok = False; drift.append({"repository": str(row.get("repository") or ""), "reason": "missing-final-generation"}); continue
        current = _require(root, "rev-parse", "--verify", "HEAD")
        tree = _require(root, "rev-parse", f"{current}^{{tree}}")
        if current != str(row.get("result_commit") or "") or tree != str(row.get("final_tree") or ""):
            rows_ok = False; drift.append({"repository": str(row.get("repository") or ""), "reason": "source-drift"}); continue
        try:
            note = _note_evidence(root, current)
        except GitFinalSealError:
            rows_ok = False; drift.append({"repository": str(row.get("repository") or ""), "reason": "notes-unavailable"}); continue
        if str(row.get("notes_evidence_digest") or "") != str(note.get("digest") or ""):
            rows_ok = False; drift.append({"repository": str(row.get("repository") or ""), "reason": "notes-drift"}); continue
        signing_policy = row.get("signing_policy") if isinstance(row.get("signing_policy"), Mapping) else {}
        if bool(signing_policy.get("enabled")):
            signature = verify_commit_signature(root, current)
            if not bool(signature.get("verified")):
                rows_ok = False; drift.append({"repository": str(row.get("repository") or ""), "reason": "signature-invalid"})
    tag_ok = True
    tag_detail: dict[str, Any] = {}
    tag = payload.get("tag") if isinstance(payload.get("tag"), Mapping) else {}
    tag_name = str(tag.get("name") or "")
    if tag_name:
        control = Path(str(payload.get("control_repository") or config.repo_root)).resolve()
        expected_target = str(tag.get("target") or "")
        ref = _git(control, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag_name}")
        if ref.returncode != 0:
            tag_ok = False
            tag_detail = {"name": tag_name, "reason": "missing-tag"}
        else:
            tagged = _require(control, "rev-list", "-n", "1", tag_name)
            tag_object = _require(control, "cat-file", "-p", tag_name)
            tag_ok = tagged == expected_target and str(payload.get("manifest_sha256") or "") in tag_object
            tag_detail = {"name": tag_name, "target": tagged, "manifest_bound": str(payload.get("manifest_sha256") or "") in tag_object}
            policy = tag.get("signing_policy") if isinstance(tag.get("signing_policy"), Mapping) else {}
            if bool(policy.get("enabled")):
                signature = verify_tag_signature(control, tag_name)
                tag_detail["signature"] = signature
                tag_ok = tag_ok and bool(signature.get("verified"))
    ok = campaign_digest_ok and campaign_binding_ok and digest_ok and rows_ok and tag_ok and bool(freshness.get("zero_gap_fresh"))
    return {
        "schema_version": SEAL_SCHEMA_VERSION,
        "kind": "devtool.git-federation-final-seal-verification",
        "campaign_id": campaign_id,
        "generation": wanted,
        "ok": ok,
        "campaign_digest_valid": campaign_digest_ok,
        "campaign_binding_valid": campaign_binding_ok,
        "manifest_digest_valid": digest_ok,
        "zero_gap_fresh": bool(freshness.get("zero_gap_fresh")),
        "repositories_valid": rows_ok,
        "tag_valid": tag_ok,
        "tag": tag_detail,
        "drift": drift,
        "seal_path": str(path),
        "manifest_sha256": str(payload.get("manifest_sha256") or ""),
    }


__all__ = [
    "SEAL_SCHEMA_VERSION", "GitFinalSealError", "build_campaign_seal", "seal_path", "verify_campaign_seal",
]
